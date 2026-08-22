"""Probe Plus BLE module for Python."""

from __future__ import annotations

__version__ = "1.0.1"

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from bleak import BleakGATTCharacteristic, BleakScanner, BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import BLE_DATA_RECEIVE, BLE_DATA_WRITE
from .exceptions import ProbePlusDeviceNotFound, ProbePlusError
from .parsers import FM2_TARGET_UNSET, ParserBase, ProbePlusData, parser_for_device

_LOGGER = logging.getLogger(__name__)


class ProbePlusDevice:
    """Representation of a Probe Plus device."""

    def __init__(
        self,
        address_or_ble_device: str | BLEDevice,
        scanner: BleakScanner | None = None,
        name: str | None = None,
        notify_callback: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the probe."""

        self._scanner = scanner if scanner else BleakScanner()
        self._client: BleakClientWithServiceCache | None = None

        self.address_or_ble_device = address_or_ble_device
        self.name = name

        # tasks
        self.heartbeat_task: asyncio.Task | None = None
        self.process_queue_task: asyncio.Task | None = None

        # connection diagnostics
        self.connected = False
        self._timestamp_last_command: float | None = None
        self.last_disconnect_time: float | None = None

        # Standard vs plus is selected from the advertised BLE name (a '+'
        # means the new probe agreement) — either passed in directly, carried
        # by a BLEDevice, or (if neither is available yet) discovered later
        # in connect(). A BLEDevice with no name yet (e.g. before its
        # advertisement data is fully parsed) does NOT count as resolved —
        # only an actual name does.
        resolved_name = name or getattr(address_or_ble_device, "name", None)
        self._name_resolved = bool(resolved_name)
        self._device_state: ParserBase | None = parser_for_device(resolved_name)

        # queue
        self._queue: asyncio.Queue = asyncio.Queue()
        self._add_to_queue_lock = asyncio.Lock()

        self._last_short_msg: bytearray | None = None

        self._notify_callback: Callable[[], None] | None = notify_callback

    @property
    def mac(self) -> str:
        """Return the mac address of the probe in upper case."""
        return (
            self.address_or_ble_device.upper()
            if isinstance(self.address_or_ble_device, str)
            else self.address_or_ble_device.address.upper()
        )

    @property
    def device_state(self) -> ProbePlusData | None:
        """Return the device info of the probe."""
        return self._device_state.state

    def device_disconnected_handler(
        self,
        client: BleakClientWithServiceCache | None = None,  # pylint: disable=unused-argument
        notify: bool = True,
    ) -> None:
        """Callback for device disconnected."""

        _LOGGER.debug(
            "probe with address %s disconnected through disconnect handler",
            self.mac,
        )
        self.connected = False
        self.last_disconnect_time = time.time()
        self.async_empty_queue_and_cancel_tasks()
        if notify and self._notify_callback:
            self._notify_callback()

    def async_empty_queue_and_cancel_tasks(self) -> None:
        """Empty the queue."""

        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()

        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()

        if self.process_queue_task and not self.process_queue_task.done():
            self.process_queue_task.cancel()

    async def process_queue(self) -> None:
        """Task to process the queue in the background."""
        while True:
            try:
                if not self.connected:
                    self.async_empty_queue_and_cancel_tasks()
                    return
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                self.connected = False
                return
            except (ProbePlusDeviceNotFound, ProbePlusError) as ex:
                self.connected = False
                _LOGGER.debug("Error communicating with device: %s", ex)
                return

    async def connect(
        self,
        callback: (
            Callable[[BleakGATTCharacteristic, bytearray], Awaitable[None] | None]
            | None
        ) = None,
        setup_tasks: bool = True,
    ) -> None:
        """Connect the bluetooth client."""

        if self.connected:
            return

        if self.last_disconnect_time and self.last_disconnect_time > (time.time() - 15):
            _LOGGER.debug(
                "Probe has recently been disconnected, waiting 15 seconds before reconnecting"
            )
            return

        # Find the device
        device = await self._scanner.find_device_by_address(self.mac)

        if device is None:
            _LOGGER.debug("Device %s not found", self.mac)
            return

        # If the device family couldn't be resolved at construction time
        # (e.g. constructed from a bare MAC address string with no `name`),
        # resolve it now that the scanner has discovered the advertised name.
        if not self._name_resolved and device.name:
            self._device_state = parser_for_device(device.name)
            self._name_resolved = True

        try:
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                device,
                device.name,
                max_attempts=3,
                disconnected_callback=self.device_disconnected_handler,
            )
        except BleakError as ex:
            _LOGGER.debug("Error connecting to device: %s", ex)
            raise ProbePlusError("Error connecting to device") from ex

        self.connected = True
        _LOGGER.debug("Connected to Probe Plus device")

        if callback is None:
            callback = self.on_bluetooth_data_received
        try:
            await self._client.start_notify(
                char_specifier=BLE_DATA_RECEIVE,
                callback=(
                    self.on_bluetooth_data_received if callback is None else callback
                ),
            )
            await asyncio.sleep(0.1)
        except BleakError as ex:
            msg = "Error subscribing to notifications"
            _LOGGER.debug("%s: %s", msg, ex)
            raise ProbePlusError(msg) from ex

        if setup_tasks:
            self._setup_tasks()

    def _setup_tasks(self) -> None:
        """Setup background tasks"""
        if not self.process_queue_task or self.process_queue_task.done():
            self.process_queue_task = asyncio.create_task(self.process_queue())

    async def disconnect(self) -> None:
        """Clean disconnect from the probe."""

        _LOGGER.debug("Disconnecting from probe")
        self.connected = False
        await self._queue.join()
        if not self._client:
            return
        try:
            await self._client.disconnect()
        except BleakError as ex:
            _LOGGER.debug("Error disconnecting from device: %s", ex)
        else:
            _LOGGER.debug("Disconnected from probe")

    async def write_target(self, ch: int, temp_c: float) -> None:
        """Set alarm target temperature for a channel (FM22xx only).

        Protocol: 01 03 [CH] [temp_lo] [temp_hi]
        Temperature in tenths of degrees, little-endian.
        """
        if not self.connected or self._client is None:
            raise ProbePlusError("Device not connected")
        if ch not in (1, 2):
            raise ProbePlusError(f"Invalid channel {ch}: expected 1 or 2")
        raw = round(temp_c * 10)
        if raw < 0 or raw >= FM2_TARGET_UNSET:
            raise ProbePlusError(f"Target temperature {temp_c} out of supported range")
        payload = bytes([0x01, 0x03, ch, raw & 0xFF, (raw >> 8) & 0xFF])
        _LOGGER.debug(
            "write_target ch=%d temp=%.1f payload=%s", ch, temp_c, payload.hex()
        )
        try:
            await self._client.write_gatt_char(BLE_DATA_WRITE, payload, response=False)
        except BleakError as ex:
            raise ProbePlusError("Error writing target temperature") from ex

    async def clear_target(self, ch: int) -> None:
        """Clear alarm target for a channel (FM22xx only).

        Protocol: 02 03 [CH]
        """
        if not self.connected or self._client is None:
            raise ProbePlusError("Device not connected")
        if ch not in (1, 2):
            raise ProbePlusError(f"Invalid channel {ch}: expected 1 or 2")
        payload = bytes([0x02, 0x03, ch])
        _LOGGER.debug("clear_target ch=%d payload=%s", ch, payload.hex())
        try:
            await self._client.write_gatt_char(BLE_DATA_WRITE, payload, response=False)
        except BleakError as ex:
            raise ProbePlusError("Error clearing target temperature") from ex

    async def on_bluetooth_data_received(
        self,
        characteristic: BleakGATTCharacteristic,  # pylint: disable=unused-argument
        data: bytearray,
    ) -> None:
        """Receive data from probe."""
        _LOGGER.debug("%s: Notification received: %s", self.mac, data.hex())
        self._device_state.parse_data(data)
        if self._notify_callback is not None:
            self._notify_callback()
