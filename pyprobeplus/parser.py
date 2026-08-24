"""Compatibility shim for the parsers package.

Prefer `pyprobeplus.parsers` for new imports. This module re-exports the
public parser types so existing `from pyprobeplus.parser import ...` callers
keep working.
"""

from .parsers import (
    FM2_TARGET_UNSET,
    FMStandardParser,
    ParserBase,
    PlusParser,
    ProbePlusData,
    ProbeReading,
    parser_for_device,
)

__all__ = [
    "FM2_TARGET_UNSET",
    "FMStandardParser",
    "ParserBase",
    "PlusParser",
    "ProbePlusData",
    "ProbeReading",
    "parser_for_device",
]
