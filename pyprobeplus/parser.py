"""Device BLE Parser."""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

# Constants for the original FMC series (single-channel).
PROBE_VOLTAGE_FACTOR = 0.03125
TEMP_FACTOR = 0.0625
TEMP_OFFSET = 50.0625
RELAY_VOLTAGE_DIVISOR = 1000.0

# Constants for the FM2 family (FM2201+, FM2209, FM210+ and rebrands such as
# INSMART), which shares a 0.1 degC little-endian wire format.
FM2_TEMP_DIVISOR = 10.0
FM2_TARGET_UNSET = 0xFFFF
# Offsets of the two channel targets within the 41-byte STATUS frame (0x00 0x05).
FM2_STATUS_TARGET_OFFSETS = (11, 20)
# Offsets of the two channel targets within the TARGET notification (0x00 0x03).
FM2_TARGET_FRAME_OFFSETS = (3, 5)


@dataclass
class ProbeReading:
    """A single physical probe slot on a Probe Plus base station."""

    channel: int
    temperature: float | None = None
    ambient_temperature: float | None = None
    target: float | None = None
    battery: float | None = None
    voltage: float | None = None
    rssi: int | None = None


@dataclass
class ProbePlusData:
    """Represents data from a Probe Plus device.

    `probes` is the canonical, per-slot representation (index 0 is the first
    physical probe, index 1 the second, and so on). The flat attributes below
    mirror `probes[0]` / `probes[1]` and exist only for backwards
    compatibility with earlier single/dual-probe releases of this library.
    """

    relay_battery: float | None = None
    relay_voltage: float | None = None
    relay_status: int | None = None
    probes: list[ProbeReading] = field(default_factory=list)

    def probe(self, index: int) -> ProbeReading | None:
        """Return the reading for probe slot `index`, if any data has arrived yet."""
        return self.probes[index] if index < len(self.probes) else None

    def _probe_field(self, index: int, attr: str):
        reading = self.probe(index)
        return getattr(reading, attr) if reading is not None else None

    # --- legacy flat accessors: probe 1 -------------------------------
    @property
    def probe_temperature(self) -> float | None:
        return self._probe_field(0, "temperature")

    @property
    def probe_battery(self) -> float | None:
        return self._probe_field(0, "battery")

    @property
    def probe_voltage(self) -> float | None:
        return self._probe_field(0, "voltage")

    @property
    def probe_rssi(self) -> int | None:
        return self._probe_field(0, "rssi")

    @property
    def ambient_temperature(self) -> float | None:
        return self._probe_field(0, "ambient_temperature")

    @property
    def target_1(self) -> float | None:
        return self._probe_field(0, "target")

    # --- legacy flat accessors: probe 2 -------------------------------
    @property
    def probe_temperature_2(self) -> float | None:
        return self._probe_field(1, "temperature")

    @property
    def probe_battery_2(self) -> float | None:
        return self._probe_field(1, "battery")

    @property
    def ambient_temperature_2(self) -> float | None:
        return self._probe_field(1, "ambient_temperature")

    @property
    def target_2(self) -> float | None:
        return self._probe_field(1, "target")


def _probe_battery_from_voltage(voltage: float) -> int:
    """Map probe battery voltage to a percentage (shared across families)."""
    if voltage >= 2.0:
        return 100
    if voltage >= 1.7:
        return 51
    if voltage >= 1.5:
        return 26
    return 20


class ParserBase:
    """Parser for the original FMC wire format (e.g. FMC210, FMC213).

    Device families that use a different wire format subclass this and
    override the hooks below rather than branching inline on device type.
    """

    def __init__(self) -> None:
        self.state: ProbePlusData = ProbePlusData()

    def parse_data(self, data: bytearray) -> ProbePlusData:
        """Handle a data notification from the device."""
        _LOGGER.debug(">> Received data notification: %s", data.hex())

        if len(data) == 9 and data[0] == 0x00 and data[1] == 0x00:
            self._parse_probe_frame(data)
        elif len(data) == 8 and data[0] == 0x00 and data[1] == 0x01:
            self._parse_relay_frame(data)
        else:
            self._parse_other_frame(data)

        return self.state

    # -- overridable hooks ------------------------------------------------

    def _parse_temperature(self, raw: bytearray) -> float:
        """Parse a 2-byte temperature reading.

        Original formula: byte-swap then big-endian unpack, scaled by
        0.0625 with a -50.0625 offset.
        """
        temp_val = struct.unpack(">H", bytes(raw[::-1]))[0]
        return (temp_val * TEMP_FACTOR) - TEMP_OFFSET

    def _channel_to_slot(self, channel: int) -> int:  # pylint: disable=unused-argument
        """Map the frame's channel byte to a probe slot index. FMC has one probe."""
        return 0

    def _relay_voltage(self, data: bytearray) -> float:
        """Parse the relay/station voltage. FMC encodes this big-endian."""
        return struct.unpack(">H", bytes(data[2:4]))[0] / RELAY_VOLTAGE_DIVISOR

    def _relay_battery(self, voltage: float) -> int:
        """Map relay voltage to a battery percentage (original FMC thresholds)."""
        if voltage > 3.87:
            return 100
        if voltage >= 3.7:
            return 74
        if voltage >= 3.6:
            return 49
        return 0

    def _parse_probe_frame(self, data: bytearray) -> None:
        """Parse a 9-byte probe state frame."""
        channel = data[2]
        slot = self._channel_to_slot(channel)
        while len(self.state.probes) <= slot:
            self.state.probes.append(ProbeReading(channel=len(self.state.probes)))
        probe = self.state.probes[slot]
        probe.channel = channel

        probe.voltage = data[3] * PROBE_VOLTAGE_FACTOR
        probe.battery = _probe_battery_from_voltage(probe.voltage)
        probe.temperature = self._parse_temperature(bytearray(data[4:6]))
        # RSSI is a signed dBm value encoded as a single byte.
        probe.rssi = int.from_bytes(data[8:9], "big", signed=True)

        _LOGGER.debug(">> Parsed temperature (ch%d): %s", channel, probe.temperature)

    def _parse_relay_frame(self, data: bytearray) -> None:
        """Parse an 8-byte relay/station state frame."""
        self.state.relay_voltage = self._relay_voltage(data)
        self.state.relay_battery = self._relay_battery(self.state.relay_voltage)
        self.state.relay_status = int(data[4])
        _LOGGER.debug(">> Relay voltage: %sV", self.state.relay_voltage)
        _LOGGER.debug(">> Relay state %s", self.state.relay_status)

    def _parse_other_frame(self, data: bytearray) -> None:
        """Handle any frame not recognised as a probe or relay frame. FMC: none."""
        return None


