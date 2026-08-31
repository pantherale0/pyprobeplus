"""Base classes for Probe Plus device parsers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar, final

from ..time_remaining_prediction import CookingTimeEstimator
from .const import PROBE_OFFLINE_MISS_THRESHOLD

_LOGGER = logging.getLogger(__name__)


@dataclass
class ProbeReading:
    """A single physical probe slot on a Probe Plus base station."""

    channel: int
    temperature: float | None = None
    ambient_temperature: float | None = None
    time_remaining: timedelta | None = None
    online: bool = True
    voltage: float | None = None
    rssi: int | None = None

    @property
    def battery(self) -> int | None:
        """Battery level as a percentage."""
        if not self.voltage:
            return
        if self.voltage >= 2.0:
            return 100
        if self.voltage >= 1.7:
            return 51
        if self.voltage >= 1.5:
            return 26
        return 20


@dataclass
class ProbePlusData:
    """Represents data from a Probe Plus device."""

    relay_battery_thresholds: tuple[float, float, float]
    probes: list[ProbeReading] = field(default_factory=lambda: [ProbeReading(1)])
    relay_voltage: float | None = None
    relay_status: int | None = None
    alarm_temperatures: list[float | None] | None = None
    cook_targets: list[float | None] = field(default_factory=lambda: [None, None])

    @property
    def supports_hardware_alarms(self) -> bool:
        """Return if the device supports hardware alarms or not."""
        return self.alarm_temperatures is not None

    @property
    def targets(self) -> list[float | None]:
        """Return the temperature targets for this device."""
        if self.alarm_temperatures:
            return self.alarm_temperatures
        return self.cook_targets

    def get_target_temperature(self, slot: int) -> float | None:
        """Return the target temperature for a given slot."""
        if len(self.targets) >= slot:
            return self.targets[slot]
        return

    @property
    def relay_battery(self) -> int | None:
        """Return the battery level of the Probe Plus device based on the voltage divisor."""
        if not self.relay_voltage:
            return None
        hi, mid, low = self.relay_battery_thresholds
        if self.relay_voltage >= hi:
            return 100
        if self.relay_voltage >= mid:
            return 74
        if self.relay_voltage >= low:
            return 49
        return 0

    @property
    def probe(self) -> ProbeReading:
        """Compatibility proxy to get a probe reading."""
        return self.probes[0]

class ParserBase(ABC):
    """Base class for Probe Plus device parsers."""

    MODEL: ClassVar[str]
    RELAY_BATTERY_THRESHOLDS: ClassVar[tuple[float, float, float]]

    def __init__(self) -> None:
        self.state: ProbePlusData = ProbePlusData(
            relay_battery_thresholds=self.RELAY_BATTERY_THRESHOLDS,
        )
        self._time_estimators: dict[int, CookingTimeEstimator] = {}
        self._time_estimator_targets: dict[int, float] = {}
        self._probe_slots_seen_since_relay: set[int] = set()
        self._probe_miss_counts: dict[int, int] = {}

    @final
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

    @final
    def _channel_to_slot(self, channel: int) -> int:
        """Return the slot based on the data channel."""
        return 1 if channel >= 2 else 0

    @abstractmethod
    def _parse_probe_frame(self, data: bytearray) -> None:
        """Parse the probe frame from the device."""

    @abstractmethod
    def _parse_relay_frame(self, data: bytearray) -> None:
        """Parse the relay frame from the device."""

    @abstractmethod
    def _parse_other_frame(self, data: bytearray) -> None:
        """Parse the other frame from the device."""

    def _should_update_time_remaining_on_probe_frame(self) -> bool:
        """Whether to update ETA when the probe frame is parsed."""
        return True

    def _mark_probe_slot_seen(self, slot: int) -> None:
        self._probe_slots_seen_since_relay.add(slot)
        if slot >= len(self.state.probes):
            return
        probe = self.state.probes[slot]
        if not probe.online:
            self._reset_time_estimator_for_slot(slot)
        probe.online = True
        self._probe_miss_counts[slot] = 0

    def _finalize_relay_frame(self) -> None:
        """Track probe presence across relay broadcast cycles."""
        for slot, probe in enumerate(self.state.probes):
            if slot in self._probe_slots_seen_since_relay:
                self._probe_miss_counts[slot] = 0
                probe.online = True
                continue

            misses = self._probe_miss_counts.get(slot, 0) + 1
            self._probe_miss_counts[slot] = misses
            if misses >= PROBE_OFFLINE_MISS_THRESHOLD:
                if probe.online:
                    probe.online = False
                    self._reset_time_estimator_for_slot(slot)
                else:
                    probe.time_remaining = None

        self._probe_slots_seen_since_relay.clear()

    def set_cook_target(self, slot: int, temp_c: float) -> None:
        """Set a software-only cook target for time-remaining estimates.

        Use on devices that do not expose alarm temperatures. When a device
        alarm is present for the slot, it takes precedence.
        """
        if slot not in (0, 1):
            raise ValueError(f"Invalid slot {slot}: expected 0 or 1")
        previous = self.state.cook_targets[slot]
        if previous != temp_c:
            self._reset_time_estimator_for_slot(slot)
        self.state.cook_targets[slot] = temp_c
        self._update_time_remaining(slot)

    def clear_cook_target(self, slot: int) -> None:
        """Clear a software-only cook target for a probe slot."""
        if slot not in (0, 1):
            raise ValueError(f"Invalid slot {slot}: expected 0 or 1")
        if self.state.cook_targets[slot] is not None:
            self._reset_time_estimator_for_slot(slot)
        self.state.cook_targets[slot] = None

    def get_target_temperature_for_slot(self, slot: int) -> float | None:
        """Return the effective cook target for a probe slot."""
        alarms = self.state.alarm_temperatures
        if alarms is not None and slot < len(alarms) and alarms[slot] is not None:
            return alarms[slot]
        if slot < len(self.state.cook_targets):
            return self.state.cook_targets[slot]
        return None

    def get_time_estimator_for_slot(self, slot: int) -> CookingTimeEstimator:
        """Return the CookingTimeEstimator for a given slot."""
        if slot not in self._time_estimators:
            self._time_estimators[slot] = CookingTimeEstimator()
        return self._time_estimators[slot]

    def _reset_time_estimator_for_slot(self, slot: int) -> None:
        if slot in self._time_estimators:
            self._time_estimators[slot].reset()
        self._time_estimator_targets.pop(slot, None)
        if slot < len(self.state.probes):
            self.state.probes[slot].time_remaining = None

    def _update_time_remaining(self, slot: int) -> None:
        """Refresh cook-time estimate for a probe after its reading changes."""
        if slot >= len(self.state.probes):
            return

        probe = self.state.probes[slot]
        if not probe.online:
            probe.time_remaining = None
            return
        if probe.temperature is None:
            probe.time_remaining = None
            return

        target = self.get_target_temperature_for_slot(slot)
        if target is None:
            probe.time_remaining = None
            return

        estimator = self.get_time_estimator_for_slot(slot)
        if self._time_estimator_targets.get(slot) != target:
            self._reset_time_estimator_for_slot(slot)
            self._time_estimator_targets[slot] = target
            estimator = self.get_time_estimator_for_slot(slot)

        probe.time_remaining = estimator.update(
            food_temp=probe.temperature,
            target_temp=target,
            ambient_temp=probe.ambient_temperature,
        )
