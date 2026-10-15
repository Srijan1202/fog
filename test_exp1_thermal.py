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


PROCEED = "Proceed to Experiment 2"


def run_meta(**over):
    """Metadata of a completed, valid four-core run with a knee; override fields."""
    m = {"mock": False, "knee": KNEE, "abort_reason": None, "p4_status": "NO_KILL",
         "cpu_util_heating": FULL_LOAD, "heat_minutes": 15.0, "heat_wall_s": 900.3,
         "heat_summary": {"throttled_or": "0x0"}}
    m.update(over)
    return m


class EndOfRunMessageFollowsAnalyzeRule(unittest.TestCase):

    def test_valid_knee_recommends_proceeding(self):
        outcome, lines = e1.exp1_outcome(run_meta())
        self.assertEqual(outcome, "VALID_KNEE")
        self.assertEqual(lines[0], "VALID RUN, KNEE MEASURED at 80.2 C (depth 16.7%)")
        self.assertIn(PROCEED, _text(lines))

    def test_valid_completed_run_without_knee_reports_p4_fail(self):
        outcome, lines = e1.exp1_outcome(run_meta(knee=NO_KNEE, p4_status="P4_FAIL"))
        self.assertEqual(outcome, "VALID_NO_KNEE")
        self.assertIn("VALID COMPLETED RUN, NO KNEE", lines[0])
        self.assertIn("P4 FAIL", _text(lines))
        self.assertNotIn(PROCEED, _text(lines))

    def test_valid_shallow_knee_reports_weak(self):
        outcome, lines = e1.exp1_outcome(run_meta(knee=SHALLOW_KNEE))
        self.assertEqual(outcome, "VALID_KNEE_WEAK")
        self.assertIn("P1/P4 WEAK", _text(lines))
        self.assertNotIn(PROCEED, _text(lines))

    def test_partial_load_knee_does_not_proceed(self):
        outcome, lines = e1.exp1_outcome(
            run_meta(cpu_util_heating=ONE_CORE, p4_status="PARTIAL_LOAD"))
        self.assertEqual(outcome, "INCONCLUSIVE")
        self.assertIn("loaded only 1.00 of 4 cores", _text(lines))
        self.assertIn("Do not proceed", _text(lines))
        self.assertNotIn(PROCEED, _text(lines))

    def test_safety_stopped_knee_does_not_proceed(self):
        outcome, lines = e1.exp1_outcome(
            run_meta(abort_reason=SAFETY, p4_status="SAFETY_STOPPED"))
        self.assertEqual(outcome, "INCONCLUSIVE")
        self.assertEqual(lines[0], e1.SAFETY_STOP_LABEL)
        self.assertNotIn(PROCEED, _text(lines))
        self.assertNotIn("P4 FAIL", _text(lines))

    def test_aborted_run_does_not_proceed(self):
        outcome, lines = e1.exp1_outcome(
            run_meta(abort_reason="worker(s) exited early: ['heat-1']",
                     p4_status="ABORTED"))
        self.assertEqual(outcome, "INCONCLUSIVE")
        self.assertEqual(lines[0], "ABORTED: INCONCLUSIVE FOR KNEE DETECTION")
        self.assertNotIn(PROCEED, _text(lines))

    def test_other_invalid_runs_with_a_knee_never_proceed(self):
        for name, over in (("short heating", {"heat_wall_s": 300.0}),
                           ("under-voltage", {"heat_summary": {"throttled_or": "0x10005"}}),
                           ("no load record", {"cpu_util_heating": None}),
                           ("no frequency", {"knee": {**KNEE, "f_max": None}})):
            with self.subTest(case=name):
                outcome, lines = e1.exp1_outcome(run_meta(**over))
                self.assertEqual(outcome, "INCONCLUSIVE")
                self.assertNotIn(PROCEED, _text(lines))

    def test_message_proceeds_only_when_analyze_would_pass_g1(self):
        cases = [run_meta(), run_meta(knee=NO_KNEE), run_meta(knee=SHALLOW_KNEE),
                 run_meta(cpu_util_heating=ONE_CORE),
                 run_meta(abort_reason=SAFETY, p4_status="SAFETY_STOPPED"),
                 run_meta(heat_wall_s=118.2), run_meta(cpu_util_heating=None),
                 run_meta(heat_summary={"throttled_or": "0x1"})]
        for i, m in enumerate(cases):
            with self.subTest(case=i):
                passes = e1.analyze.classify_g1(m, None)[0] == "PASS"
                _, lines = e1.exp1_outcome(m)
                self.assertEqual(PROCEED in _text(lines),
                                 passes and m["knee"]["depth"] >= 0.05)

    def test_p4_status_is_partial_load_even_with_a_knee(self):
        status, lines = e1.p4_verdict(KNEE, None, ONE_CORE, is_mock=False)
        self.assertEqual(status, "PARTIAL_LOAD")
        self.assertIn("knee recorded under partial load", _text(lines))
        self.assertNotIn(PROCEED, _text(lines))


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
        self.assertNotIn(PROCEED, stdout)
        self.assertIn("Do not proceed on this run", stdout)
        self.assertEqual(meta["exp1_outcome"], "INCONCLUSIVE")

    def test_control_unstopped_run_without_knee_still_reports_p4_fail(self):
        stdout, meta = self.run_main("--minutes", "0.05", "--cool-minutes", "0.05")
        self.assertIsNone(meta["abort_reason"])
        self.assertEqual(meta["p4_status"], "P4_FAIL")
        self.assertIn("P4 FAIL", stdout)
        self.assertIn("VALID COMPLETED RUN, NO KNEE", stdout)
        self.assertEqual(meta["exp1_outcome"], "VALID_NO_KNEE")

    def test_cooling_ends_only_after_n_consecutive_readings(self):
        # Limit 25.8 C is within 1 C of the 25 C baseline, so EVERY cooling
        # reading qualifies; the earliest legal end is the Nth reading.
        stdout, meta = self.run_main("--minutes", "0.5", "--max-temp", "25.8",
                                     "--cool-minutes", "1")
        self.assertEqual(meta["cool_end_reason"], "baseline_reached")
        self.assertGreaterEqual(meta["cool_wall_s"], 0.5 * N * 0.95)
        self.assertEqual(meta["cool_consecutive"], N)
        self.assertEqual(meta["idle_temp_c"], meta["idle_window"]["baseline_c"])


