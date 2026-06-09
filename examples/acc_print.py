"""
acc_print.py - on-device (ESP32 / MicroPython / aioble) ACC printer.

Copy this to the board and run it after installing the polar_h10 package. It is
the device-side mirror of examples/acc_desktop.py: same protocol core, aioble
transport instead of bleak.

    import acc_print  # or paste into the REPL
"""

import asyncio

from polar_h10.central import find_h10, PolarH10

SAMPLE_RATE = 200  # Hz: 25 | 50 | 100 | 200
RANGE_G = 8        # G: 2 | 4 | 8


async def main():
    print("Scanning for Polar H10...")
    device = await find_h10()
    if device is None:
        print("No Polar H10 found (is it worn/wetted, and free of the phone app?)")
        return

    print("Connecting...")
    async with PolarH10(device) as h10:  # connects, pairs (PMD needs encryption), subscribes
        await h10.start_acc(sample_rate=SAMPLE_RATE, range_g=RANGE_G)
        print("Streaming. Ctrl-C to stop.")
        while True:
            out = await h10.read_frame(fs=SAMPLE_RATE)
            for (x, y, z) in out["samples"]:
                print(x, y, z)  # milli-g


asyncio.run(main())
