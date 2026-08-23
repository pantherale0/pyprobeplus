"""Probe Plus device parsers."""

from __future__ import annotations

from .base import ParserBase, ProbePlusData, ProbeReading
from .const import FM2_TARGET_UNSET
from .fm22 import FM22Parser
from .fm_plus import PlusParser
from .fm_std import FMStandardParser


def parser_for_device(name: str | None) -> ParserBase:
    """Return the parser matching the advertised BLE device name.

    The OEM app treats a '+' in the name as the "new probe agreement"
    (tenths of a degree, little-endian). Everything else, including an
    unknown name, uses the standard 0.0625 formula.
    """
    name = (name or "")
    if "FM22" in name:
        return FM22Parser()
    if "+" in (name or ""):
        return PlusParser()
    return FMStandardParser()


__all__ = [
    "FM2_TARGET_UNSET",
    "FMStandardParser",
    "ParserBase",
    "PlusParser",
    "ProbePlusData",
    "ProbeReading",
    "parser_for_device",
]
