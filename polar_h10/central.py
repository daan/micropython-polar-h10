"""
polar_h10.central - aioble (MicroPython) BLE transport for the Polar H10.

This is the device-side counterpart to examples/acc_desktop.py (which is the
bleak transport). Both are thin scaffolding around the SAME protocol core in
polar_h10.pmd - this module just feeds notifications into parse_acc_frame and
writes the commands that build_acc_*_command produces.

MicroPython-only: it imports `aioble` and `bluetooth`, which do not exist on a
desktop CPython. That is why polar_h10/__init__.py does NOT import this module -
keeping `import polar_h10` working on the laptop where you develop the core.

Typical use on the ESP32:

    import asyncio
    from polar_h10.central import find_h10, PolarH10

    async def main():
        device = await find_h10()
        if not device:
            print("No Polar H10 found (is it worn/wetted?)")
            return
        async with PolarH10(device) as h10:        # connects + pairs + subscribes
            await h10.start_acc(sample_rate=200, range_g=8)
            while True:
                out = await h10.read_frame(fs=200)
                for (x, y, z) in out["samples"]:
                    print(x, y, z)  # milli-g

    asyncio.run(main())
"""

import aioble
import bluetooth

from .pmd import (
    PMD_SERVICE_UUID,
    PMD_CONTROL_UUID,
    PMD_DATA_UUID,
    build_acc_start_command,
    build_acc_stop_command,
    parse_acc_frame,
    parse_hr_measurement,
)

_SVC = bluetooth.UUID(PMD_SERVICE_UUID)
_CTRL = bluetooth.UUID(PMD_CONTROL_UUID)
_DATA = bluetooth.UUID(PMD_DATA_UUID)

# Standard Heart Rate service / measurement characteristic (16-bit UUIDs).
_HR_SVC = bluetooth.UUID(0x180D)
_HR_CHAR = bluetooth.UUID(0x2A37)

NAME_PREFIX = "Polar H10"


async def find_h10(timeout_ms=5000, name_prefix=NAME_PREFIX):
    """Scan for a Polar H10 by advertised name. Returns an aioble Device or None.

    Scanning by name (rather than MAC) works across platforms and needs no
    pairing. The strap must be worn or its electrodes wetted to advertise.
    """
    async with aioble.scan(timeout_ms, interval_us=30000, window_us=30000, active=True) as scanner:
        async for result in scanner:
            name = result.name()
            if name and name.startswith(name_prefix):
                return result.device
    return None


class PolarH10:
    """Async-context wrapper around one H10 connection and its PMD characteristics.

    On entry it connects, requests a larger MTU so PMD frames arrive
    un-fragmented, resolves the requested characteristics, and subscribes to
    their notifications. On exit it tears the connection down.

    Both the PMD accelerometer (``acc=True``) and the standard Heart Rate
    measurement (``hr=True``) ride the same connection, so a dashboard can read
    ``read_frame()`` and ``read_hr()`` concurrently from one PolarH10.
    """

    def __init__(self, device, mtu=232, pair=True, acc=True, hr=False):
        self._device = device
        self._mtu = mtu
        self._pair = pair
        self._want_acc = acc
        self._want_hr = hr
        self._connection = None
        self._control = None
        self._data = None
        self._hr = None

    async def __aenter__(self):
        self._connection = await self._device.connect()
        await self._connection.__aenter__()
        try:
            try:
                await self._connection.exchange_mtu(self._mtu)
            except Exception:
                pass  # fall back to default MTU if the peer declines

            # The H10's PMD service requires an ENCRYPTED link: every control-point
            # write and notify-enable is rejected with ATT "Insufficient
            # Authentication" (0x05) until the connection is paired, after which
            # the strap drops the link. Desktop stacks (e.g. CoreBluetooth) pair on
            # demand transparently; aioble does not, so we pair explicitly here.
            # (Plain HR GATT doesn't need it, but pairing the shared link is fine.)
            if self._pair:
                await self._connection.pair()

            if self._want_acc:
                service = await self._connection.service(_SVC)
                self._control = await service.characteristic(_CTRL)
                self._data = await service.characteristic(_DATA)
                await self._data.subscribe(notify=True)

            if self._want_hr:
                hr_service = await self._connection.service(_HR_SVC)
                self._hr = await hr_service.characteristic(_HR_CHAR)
                await self._hr.subscribe(notify=True)
        except Exception:
            # Don't leak the connection if setup fails partway: the H10 allows only
            # one central, so a held link locks everyone out until it times out.
            await self._connection.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *exc):
        return await self._connection.__aexit__(*exc)

    async def start_acc(self, sample_rate=200, range_g=8):
        """Write the Control-Point command that starts the ACC stream."""
        cmd = build_acc_start_command(sample_rate=sample_rate, resolution=16, range_g=range_g)
        await self._control.write(cmd, response=True)

    async def stop_acc(self):
        """Stop the ACC stream."""
        await self._control.write(build_acc_stop_command(), response=True)

    async def read_frame(self, fs=200):
        """Await the next data notification and return one decoded ACC frame dict.

        Call this in a loop: ``while True: out = await h10.read_frame()``. (A plain
        coroutine rather than an async generator, because MicroPython's async-
        generator support is unreliable across builds.)
        """
        frame = await self._data.notified()
        return parse_acc_frame(frame, fs=fs)

    async def read_hr(self):
        """Await the next Heart Rate notification and return a decoded dict.

        ``{"bpm": int, "contact": bool|None, "rr_ms": [...]}``. Requires the
        PolarH10 to have been created with ``hr=True``.
        """
        data = await self._hr.notified()
        return parse_hr_measurement(data)
