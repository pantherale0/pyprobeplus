"""Base classes for Probe Plus device parsers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, final

_LOGGER = logging.getLogger(__name__)


@dataclass
class ProbeReading:
    """A single physical probe slot on a Probe Plus base station."""

    channel: int
    temperature: float | None = None
    ambient_temperature: float | None = None
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
    probes: list[ProbeReading] = field(default_factory=list)
    relay_voltage: float | None = None
    relay_status: int | None = None
    alarm_temperatures: list[float | None] | None = None

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

class ParserBase(ABC):
    """Base class for Probe Plus device parsers."""

    MODEL: ClassVar[str]
    RELAY_BATTERY_THRESHOLDS: ClassVar[tuple[float, float, float]]

    def __init__(self) -> None:
        self.state: ProbePlusData = ProbePlusData(
            relay_battery_thresholds=self.RELAY_BATTERY_THRESHOLDS,
        )

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
