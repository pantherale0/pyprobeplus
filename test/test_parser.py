"""Tests for pyprobeplus.parser.

Frames are taken verbatim from real BLE sniffs: the FM2201+ frames from the
PR description for this parser, and the FM210+/INSMART frames from GitHub
issue #10 (frankyman88).
"""

import pytest

from pyprobeplus.parser import (
    FM2_TARGET_UNSET,
    Fm2Parser,
    ParserBase,
    ProbePlusData,
    parser_for_device,
)


def frame(*byte_values: int) -> bytearray:
    return bytearray(byte_values)


# ---------------------------------------------------------------------------
# FMC series (original, single probe) — regression coverage
# ---------------------------------------------------------------------------


def test_fmc_probe_frame_uses_original_formula():
    parser = ParserBase()
    # data[4:6] = e7 00 -> byte-swapped big-endian 0x00e7 = 231
    state = parser.parse_data(
        frame(0x00, 0x00, 0x00, 0x40, 0xE7, 0x00, 0x00, 0x00, 0x10)
    )

    assert state.probe_temperature == pytest.approx((231 * 0.0625) - 50.0625)
    assert state.probe_voltage == pytest.approx(0x40 * 0.03125)
    assert state.probe_rssi == 0x10
    # FMC never populates ambient/second-probe/target data.
    assert state.ambient_temperature is None
    assert state.probe_temperature_2 is None


@pytest.mark.parametrize(
    ("voltage_bytes", "expected_battery"),
    [
        ((0x0F, 0x42), 100),  # 3.906V > 3.87
        ((0x0F, 0x00), 74),  # 3.840V, in [3.7, 3.87]
        ((0x0E, 0x42), 49),  # 3.650V, in [3.6, 3.7)
        ((0x0B, 0xB8), 0),  # 3.000V, below 3.6
    ],
)
def test_fmc_relay_frame_thresholds(voltage_bytes, expected_battery):
    parser = ParserBase()
    hi, lo = voltage_bytes
    data = frame(0x00, 0x01, hi, lo, 0x01, 0xFF, 0xFF, 0xFF)
    voltage = int.from_bytes(bytes([hi, lo]), "big") / 1000.0

    state = parser.parse_data(data)

    assert state.relay_voltage == pytest.approx(voltage)
    assert state.relay_battery == expected_battery
    assert state.relay_status == 1


# ---------------------------------------------------------------------------
# FM2201+ (dual probe)
# ---------------------------------------------------------------------------


def test_fm2201_channel_1_probe_frame():
    parser = Fm2Parser()
    # 00 00 01 64 00 01 0e 01 d7
    state = parser.parse_data(
        frame(0x00, 0x00, 0x01, 0x64, 0x00, 0x01, 0x0E, 0x01, 0xD7)
    )

    assert state.probe_temperature == pytest.approx(25.6)
    assert state.ambient_temperature == pytest.approx(27.0)
    assert state.probe_voltage == pytest.approx(3.125)
    assert state.probe_battery == 100
    assert state.probe_rssi == -41
    # second probe untouched
    assert state.probe_temperature_2 is None


def test_fm2201_channel_2_probe_frame_is_independent_of_channel_1():
    parser = Fm2Parser()
    parser.parse_data(frame(0x00, 0x00, 0x01, 0x64, 0x00, 0x01, 0x0E, 0x01, 0xD7))
    # 00 00 02 64 00 01 12 01 d7
    state = parser.parse_data(
        frame(0x00, 0x00, 0x02, 0x64, 0x00, 0x01, 0x12, 0x01, 0xD7)
    )

    # channel 1 data must survive channel 2 arriving
    assert state.probe_temperature == pytest.approx(25.6)
    assert state.ambient_temperature == pytest.approx(27.0)
    # channel 2 decodes independently (bytes 6:8 = 12 01 -> 0x0112 = 274)
    assert state.probe_temperature_2 == pytest.approx(25.6)
    assert state.ambient_temperature_2 == pytest.approx(27.4)
    assert state.probe_battery_2 == 100


def test_fm2201_relay_frame_is_little_endian():
    parser = Fm2Parser()
    # 00 01 30 0f 01 01 ff ff -> 0x0f30 = 3888mV
    state = parser.parse_data(frame(0x00, 0x01, 0x30, 0x0F, 0x01, 0x01, 0xFF, 0xFF))

    assert state.relay_voltage == pytest.approx(3.888)
    assert state.relay_battery == 74
    assert state.relay_status == 1


def test_fm2201_status_frame_sets_targets():
    parser = Fm2Parser()
    data = bytearray(41)
    data[0], data[1] = 0x00, 0x05
    data[11:13] = (200).to_bytes(2, "little")  # 20.0 degC
    data[20:22] = FM2_TARGET_UNSET.to_bytes(2, "little")

    state = parser.parse_data(data)

    assert state.target_1 == pytest.approx(20.0)
    assert state.target_2 is None


