"""
acc_desktop.py - Drive the transport-independent polar_pmd core over Bleak.

Run on a laptop (macOS/Linux/Windows) to verify the PMD protocol against a real
Polar H10 *before* deploying the same core to an ESP32 via aioble. Only the BLE
transport here differs from the device build; build_acc_start_command /
parse_acc_frame are imported unchanged from polar_pmd.py.

Usage:
    uv run python examples/acc_desktop.py            # 200 Hz / 8 G, 5 s
    uv run python examples/acc_desktop.py --rate 50 --range 4 --seconds 10

The strap must be worn (or its electrodes wetted) to advertise, and no other
central (phone/Polar app) may be connected to it.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Import the protocol core from the repo root regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bleak import BleakClient, BleakScanner

from polar_h10.pmd import (
    PMD_CONTROL_UUID,
    PMD_DATA_UUID,
    build_acc_start_command,
    build_acc_stop_command,
    parse_acc_frame,
    parse_control_point_response,
)

NAME_PREFIX = "Polar H10"


async def find_h10(timeout):
    print(f"Scanning {timeout:.0f}s for a device named '{NAME_PREFIX}...'")

    def match(device, adv):
        name = adv.local_name or device.name
        return bool(name and name.startswith(NAME_PREFIX))

    device = await BleakScanner.find_device_by_filter(match, timeout=timeout)
    return device


async def run(rate, range_g, seconds, scan_timeout):
    device = await find_h10(scan_timeout)
    if device is None:
        print("No Polar H10 found. Is it worn/wetted, and disconnected from the phone app?")
        return 1

    print(f"Found {device.name}  [{device.address}]  - connecting...")

    samples = []          # (x, y, z) tuples, mG
    frames = 0
    first_t = None

    def on_data(_char, data):
        nonlocal frames, first_t
        out = parse_acc_frame(bytes(data), fs=rate)
        frames += 1
        if first_t is None:
            first_t = out["t_last_ns"]
        samples.extend(out["samples"])

    cp_replies = asyncio.Queue()

    def on_control(_char, data):
        cp_replies.put_nowait(bytes(data))

    async with BleakClient(device) as client:
        print(f"Connected. MTU={client.mtu_size}")

        # The H10 acks a start request with an *indication* on the control point
        # (a 0xF0... frame), NOT via a read - reading the characteristic returns
        # the feature flags instead. So subscribe to the control point before
        # writing, and capture the indication that follows.
        await client.start_notify(PMD_CONTROL_UUID, on_control)
        await client.start_notify(PMD_DATA_UUID, on_data)

        cmd = build_acc_start_command(sample_rate=rate, resolution=16, range_g=range_g)
        print(f"Starting ACC stream {rate} Hz / {range_g} G  -> CP write {cmd.hex()}")
        await client.write_gatt_char(PMD_CONTROL_UUID, cmd, response=True)

        # Wait for the start acknowledgement indication and confirm it succeeded.
        try:
            reply = await asyncio.wait_for(cp_replies.get(), timeout=2.0)
            ok, op, mt, err = parse_control_point_response(reply)
            print(f"Control-point ack {reply.hex()}  -> ok={ok} err={err}")
        except asyncio.TimeoutError:
            print("(no control-point ack indication within 2s)")

        print(f"Streaming for {seconds}s ... (Ctrl-C to stop early)")
        t0 = time.monotonic()
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            pass
        elapsed = time.monotonic() - t0

        await client.write_gatt_char(PMD_CONTROL_UUID, build_acc_stop_command(), response=True)
        await client.stop_notify(PMD_DATA_UUID)
        await client.stop_notify(PMD_CONTROL_UUID)

    n = len(samples)
    print("\n--- summary ---")
    print(f"frames:  {frames}")
    print(f"samples: {n}")
    if n:
        rate_meas = n / elapsed if elapsed else 0
        print(f"elapsed: {elapsed:.2f}s   measured rate: {rate_meas:.1f} Hz (expected ~{rate})")
        print("first 3 samples (mG):", samples[:3])
        print("last  3 samples (mG):", samples[-3:])
        # Sanity: magnitude of a still-ish strap should be near 1000 mG (gravity).
        x, y, z = samples[-1]
        mag = (x * x + y * y + z * z) ** 0.5
        print(f"last-sample magnitude: {mag:.0f} mG  (gravity ~1000 mG when still)")
    else:
        print("No samples decoded. Check that notifications were enabled before the start write.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Test the Polar H10 PMD ACC protocol over Bleak.")
    p.add_argument("--rate", type=int, default=200, choices=[25, 50, 100, 200], help="sample rate (Hz)")
    p.add_argument("--range", type=int, default=8, choices=[2, 4, 8], dest="range_g", help="range (G)")
    p.add_argument("--seconds", type=float, default=5.0, help="how long to stream")
    p.add_argument("--scan-timeout", type=float, default=10.0, help="scan timeout (s)")
    args = p.parse_args()

    try:
        rc = asyncio.run(run(args.rate, args.range_g, args.seconds, args.scan_timeout))
    except KeyboardInterrupt:
        rc = 0
    sys.exit(rc)


if __name__ == "__main__":
    main()
