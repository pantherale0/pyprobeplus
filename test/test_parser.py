"""Tests for pyprobeplus.parser.

Standard frames come from the ble_monitor issue #1429 HCI dump (advertised
name FM210). Plus frames come from the FM2201+ PR sniffs and the FM210+ /
INSMART frames in GitHub issue #10 (frankyman88).
"""

import pytest

from pyprobeplus.parsers import (
    FM2_TARGET_UNSET,
    FM22Parser,
    FMStandardParser,
    ParserBase,
    PlusParser,
    ProbePlusData,
    parser_for_device,
)

PARSER_CLASSES = (FMStandardParser, PlusParser)


def frame(*byte_values: int) -> bytearray:
    """Build a GATT notify payload from integer bytes."""
    return bytearray(byte_values)


def _status_frame(ch1_tenths: int, ch2_raw: int = FM2_TARGET_UNSET) -> bytearray:
    """Build a 41-byte STATUS notify (0x00 0x05) with two alarm slots."""
    data = bytearray(41)
    data[0], data[1] = 0x00, 0x05
    data[11:13] = ch1_tenths.to_bytes(2, "little")
    data[20:22] = ch2_raw.to_bytes(2, "little")
    return data


# ---------------------------------------------------------------------------
# Dump fixtures (parser_cls selects the formula that owns the capture)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    (
        "parser_cls",
        "raw",
        "channel",
        "temperature",
        "ambient",
        "voltage",
        "rssi",
        "battery",
    ),
    [
        # ble_monitor #1429 HCI dump (FM210). Ambient bytes are always 00 00.
        pytest.param(
            FMStandardParser,
            bytes.fromhex("00000059a8040000c5"),
            0,
            24.4375,
            None,
            0x59 * 0.03125,
            -59,
            100,
            id="fm210-hci-24.4",
        ),
        pytest.param(
            FMStandardParser,
            bytes.fromhex("00000059b0040000c9"),
            0,
            24.9375,
            None,
            0x59 * 0.03125,
            -55,
            100,
            id="fm210-hci-24.9",
        ),
        pytest.param(
            FMStandardParser,
            bytes.fromhex("00000059b1040000ca"),
            0,
            25.0,
            None,
            0x59 * 0.03125,
            -54,
            100,
            id="fm210-hci-25.0",
        ),
        pytest.param(
            FMStandardParser,
            bytes.fromhex("00000059c0040000d4"),
            0,
            25.9375,
            None,
            0x59 * 0.03125,
            -44,
            100,
            id="fm210-hci-25.9",
        ),
        # GitHub issue #10 — FM210+ / INSMART.
        pytest.param(
            PlusParser,
            bytes.fromhex("0000003ce700d200f3"),
            0,
            23.1,
            21.0,
            0x3C * 0.03125,
            -13,
            51,
            id="fm210plus-issue10",
        ),
        # FM2201+ PR sniff.
        pytest.param(
            FM22Parser,
            bytes.fromhex("0000016400010e01d7"),
            1,
            25.6,
            27.0,
            3.125,
            -41,
            100,
            id="fm2201plus-ch1",
        ),
        pytest.param(
            FM22Parser,
            bytes.fromhex("0000026400011201d7"),
            2,
            25.6,
            27.4,
            3.125,
            -41,
            100,
            id="fm2201plus-ch2",
        ),
    ],
)
def test_probe_frame_from_dumps(
    parser_cls,
    raw,
    channel,
    temperature,
    ambient,
    voltage,
    rssi,
    battery,
):
    """Decode a real probe notify with the parser that owns that capture."""
    state = parser_cls().parse_data(bytearray(raw))
    slot = 1 if channel >= 2 else 0
    probe = state.probes[slot]

    assert probe.channel == channel
    assert probe.temperature == pytest.approx(temperature)
    assert probe.ambient_temperature == (
        None if ambient is None else pytest.approx(ambient)
    )
    assert probe.voltage == pytest.approx(voltage)
    assert probe.rssi == rssi
    assert probe.battery == battery
    if parser_cls is FM22Parser:
        assert state.alarm_temperatures == [None, None]
    else:
        assert state.alarm_temperatures is None


