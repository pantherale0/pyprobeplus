"""Probe Plus BLE module for Python."""

from __future__ import annotations

__version__ = "1.0.1"

import asyncio
import logging
import time

from collections.abc import Awaitable, Callable

from bleak import BleakScanner, BleakGATTCharacteristic, BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import BLE_DATA_RECEIVE
from .exceptions import ProbePlusDeviceNotFound, ProbePlusError
from .parser import ParserBase, ProbePlusData

_LOGGER = logging.getLogger(__name__)

class ProbePlusDevice:
    """Representation of a Probe Plus device."""

    def __init__(
        self,
        address_or_ble_device: str | BLEDevice,
        name: str | None = None,
        notify_callback: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the probe."""

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

        self._device_state: ParserBase | None = ParserBase()

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
        device = await BleakScanner.find_device_by_address("AA:BB:CC:DD:EE:FF")

        if device is None:
            _LOGGER.debug("Device %s not found", self.mac)
            return

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
