"""
fwclock.py - the firmware's measured ARM clock, read in-process (Amendment A4).

On the Pi 4, Linux scaling_cur_freq does not show firmware throttling (A3), and
`vcgencmd measure_clock arm` starts a process. A process start while all four
cores run fixed-work jobs is exactly what stalled P4b (diag-stalls-20261017-02).
This reads the same value through the firmware mailbox (/dev/vcio, property tag
"get measured clock rate", clock id ARM) with one ioctl: no process, about
0.02 ms of CPU and about 1 ms of waiting for the firmware per read
(exp1-rerun-prep/a3/diag/a4_vcio_feasibility_3_cpu.log).

/dev/vcio is root-only by default; the Pi has the udev rule
/etc/udev/rules.d/99-tdas-vcio.rules (KERNEL=="vcio", GROUP="video", MODE="0660").
The reader only sends this read-only property request.
"""

from __future__ import annotations

import array
import fcntl
import os

VCIO = "/dev/vcio"
IOCTL_MBOX_PROPERTY = (3 << 30) | (8 << 16) | (100 << 8) | 0   # _IOWR(100, 0, char *), 64-bit
TAG_GET_MEASURED_CLOCK = 0x00030047
CLOCK_ARM = 3
RESPONSE_OK = 0x80000000


class ArmClockMailbox:
    """Measured ARM clock in MHz via the firmware mailbox. read() returns None
    on any failure (never raises), so a telemetry thread can count misses."""

    def __init__(self, path=VCIO, ioctl=fcntl.ioctl, opener=os.open, closer=os.close):
        self._ioctl, self._close = ioctl, closer
        self.fd = opener(path, os.O_RDWR)             # raises if unavailable (caller decides)

    def read(self):
        buf = array.array("I", [8 * 4, 0, TAG_GET_MEASURED_CLOCK, 8, 0, CLOCK_ARM, 0, 0])
        try:
            self._ioctl(self.fd, IOCTL_MBOX_PROPERTY, buf, True)
        except OSError:
            return None
        if buf[1] != RESPONSE_OK or not buf[4] & RESPONSE_OK or buf[5] != CLOCK_ARM or buf[6] <= 0:
            return None
        return buf[6] / 1e6

    def close(self):
        if self.fd is not None:
            self._close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