@pytest.mark.parametrize(
    ("parser_cls", "raw", "voltage", "battery", "status"),
    [
        pytest.param(
            FMStandardParser,
            bytes.fromhex("0001b50f01ffffff"),
            4.021,
            100,
            1,
            id="fm210-hci-4.021-undocked",
        ),
        pytest.param(
            FMStandardParser,
            bytes.fromhex("0001b30f01ffffff"),
            4.019,
            100,
            1,
            id="fm210-hci-4.019-undocked",
        ),
        pytest.param(
            FMStandardParser,
            bytes.fromhex("0001b50f00ffffff"),
            4.021,
            100,
            0,
            id="fm210-hci-4.021-docked",
        ),
        pytest.param(
            FMStandardParser,
            bytes.fromhex("0001b30f00ffffff"),
            4.019,
            100,
            0,
            id="fm210-hci-4.019-docked",
        ),
        pytest.param(
            PlusParser,
            bytes.fromhex("0001b20f01ffffff"),
            4.018,
            100,
            1,
            id="fm210plus-issue10",
        ),
        pytest.param(
            PlusParser,
            bytes.fromhex("0001300f0101ffff"),
            3.888,
            74,
            1,
            id="fm2201plus",
        ),
    ],
)
def test_relay_frame_from_dumps(parser_cls, raw, voltage, battery, status):
    """Decode a real relay notify; millivolts are little-endian on both families."""
    state = parser_cls().parse_data(bytearray(raw))

    assert state.relay_voltage == pytest.approx(voltage)
    assert state.relay_battery == battery
    assert state.relay_status == status


# ---------------------------------------------------------------------------
# Shared behaviour (both parser classes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parser_cls", PARSER_CLASSES)
@pytest.mark.parametrize(
    ("probe_voltage_raw", "expected_battery"),
    [
        (64, 100),  # 2.0V
        (55, 51),  # 1.71875V
        (48, 26),  # 1.5V
        (40, 20),  # 1.25V
    ],
)
def test_probe_battery_thresholds_share_curve_across_families(
    parser_cls, probe_voltage_raw, expected_battery
):
    """Probe battery steps are the same 2.0 / 1.7 / 1.5 curve on both families."""
    data = frame(0x00, 0x00, 0x01, probe_voltage_raw, 0x00, 0x00, 0x00, 0x00, 0x00)
    state = parser_cls().parse_data(data)

    assert state.probes[0].battery == expected_battery


@pytest.mark.parametrize(
    ("parser_cls", "millivolts", "expected_battery"),
    [
        pytest.param(FMStandardParser, 3906, 100, id="std-100"),
        pytest.param(FMStandardParser, 3840, 74, id="std-74"),
        pytest.param(FMStandardParser, 3650, 49, id="std-49"),
        pytest.param(FMStandardParser, 3000, 0, id="std-0"),
        pytest.param(PlusParser, 3900, 100, id="plus-100"),
        pytest.param(PlusParser, 3800, 74, id="plus-74"),
        pytest.param(PlusParser, 3500, 49, id="plus-49"),
        pytest.param(PlusParser, 3000, 0, id="plus-0"),
    ],
)
def test_relay_frame_thresholds(parser_cls, millivolts, expected_battery):
    """Relay battery steps follow each family's voltage thresholds."""
    voltage_bytes = millivolts.to_bytes(2, "little")
    data = frame(0x00, 0x01, *voltage_bytes, 0x01, 0xFF, 0xFF, 0xFF)
    state = parser_cls().parse_data(data)

    assert state.relay_voltage == pytest.approx(millivolts / 1000.0)
    assert state.relay_battery == expected_battery
    assert state.relay_status == 1


@pytest.mark.parametrize(
    ("parser_cls", "expected_alarms"),
    [
        (FMStandardParser, None),
        (PlusParser, None),
        (FM22Parser, [None, None])
    ],
)
def test_ignores_unrecognised_frame(parser_cls, expected_alarms):
    """Unknown notify types leave probe and relay state untouched."""
    state = parser_cls().parse_data(frame(0x99, 0x99, 0x01, 0x02, 0x03))

    assert state.probes == []
    assert state.relay_voltage is None
    assert state.alarm_temperatures == expected_alarms


@pytest.mark.parametrize("parser_cls", PARSER_CLASSES)
def test_parser_instances_do_not_share_state(parser_cls):
    """Each parser instance keeps its own ProbePlusData."""
    first = parser_cls()
    second = parser_cls()
    first.parse_data(frame(0x00, 0x00, 0x00, 0x59, 0xA8, 0x04, 0x00, 0x00, 0xC5))

    assert first.state is not second.state
    assert isinstance(first.state, ProbePlusData)
    assert second.state.probes == []


