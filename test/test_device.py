"""Tests for ProbePlusDevice's device-family (parser) resolution.

Covers a bug caught by automated review on this PR: constructing
ProbePlusDevice from a bare MAC address string (no `name` kwarg) left the
parser permanently pinned to the default family, even after connect()
discovers the device's real advertised name via the scanner.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pyprobeplus import ProbePlusDevice
from pyprobeplus.parser import FMStandardParser, PlusParser


def _mock_scanner(device_name):
    device = MagicMock()
    device.name = device_name
    scanner = MagicMock()
    scanner.find_device_by_address = AsyncMock(return_value=device)
    return scanner


def test_parser_is_unresolved_without_a_name_at_construction():
    device = ProbePlusDevice("AA:BB:CC:DD:EE:FF", scanner=MagicMock())

    assert isinstance(device._device_state, FMStandardParser)
    assert not isinstance(device._device_state, PlusParser)
    assert device._name_resolved is False


def test_parser_is_resolved_immediately_when_name_is_given():
    device = ProbePlusDevice("AA:BB:CC:DD:EE:FF", scanner=MagicMock(), name="FM2201+")

    assert isinstance(device._device_state, PlusParser)
    assert device._name_resolved is True


def test_connect_resolves_the_parser_from_the_discovered_name(monkeypatch):
    scanner = _mock_scanner("FM2201+ AA:BB:CC:DD:EE:FF")
    device = ProbePlusDevice("AA:BB:CC:DD:EE:FF", scanner=scanner)
    assert not isinstance(device._device_state, PlusParser)  # unresolved so far

    established_client = MagicMock()
    established_client.start_notify = AsyncMock()
    monkeypatch.setattr(
        "pyprobeplus.establish_connection",
        AsyncMock(return_value=established_client),
    )

    asyncio.run(device.connect(setup_tasks=False))

    assert isinstance(device._device_state, PlusParser)
    assert device._name_resolved is True


def test_connect_keeps_standard_parser_for_a_standard_device(monkeypatch):
    scanner = _mock_scanner("FM210 AA:BB:CC:DD:EE:FF")
    device = ProbePlusDevice("AA:BB:CC:DD:EE:FF", scanner=scanner)

    established_client = MagicMock()
    established_client.start_notify = AsyncMock()
    monkeypatch.setattr(
        "pyprobeplus.establish_connection",
        AsyncMock(return_value=established_client),
    )

    asyncio.run(device.connect(setup_tasks=False))

    assert isinstance(device._device_state, FMStandardParser)
    assert not isinstance(device._device_state, PlusParser)
