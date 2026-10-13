"""
Regression tests for exp1_thermal.py: the P4 verdict and cooling termination.

    python3 -m unittest -v test_exp1_thermal

Pure-logic tests plus end-to-end runs of main() on the MOCK backend, which is
simulated and sleep-based (no CPU load). No hardware load test is run here.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import exp1_thermal as e1

FULL_LOAD = {"per_core_busy": [1.0, 0.999, 0.999, 0.999], "avg_cores_busy": 3.997}
ONE_CORE = {"per_core_busy": [1.0, 0.0, 0.0, 0.0], "avg_cores_busy": 1.0}
NO_KNEE = {"knee_c": None, "f_max": 1800.0, "f_min": 1800.0, "depth": 0.0,
           "throttled_samples": 0}
KNEE = {"knee_c": 80.2, "f_max": 1800.0, "f_min": 1500.0, "depth": 1 / 6,
        "throttled_samples": 40}
SHALLOW_KNEE = {**KNEE, "depth": 0.01}
SAFETY = "max_temp: 75.5 C >= 75.0 C"     # exact reason in results/exp1_4core
N = e1.COOL_CONSECUTIVE


def _text(lines):
    return "\n".join(lines)


class SafetyStopIsNeverP4Fail(unittest.TestCase):

    def assertNoFailureVerdict(self, lines):
        t = _text(lines)
        for bad in ("P4 FAIL", "KILL", "dead"):
            self.assertNotIn(bad, t)

    def test_exp1_4core_case(self):
        # Full four-core load, no knee, stopped by --max-temp 75.
        status, lines = e1.p4_verdict(NO_KNEE, SAFETY, FULL_LOAD, is_mock=False)
        self.assertEqual(status, "SAFETY_STOPPED")
        self.assertEqual(lines[0], e1.SAFETY_STOP_LABEL)
        self.assertEqual(e1.SAFETY_STOP_LABEL,
                         "SAFETY-STOPPED: INCONCLUSIVE FOR KNEE DETECTION")
        self.assertNoFailureVerdict(lines)

    def test_safety_stop_wins_for_every_knee_and_load_combination(self):
        for knee in (NO_KNEE, KNEE, SHALLOW_KNEE, None):
            for util in (FULL_LOAD, ONE_CORE, None):
                for is_mock in (False, True):
                    with self.subTest(knee=knee and knee["knee_c"], util=util,
                                      is_mock=is_mock):
                        status, lines = e1.p4_verdict(knee, SAFETY, util, is_mock)
                        self.assertEqual(status, "SAFETY_STOPPED")
                        self.assertEqual(lines[0], e1.SAFETY_STOP_LABEL)
                        self.assertNoFailureVerdict(lines)

    def test_worker_death_abort_is_inconclusive_too(self):
        status, lines = e1.p4_verdict(
            NO_KNEE, "worker(s) exited early: ['heat-2']", FULL_LOAD, False)
        self.assertEqual(status, "ABORTED")
        self.assertIn("INCONCLUSIVE FOR KNEE DETECTION", lines[0])
        self.assertNoFailureVerdict(lines)

    def test_control_same_data_without_stop_is_p4_fail(self):
        # Proves the guard is not vacuous: identical data, heating not stopped.
        status, lines = e1.p4_verdict(NO_KNEE, None, FULL_LOAD, is_mock=False)
        self.assertEqual(status, "P4_FAIL")
        self.assertIn("P4 FAIL", _text(lines))

    def test_partial_load_without_stop_is_inconclusive(self):
        status, lines = e1.p4_verdict(NO_KNEE, None, ONE_CORE, is_mock=False)
        self.assertEqual(status, "PARTIAL_LOAD")
        self.assertNotIn("P4 FAIL", _text(lines))


class CoolingNeedsConsecutiveReadings(unittest.TestCase):
    BASE = 48.6

    def feed(self, readings, needed=N):
        """Index of the reading that ends cooling, or None if it never ends."""
        term = e1.CoolingTerminator(self.BASE, needed=needed)
        for i, t in enumerate(readings):
            if term.update(t):
                return i
        return None

    def test_one_qualifying_sample_does_not_end_cooling(self):
        # The exp1_4core failure mode: a single transient reading in range,
        # surrounded by warmer ones.
        readings = [55.0, 53.6, self.BASE + 1.0, 53.6, 52.6] + [52.1] * 50
        self.assertIsNone(self.feed(readings))

    def test_isolated_qualifying_samples_never_accumulate(self):
        self.assertIsNone(self.feed([self.BASE, 52.0] * 100))

    def test_streak_one_short_then_broken_does_not_end(self):
        self.assertIsNone(self.feed([self.BASE] * (N - 1) + [55.0]))

    def test_missing_reading_resets_streak(self):
        self.assertIsNone(self.feed([self.BASE] * (N - 1) + [None]
                                    + [self.BASE] * (N - 1)))

    def test_ends_exactly_at_nth_consecutive_reading(self):
        readings = [55.0] * 3 + [self.BASE + 0.5] * N + [self.BASE] * 5
        self.assertEqual(self.feed(readings), 3 + N - 1)

    def test_tolerance_is_one_degree_either_side(self):
        self.assertIsNotNone(self.feed([self.BASE + 1.0] * N))
        self.assertIsNotNone(self.feed([self.BASE - 1.0] * N))
        self.assertIsNone(self.feed([self.BASE + 1.1] * N))
        self.assertIsNone(self.feed([self.BASE - 1.1] * N))

    def test_single_sample_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            e1.CoolingTerminator(self.BASE, needed=1)

    def test_no_baseline_never_ends_early(self):
        term = e1.CoolingTerminator(None)
        self.assertFalse(any(term.update(t) for t in [48.6] * 100))


class EndToEndOnMockBackend(unittest.TestCase):
    """main() on the simulated mock device (sleep-based, no CPU load)."""

    def run_main(self, *extra):
        out = tempfile.mkdtemp(prefix="exp1_test_")
        argv = ["exp1_thermal.py", "--backend", "mock", "--outdir", out,
                "--idle-window-s", "0.5", *extra]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
            e1.main()
        with open(os.path.join(out, "exp1.meta.json")) as f:
            return buf.getvalue(), json.load(f)

    def test_safety_stopped_run_reports_inconclusive_not_p4_fail(self):
        # Mock starts at 25 C, knee at 80 C: a 27 C limit stops heating
        # long before any knee, which is the exp1_4core shape.
        stdout, meta = self.run_main("--minutes", "0.5", "--max-temp", "27",
                                     "--cool-minutes", "0.05")
        self.assertTrue(meta["abort_reason"].startswith("max_temp"))
        self.assertEqual(meta["p4_status"], "SAFETY_STOPPED")
        self.assertIn(e1.SAFETY_STOP_LABEL, stdout)
        self.assertNotIn("P4 FAIL", stdout)
        self.assertNotIn("TDAS is dead", stdout)

    def test_control_unstopped_run_without_knee_still_reports_p4_fail(self):
        stdout, meta = self.run_main("--minutes", "0.05", "--cool-minutes", "0.05")
        self.assertIsNone(meta["abort_reason"])
        self.assertEqual(meta["p4_status"], "P4_FAIL")
        self.assertIn("P4 FAIL", stdout)

    def test_cooling_ends_only_after_n_consecutive_readings(self):
        # Limit 25.8 C is within 1 C of the 25 C baseline, so EVERY cooling
        # reading qualifies; the earliest legal end is the Nth reading.
        stdout, meta = self.run_main("--minutes", "0.5", "--max-temp", "25.8",
                                     "--cool-minutes", "1")
        self.assertEqual(meta["cool_end_reason"], "baseline_reached")
        self.assertGreaterEqual(meta["cool_wall_s"], 0.5 * N * 0.95)
        self.assertEqual(meta["cool_consecutive"], N)
        self.assertEqual(meta["idle_temp_c"], meta["idle_window"]["baseline_c"])


if __name__ == "__main__":
    unittest.main()
