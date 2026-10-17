"""
Tests for fwclock.py with a fake ioctl (no /dev/vcio needed).

    python3 -m unittest -v test_fwclock
"""

import unittest

import fwclock as fc


def fake_ioctl(rate_hz, status=fc.RESPONSE_OK, tag_status=fc.RESPONSE_OK | 8, clock=fc.CLOCK_ARM,
               raise_=None, calls=None):
    def ioctl(fd, req, buf, mutate):
        if calls is not None:
            calls.append((fd, req, list(buf), mutate))
        if raise_:
            raise raise_
        buf[1], buf[4], buf[5], buf[6] = status, tag_status, clock, rate_hz
        return 0
    return ioctl


def reader(**kw):
    closed = []
    r = fc.ArmClockMailbox(ioctl=fake_ioctl(**kw), opener=lambda p, f: 7,
                           closer=closed.append)
    return r, closed


class ArmClockMailboxTests(unittest.TestCase):
    def test_request_layout_and_mhz(self):
        calls = []
        r = fc.ArmClockMailbox(ioctl=fake_ioctl(1629439488, calls=calls), opener=lambda p, f: 7,
                               closer=lambda fd: None)
        self.assertAlmostEqual(r.read(), 1629.439488)
        fd, req, buf, mutate = calls[0]
        self.assertEqual((fd, req, mutate), (7, 0xC0086400, True))
        self.assertEqual(buf, [32, 0, 0x00030047, 8, 0, 3, 0, 0])   # size, request, tag, value buf, ARM

    def test_failed_responses_are_none_not_exceptions(self):
        for kw in ({"status": 0x80000001}, {"tag_status": 0}, {"clock": 4}, {"rate_hz": 0},
                   {"raise_": OSError(5, "EIO")}):
            with self.subTest(kw=kw):
                r, _ = reader(**{"rate_hz": 1800000000, **kw})
                self.assertIsNone(r.read())

    def test_close_is_idempotent_and_context_manager_closes(self):
        r, closed = reader(rate_hz=1800000000)
        with r:
            self.assertAlmostEqual(r.read(), 1800.0)
        r.close()
        self.assertEqual(closed, [7])

    def test_open_failure_raises(self):
        def no_open(path, flags):
            raise PermissionError(13, "Permission denied")
        with self.assertRaises(PermissionError):
            fc.ArmClockMailbox(opener=no_open)


if __name__ == "__main__":
    unittest.main()
