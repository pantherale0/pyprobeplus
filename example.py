"""Find a Probe Plus device with Bleak and print its readable characteristics."""

from __future__ import annotations

import asyncio
import logging
import sys

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from pyprobeplus import ProbePlusDevice
from pyprobeplus.exceptions import ProbePlusError


async def select_device(scanner: BleakScanner, timeout: float = 5.0) -> BLEDevice | str:
    """Scan for Bluetooth devices and prompt the user to pick one."""
    print(f"Scanning for Bluetooth devices ({timeout:.0f}s)...")
    discovered = await scanner.discover(return_adv=True, timeout=timeout)
    if not discovered:
        raise SystemExit("No Bluetooth devices found.")

    devices = sorted(
        discovered.values(),
        key=lambda item: (
            item[0].name is None,
            (item[0].name or "").lower(),
            item[0].address,
        ),
    )

    print("\nFound devices:")
    for index, (dev, adv) in enumerate(devices, start=1):
        name = dev.name or "Unknown"
        print(f"  {index}. {name} ({dev.address})  RSSI: {adv.rssi}")

    while True:
        choice = input(f"\nSelect a device [1-{len(devices)}] or enter a MAC address or press R to rescan or press Q to exit: ").strip()
        try:
            if choice.upper() == "Q":
                sys.exit(0)
            if choice.upper() == "R":
                return await select_device(scanner)
            if ":" in choice:
                return choice
            selected = int(choice)
        except ValueError:
            print("Invalid selection. Enter a number from the list.")
            continue
        if 1 <= selected <= len(devices):
            return devices[selected - 1][0]
        print("Invalid selection. Enter a number from the list.")


async def main() -> None:
    """Main func."""
    scanner = BleakScanner(scanning_mode="active")
    try:
        ble_device = await select_device(scanner)
    except KeyboardInterrupt:
        return
    device = ProbePlusDevice(
        address_or_ble_device=ble_device,
        scanner=scanner,
    )
    print(f"Selected {device.name or 'Unknown'} ({device.mac})")
    print("Started. Press Ctrl+C to stop.")
    while True:
        try:
            await device.connect()
            if not device.connected:
                print("Make sure the device is turned on. Retrying connecting in 10s")
                await asyncio.sleep(10.0)
                continue
            print("Device connected")
            # Stream data from the device
            while device.connected:
                print(f"Model: {device.name}")
                print(f"Alarms supported: {'Y' if device.device_state.alarm_temperatures else 'N'}")
                print(f"Probes: {len(device.device_state.probes)}")
                print(f"Relay battery: {device.device_state.relay_battery}")
                print(f"Relay voltage: {device.device_state.relay_voltage}")
                print(f"Relay status: {device.device_state.relay_status}")
                for probe in device.device_state.probes:
                    print(f"Probe temperature: {probe.temperature}")
                    print(f"Probe RSSI: {probe.rssi}")
                    print(f"Probe battery: {probe.battery}")
                    print(f"Probe voltage: {probe.voltage}")
                await asyncio.sleep(2.0)
        except ProbePlusError:
            print("Error connecting to device.")
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(main())