# ---------------------------------------------------------------------------
# Plus-only: dual channel, alarms, signed temperature
# ---------------------------------------------------------------------------


def _with_both_channels_seen(parser):
    """Feed one FM2201+ probe frame per channel so probes[0]/probes[1] exist."""
    parser.parse_data(frame(0x00, 0x00, 0x01, 0x64, 0x00, 0x01, 0x0E, 0x01, 0xD7))
    parser.parse_data(frame(0x00, 0x00, 0x02, 0x64, 0x00, 0x01, 0x12, 0x01, 0xD7))
    return parser


def test_fm2201_channel_2_probe_frame_is_independent_of_channel_1():
    """Channel 2 must not overwrite channel 1's tip or ambient (FM2201+ sniff)."""
    parser = PlusParser()
    parser.parse_data(frame(0x00, 0x00, 0x01, 0x64, 0x00, 0x01, 0x0E, 0x01, 0xD7))
    state = parser.parse_data(
        frame(0x00, 0x00, 0x02, 0x64, 0x00, 0x01, 0x12, 0x01, 0xD7)
    )

    assert state.probes[0].temperature == pytest.approx(25.6)
    assert state.probes[0].ambient_temperature == pytest.approx(27.0)
    assert state.probes[1].temperature == pytest.approx(25.6)
    assert state.probes[1].ambient_temperature == pytest.approx(27.4)
    assert state.probes[1].battery == 100


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(_status_frame(200), [20.0, None], id="status"),
        pytest.param(
            frame(0x00, 0x03, 0x00, 0xFF, 0x00, 0xFF, 0xFF),
            [25.5, None],
            id="target",
        ),
    ],
)
def test_alarm_frame_updates_alarm_temperatures(payload, expected):
    """STATUS and TARGET write station alarms, not probe readings."""
    state = _with_both_channels_seen(FM22Parser()).parse_data(payload)

    assert state.alarm_temperatures == [pytest.approx(expected[0]), expected[1]]


def test_target_frame_does_not_fabricate_a_phantom_second_probe():
    """A dual-slot TARGET frame on FM210+ must not invent probes[1]."""
    parser = FM22Parser()
    parser.parse_data(frame(0x00, 0x00, 0x00, 0x3C, 0xE7, 0x00, 0xD2, 0x00, 0xF3))
    state = parser.parse_data(frame(0x00, 0x03, 0x00, 0xFF, 0x00, 0xFF, 0xFF))

    assert state.alarm_temperatures == [pytest.approx(25.5), None]
    assert len(state.probes) == 1


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_status_frame(255), id="status"),
        pytest.param(frame(0x00, 0x03, 0x00, 0xFF, 0x00, 0xFF, 0xFF), id="target"),
    ],
)
def test_alarm_frame_before_any_probe_frame_exposes_alarms_immediately(payload):
    """STATUS/TARGET at connect must set alarms without creating a probe slot."""
    state = FM22Parser().parse_data(payload)

    assert state.probes == []
    assert state.alarm_temperatures == [pytest.approx(25.5), None]


def test_fm2201_negative_temperature_is_signed():
    """Plus tip bytes are a signed little-endian int16 in tenths of a degree."""
    temp_bytes = (-50).to_bytes(2, "little", signed=True)
    data = frame(0x00, 0x00, 0x01, 0x64, *temp_bytes, 0x00, 0x00, 0x00)
    state = PlusParser().parse_data(data)

    assert state.probes[0].temperature == pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_parser_base_cannot_be_instantiated():
    """ParserBase is abstract; unknown names fall back to FMStandardParser."""
    with pytest.raises(TypeError):
        ParserBase()  # pylint: disable=abstract-class-instantiated


@pytest.mark.parametrize(
    ("name", "expected_type"),
    [
        (None, FMStandardParser),
        ("", FMStandardParser),
        ("FM210", FMStandardParser),
        ("FM210_coded", FMStandardParser),
        ("fm2209", FMStandardParser),
        ("FM210+", PlusParser),
        ("FM2201+", FM22Parser),
        ("fm2201+ AA:BB:CC:DD:EE:FF", FM22Parser),
    ],
)
def test_parser_for_device_dispatches_on_plus_in_name(name, expected_type):
    """A '+' in the advertised name selects PlusParser; everything else is standard."""
    assert (
        isinstance(parser_for_device(name), expected_type)
    )
