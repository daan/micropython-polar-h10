"""
polar_pmd.py  -  Transport-independent Polar H10 PMD (Polar Measurement Data) protocol.

This module contains ONLY the wire-format logic for the H10 accelerometer stream:
building the control-point command, and decoding the delta-compressed data frames.
It has NO dependency on any BLE library, so the exact same functions run under
bleak (CPython, laptop) and aioble (MicroPython, ESP32). The only thing that
changes between platforms is the transport scaffolding that calls these functions.

Wire format reconstructed from Polar's public PMD specification and cross-checked
against community decoders. Values are in milli-g (mG); divide by 1000.0 for g.

----------------------------------------------------------------------------------
GATT surface (PMD service)
    Service        FB005C80-02E7-F387-1CAD-8ACD2D8DF0C8
    Control Point  FB005C81-02E7-F387-1CAD-8ACD2D8DF0C8   (write + indicate/read)
    Data           FB005C82-02E7-F387-1CAD-8ACD2D8DF0C8   (notify)

Handshake
    1. Enable notifications on the Data characteristic.
    2. Write an ACC-start command (below) to the Control Point.
    3. Control Point replies starting with 0xF0; byte[3]==0x00 means success.
    4. Frames arrive as notifications on the Data characteristic until you
       write the stop command.

Control-point command layout (start)
    [0x02]              op code = request measurement start
    [0x02]              measurement type = ACC
    then zero or more setting blocks, each:
        [setting_type]  0x00=SAMPLE_RATE 0x01=RESOLUTION 0x02=RANGE 0x04=CHANNELS
        [array_length]  number of uint16 values that follow (usually 0x01)
        [value ...]     little-endian uint16 each

Data-frame layout (notification)
    [0]      measurement type (0x02 = ACC)
    [1..8]   timestamp, uint64 little-endian, nanoseconds, of the LAST sample
    [9]      frame type; bit7 (0x80) set => delta-compressed frame
    [10..]   payload:
                reference sample: <channels> signed ints, each <resolution/8> bytes
                then repeating delta groups, each byte-aligned:
                    [delta_bits]     bit width of every delta value in this group
                    [sample_count]   number of samples in this group
                    packed deltas    sample_count * channels values, delta_bits
                                     each, LSB-first bit packing, two's complement
             Each sample = previous sample + per-channel delta (cumulative).
----------------------------------------------------------------------------------
"""

# --- GATT UUIDs (strings; wrap with your stack's UUID type at the call site) ---
PMD_SERVICE_UUID = "fb005c80-02e7-f387-1cad-8acd2d8df0c8"
PMD_CONTROL_UUID = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA_UUID    = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"

# Standard heart-rate service (kept here for convenience; not used by ACC path)
HR_SERVICE_UUID  = "0000180d-0000-1000-8000-00805f9b34fb"
HR_CHAR_UUID     = "00002a37-0000-1000-8000-00805f9b34fb"

# Control-point op codes
OP_GET_SETTINGS = 0x01
OP_START        = 0x02
OP_STOP         = 0x03

# Measurement types
MEAS_ECG = 0x00
MEAS_ACC = 0x02

# Setting types
SET_SAMPLE_RATE = 0x00
SET_RESOLUTION  = 0x01
SET_RANGE       = 0x02
SET_CHANNELS    = 0x04

# Control-point response marker and success code
CP_RESPONSE = 0xF0
CP_SUCCESS  = 0x00

ACC_CHANNELS = 3  # x, y, z


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------
def _setting_block(setting_type, value):
    """One TLV setting block: type, array_length=1, then uint16 little-endian."""
    return bytes((setting_type, 0x01, value & 0xFF, (value >> 8) & 0xFF))


def build_acc_start_command(sample_rate=200, resolution=16, range_g=8):
    """Build the Control-Point write that starts the H10 accelerometer stream.

    sample_rate: 25 | 50 | 100 | 200  (Hz)
    resolution : 16                    (bits; the H10 ACC uses 16)
    range_g    : 2 | 4 | 8             (G)

    Example (200 Hz / 16-bit / 8 G):
        02 02 00 01 C8 00 01 01 10 00 02 01 08 00
    """
    cmd = bytearray((OP_START, MEAS_ACC))
    cmd += _setting_block(SET_SAMPLE_RATE, sample_rate)
    cmd += _setting_block(SET_RESOLUTION, resolution)
    cmd += _setting_block(SET_RANGE, range_g)
    return bytes(cmd)


def build_acc_stop_command():
    """Stop the accelerometer stream."""
    return bytes((OP_STOP, MEAS_ACC))


def parse_control_point_response(data):
    """Interpret a Control-Point reply. Returns (ok, op_code, meas_type, error_code).

    A well-formed reply begins with 0xF0; error_code 0x00 means success.
    """
    if not data or data[0] != CP_RESPONSE or len(data) < 4:
        return (False, None, None, None)
    op_code, meas_type, error_code = data[1], data[2], data[3]
    return (error_code == CP_SUCCESS, op_code, meas_type, error_code)