def test_fm2201_target_frame_updates_targets():
    parser = Fm2Parser()
    data = frame(0x00, 0x03, 0x00, 0xFF, 0x00, 0xFF, 0xFF)  # ch1=25.5, ch2=unset

    state = parser.parse_data(data)

    assert state.target_1 == pytest.approx(25.5)
    assert state.target_2 is None


def test_fm2201_negative_temperature_is_signed():
    parser = Fm2Parser()
    # -50 tenths of a degree, little-endian two's complement -> -5.0 degC
    temp_bytes = (-50).to_bytes(2, "little", signed=True)
    data = frame(0x00, 0x00, 0x01, 0x64, *temp_bytes, 0x00, 0x00, 0x00)

    state = parser.parse_data(data)

    assert state.probe_temperature == pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# FM210+ / INSMART (single probe, GitHub issue #10)
# ---------------------------------------------------------------------------


def test_fm210_probe_frame_channel_zero_uses_fm2_formula():
    parser = Fm2Parser()
    # 00 00 00 3c e7 00 d2 00 f3
    state = parser.parse_data(
        frame(0x00, 0x00, 0x00, 0x3C, 0xE7, 0x00, 0xD2, 0x00, 0xF3)
    )

    assert state.probe_temperature == pytest.approx(23.1)
    assert state.ambient_temperature == pytest.approx(21.0)


def test_fm210_relay_frame_is_little_endian():
    parser = Fm2Parser()
    # 00 01 b2 0f 01 ff ff ff -> 0x0fb2 = 4018mV
    state = parser.parse_data(frame(0x00, 0x01, 0xB2, 0x0F, 0x01, 0xFF, 0xFF, 0xFF))

    assert state.relay_voltage == pytest.approx(4.018)
    assert state.relay_battery == 100


def test_fm2_relay_frame_thresholds():
    for millivolts, expected_battery in (
        (3900, 100),
        (3800, 74),
        (3500, 49),
        (3000, 0),
    ):
        parser = Fm2Parser()
        voltage_bytes = millivolts.to_bytes(2, "little")
        data = frame(0x00, 0x01, *voltage_bytes, 0x01, 0xFF, 0xFF, 0xFF)

        state = parser.parse_data(data)

        assert state.relay_voltage == pytest.approx(millivolts / 1000.0)
        assert state.relay_battery == expected_battery


def test_probe_battery_thresholds_share_curve_across_families():
    for probe_voltage_raw, expected_battery in (
        (64, 100),  # 2.0V
        (55, 51),  # 1.71875V
        (48, 26),  # 1.5V
        (40, 20),  # 1.25V
    ):
        parser = Fm2Parser()
        data = frame(0x00, 0x00, 0x01, probe_voltage_raw, 0x00, 0x00, 0x00, 0x00, 0x00)

        state = parser.parse_data(data)

        assert state.probe_battery == expected_battery


def test_fmc_ignores_unrecognised_frame():
    parser = ParserBase()
    state = parser.parse_data(frame(0x99, 0x99, 0x01, 0x02, 0x03))

    assert state.probe_temperature is None
    assert state.relay_voltage is None


def test_fm2_ignores_unrecognised_frame():
    parser = Fm2Parser()
    state = parser.parse_data(frame(0x99, 0x99, 0x01, 0x02, 0x03))

    assert state.probe_temperature is None
    assert state.relay_voltage is None
    assert state.target_1 is None


# ---------------------------------------------------------------------------
# Structural behaviour
# ---------------------------------------------------------------------------


def test_parser_instances_do_not_share_state():
    a = ParserBase()
    b = ParserBase()

    a.parse_data(frame(0x00, 0x00, 0x00, 0x40, 0xE7, 0x00, 0x00, 0x00, 0x10))

    assert a.state is not b.state
    assert isinstance(a.state, ProbePlusData)
    assert b.state.probe_temperature is None


@pytest.mark.parametrize(
    ("name", "expected_type"),
    [
        (None, ParserBase),
        ("", ParserBase),
        ("FMC210", ParserBase),
        ("FMC213", ParserBase),
        ("FM2201+", Fm2Parser),
        ("fm2209", Fm2Parser),
        # Advertised name per GitHub issue #10 (INSMART is only the retail
        # branding; the BLE device name itself is "FM210+").
        ("FM210+", Fm2Parser),
    ],
)
def test_parser_for_device_dispatches_on_name(name, expected_type):
    assert (
        type(parser_for_device(name)) is expected_type
    )  # pylint: disable=unidiomatic-typecheck
