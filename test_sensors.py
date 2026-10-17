"""
Tests for sensors.RaspberryPiBackend's firmware ARM clock reading (Amendment A3).

    python3 -m unittest -v test_sensors

A fake vcgencmd script stands in for the real one (POSIX only); no hardware.
"""

import os
import stat
import tempfile
import time
import unittest

import sensors

FAKE = r"""#!/bin/sh
d=$(dirname "$0")
case "$1 $2" in
  "measure_clock arm") cat "$d/arm" ;;
  "get_throttled "*) echo "throttled=0x80008" ;;
  *) exit 1 ;;
esac
"""

HANG = r"""#!/bin/sh
d=$(dirname "$0")
trap 'echo term >> "$d/signals"; exit 0' TERM
while :; do sleep 0.1; done
"""


def _script(d, body):
    p = os.path.join(d, "vcgencmd")
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, stat.S_IRWXU)
    return p


@unittest.skipIf(os.name != "posix", "fake vcgencmd is a POSIX shell script")
class ArmClock(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sensors_test_")
        self.b = sensors.RaspberryPiBackend()
        self.b.vcgencmd = _script(self.d, FAKE)

    def arm(self, text):
        with open(os.path.join(self.d, "arm"), "w") as f:
            f.write(text)

    def test_parsed_in_mhz(self):
        self.arm("frequency(48)=1629439488\n")
        self.assertAlmostEqual(self.b._arm_clock(), 1629.439488)

    def test_garbage_or_zero_is_unreadable(self):
        for text in ("", "frequency(48)=\n", "error=1 error_msg=\"x\"\n",
                     "frequency(48)=0\n", "frequency(48)=-5\n"):
            with self.subTest(text=text):
                self.arm(text)
                self.assertIsNone(self.b._arm_clock())

    def test_off_by_default_and_on_when_asked(self):
        self.arm("frequency(48)=1800457088\n")
        self.assertFalse(self.b.record_arm_clock)
        self.assertNotIn("arm_clock_mhz", self.b.read())   # other experiments unchanged
        self.b.record_arm_clock = True
        r = self.b.read()
        self.assertAlmostEqual(r["arm_clock_mhz"], 1800.457088)
        self.assertEqual(r["throttled"], 0x80008)

    def test_missing_vcgencmd_is_unreadable(self):
        self.b.vcgencmd = None
        self.b.record_arm_clock = True
        self.assertIsNone(self.b.read()["arm_clock_mhz"])

    def test_hung_vcgencmd_gets_one_sigterm_never_sigkill(self):
        self.b.vcgencmd = _script(self.d, HANG)
        t0 = time.monotonic()
        self.assertIsNone(self.b._arm_clock())
        self.assertLess(time.monotonic() - t0, 4.5)
        # dash runs the TERM trap only after its current `sleep 0.1`; on a busy
        # machine that can take longer than the 1 s grace, so wait for the marker.
        p = os.path.join(self.d, "signals")
        end = time.monotonic() + 5.0
        while not os.path.exists(p) and time.monotonic() < end:
            time.sleep(0.05)
        time.sleep(0.3)                       # a second signal would add a line
        with open(p) as f:
            self.assertEqual(f.read().split(), ["term"])


if __name__ == "__main__":
    unittest.main()