# ---------------------------------------------------------------------------
# Standard Heart Rate Measurement (0x2A37) - plain GATT, no PMD, no encryption.
# ---------------------------------------------------------------------------
def parse_hr_measurement(data):
    """Decode a standard BLE Heart Rate Measurement notification (char 0x2A37).

    Layout: a flags byte, then the HR value (uint8 or uint16 per flag bit0),
    optional energy-expended (uint16), then zero or more RR-intervals (uint16,
    units of 1/1024 s).

    Returns a dict:
        {
          "bpm": int,                 # heart rate, beats per minute
          "contact": bool | None,     # skin-contact detected, if supported
          "rr_ms": [float, ...],      # beat-to-beat (RR) intervals in ms
        }
    """
    if not data:
        raise ValueError("empty HR measurement")
    flags = data[0]
    hr16 = flags & 0x01
    contact_supported = flags & 0x04
    contact_detected = flags & 0x02
    energy_present = flags & 0x08
    rr_present = flags & 0x10

    off = 1
    if hr16:
        bpm = data[off] | (data[off + 1] << 8)
        off += 2
    else:
        bpm = data[off]
        off += 1

    if energy_present:
        off += 2  # skip energy-expended uint16

    rr_ms = []
    if rr_present:
        while off + 1 < len(data):
            raw = data[off] | (data[off + 1] << 8)
            off += 2
            rr_ms.append(raw * 1000.0 / 1024.0)

    contact = bool(contact_detected) if contact_supported else None
    return {"bpm": bpm, "contact": contact, "rr_ms": rr_ms}


# ---------------------------------------------------------------------------
# Low-level helpers (MicroPython-safe: no int.from_bytes(signed=...) )
# ---------------------------------------------------------------------------
def _signed(value, nbits):
    """Two's-complement interpret an nbits unsigned int as signed."""
    if value & (1 << (nbits - 1)):
        value -= (1 << nbits)
    return value


def _read_signed_le(buf, offset, nbytes):
    """Read a little-endian signed integer of nbytes from buf at offset."""
    v = 0
    for i in range(nbytes):
        v |= buf[offset + i] << (8 * i)
    return _signed(v, nbytes * 8)


class _BitReader:
    """LSB-first bit reader over a bytes-like slice, for one delta group."""

    __slots__ = ("buf", "bitpos")

    def __init__(self, buf):
        self.buf = buf
        self.bitpos = 0

    def read(self, nbits):
        """Read nbits as an unsigned int, LSB-first within and across bytes."""
        value = 0
        for i in range(nbits):
            byte_index = (self.bitpos + i) >> 3
            bit_index = (self.bitpos + i) & 7
            bit = (self.buf[byte_index] >> bit_index) & 1
            value |= bit << i
        self.bitpos += nbits
        return value

    def read_signed(self, nbits):
        return _signed(self.read(nbits), nbits)


# ---------------------------------------------------------------------------
# Frame decoding
# ---------------------------------------------------------------------------
def parse_acc_frame(data, resolution=16, channels=ACC_CHANNELS, fs=None):
    """Decode one PMD ACC data notification into samples.

    data       : the full notification payload (bytes/bytearray/memoryview)
    resolution : bits per reference channel value (16 for H10 ACC)
    channels   : 3 for ACC
    fs         : sample rate in Hz; if given, per-sample timestamps (ns) are
                 reconstructed from the header timestamp of the LAST sample.

    Returns a dict:
        {
          "meas_type": int,
          "t_last_ns": int,          # device timestamp of final sample
          "samples":   [(x, y, z), ...],   # int, mG
          "timestamps_ns": [...] | None,   # one per sample if fs given
        }
    """
    if len(data) < 10:
        raise ValueError("frame too short for PMD header")

    meas_type = data[0]
    t_last_ns = 0
    for i in range(8):
        t_last_ns |= data[1 + i] << (8 * i)
    frame_type = data[9]
    payload = memoryview(data)[10:]

    is_delta = bool(frame_type & 0x80)
    samples = []

    if is_delta:
        ref_bytes = resolution // 8
        ref = [0] * channels
        off = 0
        for c in range(channels):
            ref[c] = _read_signed_le(payload, off, ref_bytes)
            off += ref_bytes
        samples.append(tuple(ref))

        # Repeating, byte-aligned delta groups until payload is exhausted.
        while off + 2 <= len(payload):
            delta_bits = payload[off]
            sample_count = payload[off + 1]
            off += 2
            if delta_bits == 0 or sample_count == 0:
                break
            group_bits = delta_bits * channels * sample_count
            group_bytes = (group_bits + 7) >> 3
            reader = _BitReader(payload[off:off + group_bytes])
            for _ in range(sample_count):
                for c in range(channels):
                    ref[c] += reader.read_signed(delta_bits)
                samples.append(tuple(ref))
            off += group_bytes  # next group starts byte-aligned
    else:
        # Non-delta ("raw") ACC frame: contiguous signed int16 triples.
        step = resolution // 8
        n = len(payload) // (step * channels)
        off = 0
        for _ in range(n):
            sample = []
            for c in range(channels):
                sample.append(_read_signed_le(payload, off, step))
                off += step
            samples.append(tuple(sample))

    timestamps_ns = None
    if fs:
        period_ns = int(1_000_000_000 // fs)
        n = len(samples)
        timestamps_ns = [t_last_ns - (n - 1 - i) * period_ns for i in range(n)]

    return {
        "meas_type": meas_type,
        "t_last_ns": t_last_ns,
        "samples": samples,
        "timestamps_ns": timestamps_ns,
    }
# Wire-format regression tests for this module live in tests/test_pmd.py and run
# under CPython (and, optionally, the MicroPython unix port) with no BLE needed.
