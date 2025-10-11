"""Device BLE Parser."""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

# Constants for parsing
PROBE_VOLTAGE_FACTOR = 0.03125
TEMP_FACTOR = 0.0625
TEMP_OFFSET = 50.0625

RELAY_VOLTAGE_DIVISOR = 1000.0


@dataclass
class ProbePlusData:
    """Represents data from PP."""
    relay_battery: float | None = None
    relay_voltage: float | None = None
    relay_status: int | None = None
    probe_battery: float | None = None
    probe_voltage: float | None = None
    probe_temperature: float | None = None
    probe_rssi: float | None = None


def _parse_temperature(temp_bytes: bytearray) -> float:
    """Parse temperature from 2 bytes (little-endian)."""
    # The device sends temperature as little-endian, but struct wants big-endian for ">H"
    # temp_bytes[::-1] will do the byte swapping for us.
    temp_val = struct.unpack(">H", temp_bytes[::-1])[0]
    return (temp_val * TEMP_FACTOR) - TEMP_OFFSET

class ParserBase:
    """ParserBase"""

    state: ProbePlusData = ProbePlusData()

    def parse_data(self, data: bytearray):
        """Handle data notification updates from the device."""
        probe_channels = [0]  # Hardcoded probe channels

        _LOGGER.debug(">> Received data notification: %s", data.hex())

        if len(data) == 9 and data[0] == 0x00 and data[1] == 0x00:
            # probe state
            probe_voltage = data[3] * PROBE_VOLTAGE_FACTOR
            if probe_voltage >= 2.0:
                self.state.probe_battery = 100
            elif probe_voltage >= 1.7:
                self.state.probe_battery = 51
            elif probe_voltage >= 1.5:
                self.state.probe_battery = 26
            else:
                self.state.probe_battery = 20

            temp_bytes = data[4:6]
            self.state.probe_temperature = _parse_temperature(bytearray(temp_bytes))
            _LOGGER.debug(">> Parsed temperature: %s", self.state.probe_temperature)

            self.state.probe_rssi = data[8]
            return self.state

        elif len(data) == 8 and data[0] == 0x00 and data[1] == 0x01:
            # relay state
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

            for channel in probe_channels:
                if len(data) > 4: # check to avoid index out of range errors
                    status_byte = data[4] # Directly access the 5th byte (index 4)
                    self.state.relay_status = int(status_byte)
                    break
                self.state.relay_status = None
            _LOGGER.debug(">> Relay state %s", self.state.relay_status)
            return self.state

        return self.state
