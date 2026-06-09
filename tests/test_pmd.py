"""
Wire-format regression tests for the transport-independent protocol core.

These run under CPython with `uv run pytest` and need no BLE hardware. They are
plain asserts (no pytest-only features) so the same file can also be run under
the MicroPython unix port for higher-fidelity checking:

    micropython tests/test_pmd.py
"""

import sys
from pathlib import Path

# Make the repo root importable when run directly (e.g. under MicroPython unix).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polar_h10.pmd import (
    MEAS_ACC,
    OP_START,
    build_acc_start_command,
    parse_acc_frame,
    parse_control_point_response,
    parse_hr_measurement,
)


def test_acc_start_command_200_16_8():
    cmd = build_acc_start_command(200, 16, 8)
    expected = bytes.fromhex("02020001c8000101100002010800")
    assert cmd == expected, cmd.hex()


def test_acc_start_command_50hz_4g():
    # 50 Hz -> 0x32 0x00, 4 G -> 0x04 0x00; each setting block is type,len=01,u16.
    cmd = build_acc_start_command(50, 16, 4)
    assert cmd == bytes.fromhex("0202000132000101100002010400"), cmd.hex()


def test_control_point_response_success():
    ok, op, mt, err = parse_control_point_response(bytes.fromhex("F0020200"))
    assert ok and op == OP_START and mt == MEAS_ACC and err == 0


def test_control_point_response_error():
    ok, op, mt, err = parse_control_point_response(bytes.fromhex("F0020205"))
    assert not ok and err == 5


def test_control_point_response_malformed():
    assert parse_control_point_response(b"") == (False, None, None, None)
    assert parse_control_point_response(bytes.fromhex("AB02")) == (False, None, None, None)


def _delta_frame():
    """Synthetic delta frame: ref (100, -50, 990), one group of 2 samples,
    4-bit deltas, every delta = +1 per channel."""
    header = bytes([MEAS_ACC]) + (1234567).to_bytes(8, "little") + bytes([0x81])
    ref = (
        (100).to_bytes(2, "little")
        + ((-50) & 0xFFFF).to_bytes(2, "little")
        + (990).to_bytes(2, "little")
    )
    # 2 samples * 3 channels * 4 bits = 24 bits = 3 bytes; each nibble = 0x1.
    group = bytes([4, 2]) + bytes([0x11, 0x11, 0x11])
    return header + ref + group


def test_delta_decode():
    out = parse_acc_frame(_delta_frame(), fs=200)
    assert out["samples"] == [(100, -50, 990), (101, -49, 991), (102, -48, 992)]
    assert out["meas_type"] == MEAS_ACC
    assert out["t_last_ns"] == 1234567


def test_delta_timestamps_align_to_last_sample():
    out = parse_acc_frame(_delta_frame(), fs=200)
    ts = out["timestamps_ns"]
    assert len(ts) == len(out["samples"])
    assert ts[-1] == out["t_last_ns"]          # last sample carries the header time
    assert ts[1] - ts[0] == 1_000_000_000 // 200  # 5 ms period at 200 Hz


def test_raw_frame_decode():
    # Non-delta frame (bit7 clear): contiguous int16 triples.
    header = bytes([MEAS_ACC]) + (0).to_bytes(8, "little") + bytes([0x00])
    s1 = (10).to_bytes(2, "little") + (20).to_bytes(2, "little") + (30).to_bytes(2, "little")
    s2 = ((-10) & 0xFFFF).to_bytes(2, "little") + (5).to_bytes(2, "little") + (0).to_bytes(2, "little")
    out = parse_acc_frame(header + s1 + s2)
    assert out["samples"] == [(10, 20, 30), (-10, 5, 0)]


def test_hr_uint8_no_rr():
    # flags=0x00 (uint8 HR, no RR), HR=72
    out = parse_hr_measurement(bytes([0x00, 72]))
    assert out["bpm"] == 72 and out["rr_ms"] == [] and out["contact"] is None


def test_hr_uint8_with_contact_and_rr():
    # flags: contact supported+detected (0x06) + RR present (0x10) = 0x16, HR=60,
    # one RR = 1024 -> exactly 1000 ms.
    out = parse_hr_measurement(bytes([0x16, 60, 0x00, 0x04]))
    assert out["bpm"] == 60
    assert out["contact"] is True
    assert len(out["rr_ms"]) == 1 and abs(out["rr_ms"][0] - 1000.0) < 1e-6


def test_hr_uint16():
    # flags=0x01 (uint16 HR), HR=300 -> 0x012C little-endian
    out = parse_hr_measurement(bytes([0x01, 0x2C, 0x01]))
    assert out["bpm"] == 300


def test_hr_energy_then_rr():
    # flags: uint8 HR + energy(0x08) + RR(0x10) = 0x18; HR=80; energy=2 bytes; RR=512 -> ~500ms
    out = parse_hr_measurement(bytes([0x18, 80, 0xFF, 0x00, 0x00, 0x02]))
    assert out["bpm"] == 80
    assert len(out["rr_ms"]) == 1 and abs(out["rr_ms"][0] - 500.0) < 1e-6


def test_short_frame_raises():
    try:
        parse_acc_frame(b"\x02\x00\x00")
    except ValueError:
        return
    raise AssertionError("expected ValueError for short frame")


if __name__ == "__main__":
    # Allow running under the MicroPython unix port (no pytest there).
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all tests passed")
