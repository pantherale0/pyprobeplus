"""Parser for FM standard devices (advertised name has no '+')."""

import logging
import struct

from .base import ParserBase, ProbeReading
from .const import (
    RELAY_VOLTAGE_DIVISOR,
    STD_PROBE_VOLTAGE_FACTOR,
    STD_TEMP_FACTOR,
    STD_TEMP_OFFSET,
)

_LOGGER = logging.getLogger(__name__)


class FMStandardParser(ParserBase):
    """Parser for the OEM old probe agreement (e.g. FM210, FM210_coded)."""

    RELAY_BATTERY_THRESHOLDS = (3.87, 3.7, 3.6)
    MODEL = ""

    def _parse_temperature(self, raw: bytearray) -> float:
        """Parse a 2-byte temperature reading.

        Byte-swap then big-endian unpack, scaled by 0.0625 with a -50.0625
        offset (OEM GATT path when the device name does not contain '+').
        """
        temp_val = struct.unpack(">H", bytes(raw[::-1]))[0]
        return (temp_val * STD_TEMP_FACTOR) - STD_TEMP_OFFSET

    def _parse_relay_voltage(self, raw: bytearray) -> float:
        """Parse the relay/station voltage (little-endian millivolts)."""
        return int.from_bytes(raw[2:4], "little") / RELAY_VOLTAGE_DIVISOR

    def _parse_probe_frame(self, data: bytearray) -> None:
        """Parse a 9-byte probe state frame."""
        channel = data[2]
        slot = self._channel_to_slot(channel)
        while len(self.state.probes) <= slot:
            self.state.probes.append(ProbeReading(channel=len(self.state.probes)))
        probe = self.state.probes[slot]
        probe.channel = channel
        probe.voltage = data[3] * STD_PROBE_VOLTAGE_FACTOR
        probe.temperature = self._parse_temperature(bytearray(data[4:6]))
        probe.rssi = int.from_bytes(data[8:9], "big", signed=True)
        _LOGGER.debug(">> Parsed temperature (ch%d): %s", channel, probe.temperature)

    def _parse_relay_frame(self, data: bytearray) -> None:
        """Parse 8-byte relay/station state frame."""
        self.state.relay_voltage = self._parse_relay_voltage(data)
        self.state.relay_status = int(data[4])
        _LOGGER.debug(">> Relay voltage: %sV", self.state.relay_voltage)
        _LOGGER.debug(">> Relay state %s", self.state.relay_status)

    def _parse_other_frame(self, data: bytearray) -> None:
        """Handle other data frames (not used on standard devices)."""
        return
