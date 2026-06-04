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
    probe_temperature_2: float | None = None    # FM22xx series: second probe channel
    probe_battery_2: float | None = None        # FM22xx series: second probe battery
    ambient_temperature: float | None = None    # FM22xx series: ambient at probe 1 (炉温 ch1)
    ambient_temperature_2: float | None = None  # FM22xx series: ambient at probe 2 (炉温 ch2)
    target_1: float | None = None  # FM22xx: CH1 alarm target (None = not set)
    target_2: float | None = None  # FM22xx: CH2 alarm target (None = not set)


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

    def __init__(self, is_fm22: bool = False) -> None:
        self.state: ProbePlusData = ProbePlusData()
        self._is_fm22: bool = is_fm22  # set from model at init; also detected at runtime

    def parse_data(self, data: bytearray):
        """Handle data notification updates from the device."""
        _LOGGER.debug(">> Received data notification: %s", data.hex())

        if len(data) == 9 and data[0] == 0x00 and data[1] == 0x00:
            # Probe state frame
            channel = data[2]
            probe_voltage = data[3] * PROBE_VOLTAGE_FACTOR

            if probe_voltage >= 2.0:
                probe_battery = 100
            elif probe_voltage >= 1.7:
                probe_battery = 51
            elif probe_voltage >= 1.5:
                probe_battery = 26
            else:
                probe_battery = 20

            self.state.probe_voltage = probe_voltage
            temp_bytes = bytearray(data[4:6])

            if channel == 0:
                # FMC series: single channel 0, original formula
                self._is_fm22 = False
                self.state.probe_battery = probe_battery
                self.state.probe_temperature = _parse_temperature_fmc(temp_bytes)
            elif channel == 1:
                # FM22xx: channel 1 -> probe_temperature + probe_battery + ambient 1
                self._is_fm22 = True
                self.state.probe_battery = probe_battery
                self.state.probe_temperature = _parse_temperature_fm22(temp_bytes)
                self.state.ambient_temperature = _parse_temperature_fm22(data[6:8])
            else:
                # FM22xx: channel 2+ -> probe_temperature_2 + probe_battery_2 + ambient 2
                self._is_fm22 = True
                self.state.probe_battery_2 = probe_battery
                self.state.probe_temperature_2 = _parse_temperature_fm22(temp_bytes)
                self.state.ambient_temperature_2 = _parse_temperature_fm22(data[6:8])

            # RSSI is a signed dBm value encoded as a single byte
            self.state.probe_rssi = int.from_bytes([data[8]], signed=True)
            _LOGGER.debug(
                ">> Parsed temperature (ch%d): %s",
                channel,
                self.state.probe_temperature if channel <= 1 else self.state.probe_temperature_2,
            )
            return self.state

        elif len(data) == 41 and data[0] == 0x00 and data[1] == 0x05:
            # FM22xx STATUS frame — contains current alarm targets at fixed offsets
            self._is_fm22 = True
            ch1_raw = int.from_bytes(data[11:13], 'little')
            ch2_raw = int.from_bytes(data[20:22], 'little')
            self.state.target_1 = None if ch1_raw == 0xFFFF else ch1_raw / FM22_TEMP_DIVISOR
            self.state.target_2 = None if ch2_raw == 0xFFFF else ch2_raw / FM22_TEMP_DIVISOR
            return self.state

        elif len(data) >= 7 and data[0] == 0x00 and data[1] == 0x03:
            # FM22xx TARGET notification — fired when target changes (set/cleared)
            self._is_fm22 = True
            ch1_raw = int.from_bytes(data[3:5], 'little')
            ch2_raw = int.from_bytes(data[5:7], 'little')
            self.state.target_1 = None if ch1_raw == 0xFFFF else ch1_raw / FM22_TEMP_DIVISOR
            self.state.target_2 = None if ch2_raw == 0xFFFF else ch2_raw / FM22_TEMP_DIVISOR
            return self.state

        elif len(data) == 8 and data[0] == 0x00 and data[1] == 0x01:
            # Relay/station state frame — FM22xx: little-endian, FMC: big-endian
            endian = 'little' if self._is_fm22 else 'big'
            self.state.relay_voltage = int.from_bytes(data[2:4], endian) / RELAY_VOLTAGE_DIVISOR
            _LOGGER.debug(">> Relay voltage: %sV", self.state.relay_voltage)
            if self._is_fm22:
                # FM22xx thresholds from OEM app (mV: >=3900/3700/3460)
                if self.state.relay_voltage >= 3.9:
                    self.state.relay_battery = 100
                elif self.state.relay_voltage >= 3.7:
                    self.state.relay_battery = 74
                elif self.state.relay_voltage >= 3.46:
                    self.state.relay_battery = 49
                else:
                    self.state.relay_battery = 0
            else:
                # FMC series thresholds (original)
                if self.state.relay_voltage > 3.87:
                    self.state.relay_battery = 100
                elif self.state.relay_voltage >= 3.7:
                    self.state.relay_battery = 74
                elif self.state.relay_voltage >= 3.6:
                    self.state.relay_battery = 49
                else:
                    self.state.relay_battery = 0

            self.state.relay_status = int(data[4])
            _LOGGER.debug(">> Relay state %s", self.state.relay_status)
            return self.state

        return self.state
