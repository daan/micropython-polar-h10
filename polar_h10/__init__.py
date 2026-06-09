"""
polar_h10 - read the Polar H10's PMD accelerometer stream on a microcontroller.

This package is deliberately split so the same code develops on a laptop and
deploys to an ESP32 unchanged:

    polar_h10.pmd      - transport-independent protocol core. Imports NOTHING
                         platform-specific; runs byte-for-byte the same under
                         CPython (bleak) and MicroPython (aioble).
    polar_h10.central  - aioble BLE transport. MicroPython-only (imports aioble),
                         so it is imported explicitly, never from here.

Only the pure core is re-exported below, which is why `import polar_h10` works
on a desktop where `aioble` does not exist. For the on-device transport do:

    from polar_h10.central import find_h10, PolarH10
"""

from .pmd import (
    PMD_SERVICE_UUID,
    PMD_CONTROL_UUID,
    PMD_DATA_UUID,
    HR_SERVICE_UUID,
    HR_CHAR_UUID,
    build_acc_start_command,
    build_acc_stop_command,
    parse_control_point_response,
    parse_acc_frame,
)

__version__ = "0.1.0"