def _ignore_term_worker():
    """Test double for HeatWorkers: ignores SIGTERM and never sees the stop event."""
    import signal as _s
    import time as _t
    _s.signal(_s.SIGTERM, _s.SIG_IGN)
    while True:
        _t.sleep(0.2)


class HeatWorkersNeverSigkill(unittest.TestCase):

    def test_term_ignoring_worker_is_reported_not_killed(self):
        import multiprocessing as mp
        import signal
        hw = e1.HeatWorkers.__new__(e1.HeatWorkers)
        ctx = mp.get_context("spawn")
        hw.go, hw.stop_evt, hw.q = ctx.Event(), ctx.Event(), ctx.Queue()
        hw.procs = [ctx.Process(target=_ignore_term_worker, daemon=True)]
        hw.reports, hw.forced, hw._stopped, hw.unstopped = {}, [], False, []
        hw.procs[0].start()
        try:
            report = hw.stop(timeout_s=0.5)
            self.assertTrue(hw.procs[0].is_alive())         # no SIGKILL was sent
            self.assertEqual(hw.unstopped, [hw.procs[0].pid])
            self.assertTrue(report[0]["unstopped"] and report[0]["forced_stop"])
        finally:                                            # test cleanup of the double only
            os.kill(hw.procs[0].pid, signal.SIGKILL)
            hw.procs[0].join(3)


class FakeGuard:
    """Stands in for Exp1Guard (software only). `lost_after`: raise MonitorLost on
    that raise_if_lost() call."""
    why, lost_after, made = None, None, []

    def __init__(self, root, run_id, **kw):
        self.root, self.run_id, self.calls = root, run_id, 0
        FakeGuard.made.append(self)

    def wait(self, timeout_s):
        return FakeGuard.why

    def raise_if_lost(self):
        self.calls += 1
        if FakeGuard.lost_after is not None and self.calls >= FakeGuard.lost_after:
            raise e1.mg.MonitorLost("watchdog lost: test")

    def describe(self):
        return {"fake_guard": True}


class FakeHardware:
    name = "fakehw"

    def __init__(self):
        self.reads = 0

    def read(self):
        self.reads += 1
        return {"t_wall": 0.0, "temp_c": 45.0, "freq_mhz": 1800.0, "throttled": 0}