class Fm2Parser(ParserBase):
    """Parser for the FM2 family: FM2201+/FM2209 (dual probe) and FM210+ /
    INSMART rebrands (single probe).

    Temperatures are little-endian int16 values in tenths of a degree, and
    the base station reports its voltage little-endian (both verified via
    BLE sniffing against physical FM2201+ hardware).
    """

    def __init__(self) -> None:
        super().__init__()
        # Targets for a probe slot that hasn't sent a probe frame yet (e.g.
        # the STATUS frame arrives before the first periodic probe update).
        # Applied as soon as that slot's first probe frame creates it.
        self._pending_targets: dict[int, float | None] = {}

    def _parse_temperature(self, raw: bytearray) -> float:
        return int.from_bytes(raw, "little", signed=True) / FM2_TEMP_DIVISOR

    def _channel_to_slot(self, channel: int) -> int:
        # FM2201+/FM2209 send channel 1 (probe 1) and channel 2+ (probe 2).
        # FM210+ (single probe) sends channel 0. Either way, the first probe
        # always lands in slot 0.
        return 1 if channel >= 2 else 0

    def _relay_voltage(self, data: bytearray) -> float:
        return int.from_bytes(data[2:4], "little") / RELAY_VOLTAGE_DIVISOR

    def _relay_battery(self, voltage: float) -> int:
        # Thresholds reverse-engineered from the OEM app (mV: >=3900/3700/3460).
        if voltage >= 3.9:
            return 100
        if voltage >= 3.7:
            return 74
        if voltage >= 3.46:
            return 49
        return 0

    def _parse_probe_frame(self, data: bytearray) -> None:
        super()._parse_probe_frame(data)
        slot = self._channel_to_slot(data[2])
        # Ambient (oven) temperature, labelled 炉温 in the OEM source, at
        # bytes 6-7 of the probe frame — same encoding as the probe temperature.
        self.state.probes[slot].ambient_temperature = self._parse_temperature(
            bytearray(data[6:8])
        )
        if slot in self._pending_targets:
            self.state.probes[slot].target = self._pending_targets.pop(slot)

    def _parse_other_frame(self, data: bytearray) -> None:
        if len(data) == 41 and data[0] == 0x00 and data[1] == 0x05:
            self._parse_status_frame(data)
        elif len(data) >= 7 and data[0] == 0x00 and data[1] == 0x03:
            self._parse_target_frame(data)

    def _parse_status_frame(self, data: bytearray) -> None:
        """STATUS frame (0x00 0x05, 41 bytes): current alarm targets at init."""
        for slot, offset in enumerate(FM2_STATUS_TARGET_OFFSETS):
            self._set_target(slot, int.from_bytes(data[offset : offset + 2], "little"))

    def _parse_target_frame(self, data: bytearray) -> None:
        """TARGET notification (0x00 0x03): fired when a target is set/cleared."""
        for slot, offset in enumerate(FM2_TARGET_FRAME_OFFSETS):
            self._set_target(slot, int.from_bytes(data[offset : offset + 2], "little"))

    def _set_target(self, slot: int, raw: int) -> None:
        target = None if raw == FM2_TARGET_UNSET else raw / FM2_TEMP_DIVISOR
        # Never fabricate a probe slot from a STATUS/TARGET frame alone —
        # only a probe frame is proof the physical probe exists. A
        # single-probe device (e.g. FM210+) would otherwise gain a phantom
        # second probe the moment such a frame arrives. If the STATUS frame
        # (sent at connect time) arrives before that slot's first probe
        # frame, remember the target and apply it once the slot is created.
        if slot >= len(self.state.probes):
            self._pending_targets[slot] = target
            return
        self.state.probes[slot].target = target


def parser_for_device(name: str | None) -> ParserBase:
    """Return the parser matching the advertised BLE device name.

    Names starting with "FM2" (FM2201+, FM2209, FM210+ and rebrands such as
    INSMART) use the FM2 tenths-of-a-degree little-endian format; everything
    else, including an unknown name, falls back to the original FMC decode.
    """
    if (name or "").upper().startswith("FM2"):
        return Fm2Parser()
    return ParserBase()
