"""Device BLE Parser."""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

# Constants for parsing
PROBE_VOLTAGE_FACTOR = 0.03125
TEMP_FACTOR = 0.0625
TEMP_OFFSET = 50.0625

RELAY_VOLTAGE_DIVISOR = 1000.0

# FM2201+ family encodes temperatures as little-endian uint16 in tenths of degC,
# and the base-station voltage as little-endian millivolts.
FM2201_TEMP_DIVISOR = 10.0
FM2201_BASE_VOLTAGE_DIVISOR = 1000.0


@dataclass
class ProbeReading:
    """A single physical probe's readings (FM2201+ family)."""

    channel: int
    temperature: float | None = None
    ambient_temperature: float | None = None
    target_temperature: float | None = None
    battery: int | None = None
    rssi: float | None = None


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
    # FM2201+ family: one entry per physical probe. Empty for other models.
    probes: list[ProbeReading] = field(default_factory=list)


def _parse_temperature(temp_bytes: bytearray) -> float:
    """Parse temperature from 2 bytes (little-endian)."""
    # The device sends temperature as little-endian, but struct wants big-endian for ">H"
    # temp_bytes[::-1] will do the byte swapping for us.
    temp_val = struct.unpack(">H", temp_bytes[::-1])[0]
    return (temp_val * TEMP_FACTOR) - TEMP_OFFSET


def _u16le(data: bytes) -> int:
    """Read an unsigned little-endian 16-bit value."""
    return struct.unpack("<H", data)[0]


def _signed_rssi(value: int) -> int:
    """Interpret a byte as a signed RSSI value."""
    return value - 256 if value > 127 else value


class ParserBase:
    """ParserBase"""

    state: ProbePlusData = ProbePlusData()

    def __init__(self, name: str | None = None) -> None:
        """Create a parser. ``name`` is the advertised BLE device name, used to
        select the wire format (FM2201+ family vs. the original FMC family)."""
        # Fresh state per instance (the class-level default is shared otherwise).
        self.state = ProbePlusData()
        self.name = name or ""

    @property
    def _is_fm2201_family(self) -> bool:
        """FM2201+ family advertises names starting with 'FM2' but not 'FMC'."""
        upper = self.name.upper()
        return upper.startswith("FM2") and not upper.startswith("FMC")

    def _probe(self, channel: int) -> ProbeReading:
        """Return the ProbeReading for a channel, creating it if needed."""
        for probe in self.state.probes:
            if probe.channel == channel:
                return probe
        reading = ProbeReading(channel=channel)
        self.state.probes.append(reading)
        return reading

    def parse_data(self, data: bytearray):
        """Handle data notification updates from the device."""
        _LOGGER.debug(">> Received data notification: %s", data.hex())

        if self._is_fm2201_family:
            return self._parse_fm2201(data)

        return self._parse_legacy(data)

    # ------------------------------------------------------------------ FM2201+
    def _parse_fm2201(self, data: bytearray):
        """Parse the FM2201+ family wire format.

        Packet types (all delivered on 0xFF01):
        * 9 bytes  ``00 00 ch batt t_lo t_hi a_lo a_hi rssi`` - live probe update
          (probe temperature, ambient temperature, battery, RSSI).
        * 8 bytes  ``00 01 v_lo v_hi .. .. ff ff`` - base-station status
          (base voltage / battery).
        * 41 bytes ``00 05 ..`` - full snapshot sent once on connect; carries
          each probe's temperature, ambient AND target temperature.
        """
        n = len(data)

        if n == 9 and data[0] == 0x00 and data[1] == 0x00:
            channel = data[2]
            probe = self._probe(channel)
            probe.battery = data[3]
            probe.temperature = _u16le(data[4:6]) / FM2201_TEMP_DIVISOR
            probe.ambient_temperature = _u16le(data[6:8]) / FM2201_TEMP_DIVISOR
            probe.rssi = _signed_rssi(data[8])

            # Backward-compat: mirror the first probe into the flat fields.
            if channel == 1 or not self.state.probes:
                self.state.probe_temperature = probe.temperature
                self.state.probe_battery = probe.battery
                self.state.probe_rssi = probe.rssi
            _LOGGER.debug(
                ">> FM2201+ probe ch=%s temp=%s ambient=%s batt=%s rssi=%s",
                channel, probe.temperature, probe.ambient_temperature,
                probe.battery, probe.rssi,
            )
            return self.state

        if n == 8 and data[0] == 0x00 and data[1] == 0x01:
            voltage = _u16le(data[2:4]) / FM2201_BASE_VOLTAGE_DIVISOR
            self.state.relay_voltage = voltage
            if voltage > 3.87:
                self.state.relay_battery = 100
            elif voltage >= 3.7:
                self.state.relay_battery = 74
            elif voltage >= 3.6:
                self.state.relay_battery = 49
            else:
                self.state.relay_battery = 0
            _LOGGER.debug(">> FM2201+ base voltage=%sV batt=%s%%",
                          voltage, self.state.relay_battery)
            return self.state

        if n >= 23 and data[0] == 0x00 and data[1] == 0x05:
            # Full snapshot sent on connect. Two 8-byte records starting at
            # offset 5, each ``present batt t_lo t_hi a_lo a_hi tgt_lo tgt_hi``.
            # The leading byte is a presence/active flag (0x01), NOT the probe
            # number - probe identity is positional: 1st record = probe 1,
            # 2nd record = probe 2.
            for index, offset in enumerate((5, 14), start=1):
                if offset + 8 > n:
                    break
                rec = data[offset:offset + 8]
                if rec[0] == 0x00:
                    continue  # probe slot not present
                probe = self._probe(index)
                probe.battery = rec[1]
                probe.temperature = _u16le(rec[2:4]) / FM2201_TEMP_DIVISOR
                probe.ambient_temperature = _u16le(rec[4:6]) / FM2201_TEMP_DIVISOR
                probe.target_temperature = _u16le(rec[6:8]) / FM2201_TEMP_DIVISOR
                _LOGGER.debug(
                    ">> FM2201+ snapshot probe%s temp=%s ambient=%s target=%s",
                    index, probe.temperature, probe.ambient_temperature,
                    probe.target_temperature,
                )
            return self.state

        return self.state

    # ------------------------------------------------------------------- legacy
    def _parse_legacy(self, data: bytearray):
        """Original parser, unchanged, for the FMC family."""
        probe_channels = [0]  # Hardcoded probe channels

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
