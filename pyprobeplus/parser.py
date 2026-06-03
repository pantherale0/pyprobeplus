"""Device BLE Parser."""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

# Constants for FMC series (single-channel, original formula)
PROBE_VOLTAGE_FACTOR = 0.03125
TEMP_FACTOR = 0.0625
TEMP_OFFSET = 50.0625
RELAY_VOLTAGE_DIVISOR = 1000.0

# Constants for FM22xx series (FM2201+, multi-channel, 0.1°C resolution)
FM22_TEMP_DIVISOR = 10.0


@dataclass
class ProbePlusData:
    """Represents data from a Probe Plus device."""
    relay_battery: float | None = None
    relay_voltage: float | None = None
    relay_status: int | None = None
    probe_battery: float | None = None
    probe_voltage: float | None = None
    probe_temperature: float | None = None
    probe_rssi: float | None = None
    probe_temperature_2: float | None = None  # FM22xx series: second probe channel


def _parse_temperature_fmc(temp_bytes: bytearray) -> float:
    """Parse temperature for FMC series (channel 0).

    Original formula: byte-swap then big-endian unpack, scaled by 0.0625 with -50.0625 offset.
    """
    temp_val = struct.unpack(">H", temp_bytes[::-1])[0]
    return (temp_val * TEMP_FACTOR) - TEMP_OFFSET


def _parse_temperature_fm22(temp_bytes: bytearray) -> float:
    """Parse temperature for FM22xx series (FM2201+).

    Format: little-endian uint16 in units of 0.1°C.
    Verified against FM2201+ hardware at multiple temperatures.
    """
    return int.from_bytes(temp_bytes, 'little', signed=True) / FM22_TEMP_DIVISOR


class ParserBase:
    """Parser for Probe Plus BLE notifications.

    Auto-detects device family from frame content:
    - data[2] == 0x00: FMC series (FMC210, FMC213) — original single-channel formula
    - data[2] != 0x00: FM22xx series (FM2201+) — multi-channel, 0.1 deg C resolution
    """

    def __init__(self) -> None:
        self.state: ProbePlusData = ProbePlusData()

    def parse_data(self, data: bytearray):
        """Handle data notification updates from the device."""
        _LOGGER.debug(">> Received data notification: %s", data.hex())

        if len(data) == 9 and data[0] == 0x00 and data[1] == 0x00:
            # Probe state frame
            channel = data[2]
            probe_voltage = data[3] * PROBE_VOLTAGE_FACTOR

            if probe_voltage >= 2.0:
                self.state.probe_battery = 100
            elif probe_voltage >= 1.7:
                self.state.probe_battery = 51
            elif probe_voltage >= 1.5:
                self.state.probe_battery = 26
            else:
                self.state.probe_battery = 20

            self.state.probe_voltage = probe_voltage
            temp_bytes = bytearray(data[4:6])

            if channel == 0:
                # FMC series: single channel 0, original formula
                self.state.probe_temperature = _parse_temperature_fmc(temp_bytes)
            elif channel == 1:
                # FM22xx: channel 1 -> probe_temperature
                self.state.probe_temperature = _parse_temperature_fm22(temp_bytes)
            else:
                # FM22xx: channel 2+ -> probe_temperature_2
                self.state.probe_temperature_2 = _parse_temperature_fm22(temp_bytes)

            self.state.probe_rssi = data[8]
            _LOGGER.debug(
                ">> Parsed temperature (ch%d): %s",
                channel,
                self.state.probe_temperature if channel <= 1 else self.state.probe_temperature_2,
            )
            return self.state

        elif len(data) == 8 and data[0] == 0x00 and data[1] == 0x01:
            # Relay state frame (same for all device families)
            voltage_bytes = data[2:4]
            self.state.relay_voltage = struct.unpack(">H", voltage_bytes)[0] / RELAY_VOLTAGE_DIVISOR
            _LOGGER.debug(">> Relay voltage: %sV", self.state.relay_voltage)
            if self.state.relay_voltage > 3.87:
                self.state.relay_battery = 100
            elif self.state.relay_voltage >= 3.7:
                self.state.relay_battery = 74
            elif self.state.relay_voltage >= 3.6:
                self.state.relay_battery = 49
            else:
                self.state.relay_battery = 0

            if len(data) > 4:
                self.state.relay_status = int(data[4])
            else:
                self.state.relay_status = None
            _LOGGER.debug(">> Relay state %s", self.state.relay_status)
            return self.state

        return self.state