class SupervisedRun(unittest.TestCase):
    """--require-monitor: mandatory on real hardware, fresh outdir, barrier before
    any load, and an interrupted run still writes exp1.meta.json (ABORTED)."""

    def setUp(self):
        FakeGuard.why, FakeGuard.lost_after, FakeGuard.made = None, None, []
        self.base = tempfile.mkdtemp(prefix="exp1_sup_")

    def call(self, argv, backend=None):
        buf = io.StringIO()
        patches = [mock.patch.object(sys, "argv", ["exp1_thermal.py", *argv]),
                   mock.patch.object(e1, "Exp1Guard", FakeGuard),
                   mock.patch.object(e1, "HeatWorkers",
                                     mock.Mock(side_effect=AssertionError("no load may start")))]
        if backend is not None:
            patches.append(mock.patch.object(e1.sensors, "get_backend", return_value=backend))
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(buf))
            rc = e1.main()
        return rc, buf.getvalue()

    def test_real_hardware_without_require_monitor_is_refused(self):
        hw, out = FakeHardware(), os.path.join(self.base, "r1")
        rc, text = self.call(["--workers", "4", "--outdir", out], backend=hw)
        self.assertEqual(rc, 2)
        self.assertIn("--require-monitor SECONDS is mandatory", text)
        self.assertFalse(os.path.exists(out))
        self.assertEqual((hw.reads, FakeGuard.made), (0, []))

    def test_barrier_refusal_writes_aborted_meta_and_starts_nothing(self):
        FakeGuard.why = "no verified watchdog within 1 s: MONITOR_OK not written"
        hw, out = FakeHardware(), os.path.join(self.base, "r2")
        rc, _ = self.call(["--workers", "4", "--outdir", out, "--require-monitor", "1"],
                          backend=hw)
        self.assertEqual(rc, 2)
        with open(os.path.join(out, "exp1.meta.json")) as f:
            meta = json.load(f)
        self.assertEqual(meta["p4_status"], "ABORTED")
        self.assertIn("refused: monitoring not established", meta["abort_reason"])
        self.assertEqual(hw.reads, 0)                        # no idle window, no load
        self.assertTrue(os.path.exists(os.path.join(out, "exp1.start.json")))
        self.assertEqual(e1.analyze.classify_g1(meta, None)[0], "INCONCLUSIVE")

    def test_existing_outdir_is_refused_and_untouched(self):
        out = os.path.join(self.base, "r3")
        os.makedirs(out)
        with open(os.path.join(out, "keep.txt"), "w") as f:
            f.write("precious")
        rc, text = self.call(["--workers", "4", "--outdir", out, "--require-monitor", "1"],
                             backend=FakeHardware())
        self.assertEqual(rc, 2)
        self.assertIn("refusing to overwrite", text)
        self.assertEqual(os.listdir(out), ["keep.txt"])

    def test_watchdog_lost_during_heating_saves_aborted_meta(self):
        # mock backend (simulated, no CPU load) under the barrier; the fake guard
        # reports the watchdog lost after a few checks inside the heating loop
        FakeGuard.lost_after = 20
        out = os.path.join(self.base, "r4")
        rc, text = self.call(["--backend", "mock", "--minutes", "0.5", "--cool-minutes", "0.05",
                              "--idle-window-s", "0.5", "--outdir", out,
                              "--require-monitor", "1"])
        self.assertEqual(rc, 130)
        with open(os.path.join(out, "exp1.meta.json")) as f:
            meta = json.load(f)
        self.assertEqual(meta["p4_status"], "ABORTED")
        self.assertIn("watchdog lost", meta["abort_reason"])
        self.assertEqual(meta["cool_end_reason"], "skipped: run interrupted")
        self.assertEqual(meta["monitor"], {"fake_guard": True})
        self.assertEqual(e1.analyze.classify_g1(meta, None)[0], "INCONCLUSIVE")
        self.assertTrue(os.path.exists(os.path.join(out, "exp1.telemetry.jsonl")))


class Exp1GuardIdentity(unittest.TestCase):
    """Exp1Guard accepts only tools/exp1_monitor.sh for this pid and outdir."""

    def problem(self, argv, exe="/usr/bin/bash", start=7):
        g = e1.Exp1Guard("results/exp1-x", "exp1-x", pid=123)
        with mock.patch.object(e1.mg, "proc_start", return_value=start), \
             mock.patch.object(e1.mg, "proc_argv", return_value=argv), \
             mock.patch.object(e1.mg, "proc_exe", return_value=exe):
            return g._watchdog_problem(99, 7, check_exe=True)

    def test_right_watchdog_accepted(self):
        self.assertIsNone(self.problem(["bash", "tools/exp1_monitor.sh", "123", "results/exp1-x"]))

    def test_validation_watchdog_refused(self):
        self.assertIn("does not match",
                      self.problem(["bash", "tools/validation_monitor.sh", "123", "results/exp1-x"]))

    def test_wrong_pid_outdir_exe_or_start_refused(self):
        self.assertIsNotNone(self.problem(["bash", "tools/exp1_monitor.sh", "124", "results/exp1-x"]))
        self.assertIsNotNone(self.problem(["bash", "tools/exp1_monitor.sh", "123", "results/other"]))
        self.assertIn("is not bash", self.problem(["bash", "tools/exp1_monitor.sh", "123",
                                                   "results/exp1-x"], exe="/usr/bin/python3.13"))
        self.assertIn("start time", self.problem(["bash", "tools/exp1_monitor.sh", "123",
                                                  "results/exp1-x"], start=8))


if __name__ == "__main__":
    unittest.main()
