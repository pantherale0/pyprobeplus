"""Parser for FM plus devices (advertised name contains '+')."""

import logging

from .const import (
    FM2_TARGET_UNSET,
    PLUS_STATUS_TARGET_OFFSETS,
    PLUS_TARGET_FRAME_OFFSETS,
    PLUS_TEMP_DIVISOR,
    RELAY_VOLTAGE_DIVISOR,
)
from .fm_std import FMStandardParser

_LOGGER = logging.getLogger(__name__)

class PlusParser(FMStandardParser):
    """Parser for the OEM new probe agreement (e.g. FM210+, FM2201+)."""

    RELAY_BATTERY_THRESHOLDS = (3.9, 3.7, 3.46)
    MODEL = "+"

    def _should_update_time_remaining_on_probe_frame(self) -> bool:
        return False

    def _parse_temperature(self, raw: bytearray) -> float:
        """Parse temperatures as little-endian tenths of a degree."""
        return int.from_bytes(raw, "little", signed=True) / PLUS_TEMP_DIVISOR

    def _parse_relay_voltage(self, raw: bytearray) -> float:
        """Parse relay voltage (little-endian millivolts)."""
        return int.from_bytes(raw[2:4], "little") / RELAY_VOLTAGE_DIVISOR

    def _parse_probe_frame(self, data: bytearray) -> None:
        """Parse a frame from the probe, including ambient temperature."""
        super()._parse_probe_frame(data)
        slot = self._channel_to_slot(data[2])
        # Ambient (oven) temperature, labelled 炉温 in the OEM source, at
        # bytes 6-7 of the probe frame — same encoding as the probe temperature.
        self.state.probes[slot].ambient_temperature = self._parse_temperature(
            bytearray(data[6:8])
        )
        self._update_time_remaining(slot)

    def _parse_other_frame(self, data: bytearray) -> None:
        if len(data) == 41 and data[0] == 0x00 and data[1] == 0x05:
            self._parse_status_frame(data)
        elif len(data) == 7 and data[0] == 0x00 and data[1] == 0x03:
            self._parse_target_frame(data)

    def _parse_status_frame(self, data: bytearray) -> None:
        """Parse a status frame (0x00 0x05, 41 bytes): current alarm targets at init."""
        for slot, offset in enumerate(PLUS_STATUS_TARGET_OFFSETS):
            self._set_alarm_temperature(
                slot, int.from_bytes(data[offset : offset + 2], "little")
            )

    def _parse_target_frame(self, data: bytearray) -> None:
        """Parse a target frame (0x00 0x03): fired when a target is set/cleared."""
        for slot, offset in enumerate(PLUS_TARGET_FRAME_OFFSETS):
            self._set_alarm_temperature(
                slot, int.from_bytes(data[offset : offset + 2], "little")
            )

    def _set_alarm_temperature(self, slot: int, raw: int) -> None:
        """Set a channel alarm temperature from a raw little-endian value."""
        if not self.state.alarm_temperatures:
            _LOGGER.warning("Alarm temperatures not available for connected device")
            return
        alarm = None if raw == FM2_TARGET_UNSET else raw / PLUS_TEMP_DIVISOR
        previous = self.state.alarm_temperatures[slot]
        if previous != alarm:
            self._reset_time_estimator_for_slot(slot)
        self.state.alarm_temperatures[slot] = alarm
