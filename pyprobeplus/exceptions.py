"""Exceptions for probe_plus."""

from bleak.exc import BleakDeviceNotFoundError, BleakError


class ProbePlusException(Exception):
    """Base class for exceptions in this module."""


class ProbePlusDeviceNotFound(BleakDeviceNotFoundError):
    """Exception when no device is found."""


class ProbePlusError(BleakError):
    """Exception for general bleak errors."""


class ProbePlusUnknownDevice(Exception):
    """Exception for unknown devices."""


class ProbePlusMessageError(Exception):
    """Exception for message errors."""

    def __init__(self, bytes_recvd: bytearray, message: str) -> None:
        super().__init__()
        self.message = message
        self.bytes_recvd = bytes_recvd


class ProbePlusMessageTooShort(ProbePlusMessageError):
    """Exception for messages that are too short."""

    def __init__(self, bytes_recvd: bytearray) -> None:
        super().__init__(bytes_recvd, "Message too short")


class ProbePlusMessageTooLong(ProbePlusMessageError):
    """Exception for messages that are too long."""

    def __init__(self, bytes_recvd: bytearray) -> None:
        super().__init__(bytes_recvd, "Message too long")

class ProbePlusNotSupported(ProbePlusException):
    """Exception for functions that are not supported by a connected device."""
