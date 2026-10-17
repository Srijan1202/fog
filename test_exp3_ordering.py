"""
Regression tests for exp3_ordering.py: where the time-above-knee threshold
may come from, the workload-validation prerequisite, and refusal of an
invalid confirmatory run.

    python3 -m unittest -v test_exp3_ordering

No hardware load: a fake backend and a fake worker pool for the real-hardware
paths, the simulated mock backend (sleep-based) for end-to-end runs.
"""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import exp3_ordering as e3
import validate_workload as vw
import workload as wl
from test_analyze import (E1_4CORE, E1_KNEE, E1_KNEE_PARTIAL,
                          E1_KNEE_THEN_STOPPED, E2_PASS)

E2_KNEE = {**E2_PASS, "knee_c": 79.0}


def outdir_with(e1=None, e2=None):
    d = tempfile.mkdtemp(prefix="exp3_test_")
    for name, m in (("exp1", e1), ("exp2", e2)):
        if m is not None:
            with open(os.path.join(d, f"{name}.meta.json"), "w") as f:
                json.dump(m, f)
    return d


def threshold(e1, e2, is_mock=False, knee=None):
    d = outdir_with(e1, e2)
    return e3.resolve_threshold(os.path.join(d, "exp1.meta.json"),
                                os.path.join(d, "exp2.meta.json"), is_mock, knee)


class ThresholdComesOnlyFromValidExp1(unittest.TestCase):

    def test_valid_exp1_knee_sets_threshold_even_with_exp2_knee(self):
        th = threshold(E1_KNEE, E2_KNEE)
        self.assertEqual((th["knee_c"], th["knee_source"], th["confirmatory"]),
                         (80.2, "exp1 (G1 PASS)", True))
        self.assertIn("79.0 C", th["exp2_knee_support"])   # kept, not used

    def test_exp2_knee_never_substituted_for_invalid_or_missing_exp1(self):
        for name, e1 in (("archived 4-core safety stop", E1_4CORE),
                         ("knee before safety stop", E1_KNEE_THEN_STOPPED),
                         ("knee under partial load", E1_KNEE_PARTIAL),
                         ("missing", None)):
            with self.subTest(e1=name):
                th = threshold(e1, E2_KNEE)
                self.assertIsNone(th["knee_c"])
                self.assertIsNone(th["knee_source"])
                self.assertFalse(th["confirmatory"])
                self.assertIn("79.0 C", th["exp2_knee_support"])
        self.assertEqual(threshold(None, E2_KNEE)["g1_state"], "NOT_TESTED")

    def test_manual_knee_rejected_on_real_hardware(self):
        with self.assertRaises(SystemExit):
            threshold(E1_4CORE, E2_KNEE, is_mock=False, knee=80.0)

    def test_mock_runs_are_never_confirmatory(self):
        th = threshold(None, None, is_mock=True, knee=80.0)
        self.assertEqual((th["knee_c"], th["confirmatory"]), (80.0, False))
        th = threshold(E1_KNEE, None, is_mock=True)
        self.assertEqual((th["knee_c"], th["confirmatory"]), (80.2, False))


class FakeHardware:
    name = "fakehw"
    caps = {}

    def read(self):
        return {"t_wall": 0.0, "temp_c": 48.0, "freq_mhz": 1800.0,
                "throttled": 0, "energy_uj": None}


def run_main(outdir, *extra):
    argv = ["exp3_ordering.py", "--outdir", outdir, *extra]
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
        rc = e3.main()
    return rc, buf.getvalue()


class InvalidConfirmatoryRunIsRefused(unittest.TestCase):

    def test_real_backend_without_valid_exp1_knee_refuses_before_any_load(self):
        d = outdir_with(E1_4CORE, E2_KNEE)
        boom = mock.Mock(side_effect=AssertionError("workload must not run"))
        with mock.patch.object(e3.sensors, "get_backend",
                               return_value=FakeHardware()), \
                mock.patch.object(e3.wl, "FixedWorkPool", boom):
            rc, out = run_main(d, "--reps", "1", "--k", "4", "--parallel", "4",
                               "--workload-validation", write_report(make_report()))
        self.assertEqual(rc, 2)
        self.assertIn("REFUSING TO RUN: no valid Experiment 1 knee", out)
        self.assertIn("exp2 observed a frequency drop from 79.0 C", out)
        boom.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(d, "exp3.meta.json")))


def _sha_workload():
    with open(wl.__file__, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def make_report(P=4, status=vw.ACCEPTED, sha=None, gap_class=vw.TARGET,
                n_pairs=40, stale=False, criteria_ok=True, frozen_P=None):
    frozen = {"P": frozen_P or P, "n_b": 128, "u_h": 50, "u_c": 18, "S_ms": 118.0,
              "params_id": f"P{P}-F1", "stale": stale}
    crit = {f"A{i}": True for i in range(1, 10)}
    crit["A9"] = criteria_ok
    cfg = {"status": status, "frozen": frozen,
           "evaluation": {"gap": {"n_pairs": n_pairs, "class": gap_class,
                                  "g_hat": 0.004, "ci_lo": -0.006, "ci_hi": 0.013},
                          "criteria": crit,
                          "values": {"hot_median_ms": 201.0, "cool_median_ms": 201.8}}}
    return {"run_id": "test-run", "overall": "COMPLETE", "configs": {str(P): cfg},
            "prereg_sha256": {"workload.py": sha or _sha_workload()}}


def write_report(rep):
    fd, path = tempfile.mkstemp(prefix="wv_", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(rep, f)
    return path


class FakePool:
    """Stands in for workload.FixedWorkPool: records specs, no processes."""
    instances = []

    def __init__(self, P, **kw):
        self.P, self.specs, self.stopped = P, [], False
        FakePool.instances.append(self)

    def run_job(self, spec, record_units=False):
        self.specs.append(spec)
        cpu = 200.0 * spec.P * (1.0 if spec.name == "HOT" else 0.35)
        return {"latency_ms": 200.0, "units_planned": spec.planned_units,
                "units_done": spec.planned_units, "cpu_ms": cpu, "compute_ms": cpu,
                "sleep_req_ms": spec.planned_sleep_ms,
                "sleep_actual_ms": spec.planned_sleep_ms,
                "realized_duty": cpu / (200.0 * spec.P), "overhead_ms": 1.0,
                "imbalance": 0.01, "skew_ms": 0.1}

    def stop(self):
        self.stopped = True


class WorkloadValidationGate(unittest.TestCase):

    def resolve(self, rep=None, P=4, path=None):
        return e3.resolve_workload(path or write_report(rep), P, is_mock=False)

    def test_accepted_validation_supplies_the_frozen_jobs(self):
        info, refusal = self.resolve(make_report())
        self.assertIsNone(refusal)
        self.assertTrue(info["accepted"])
        self.assertEqual((info["hot"].units_per_shard, info["hot"].sleep_per_shard_ms),
                         (50, 0.0))
        self.assertEqual((info["cool"].units_per_shard, info["cool"].sleep_per_shard_ms),
                         (18, 118.0))
        vm = info["cold_latency_match"]
        self.assertTrue(vm["matched"])
        self.assertEqual((vm["relative_gap"], vm["gap_class"]), (0.004, vw.TARGET))

    def test_every_unmet_prerequisite_is_a_refusal(self):
        cases = {
            "missing report": (None, 4, "/nonexistent/report.json"),
            "rejected": (make_report(status=vw.REJECTED), 4, None),
            "incomplete": (make_report(status=vw.INCOMPLETE), 4, None),
            "other P only": (make_report(P=1), 4, None),
            "frozen for other P": (make_report(frozen_P=1), 4, None),
            "stale params": (make_report(stale=True), 4, None),
            "gap FAIL": (make_report(gap_class=vw.FAIL), 4, None),
            "criterion false": (make_report(criteria_ok=False), 4, None),
            "39 pairs": (make_report(n_pairs=39), 4, None),
            "code changed": (make_report(sha="0" * 64), 4, None),
            "no --parallel": (make_report(), None, None),
        }
        for name, (rep, P, path) in cases.items():
            with self.subTest(case=name):
                info, refusal = self.resolve(rep, P, path)
                self.assertIsNone(info)
                self.assertTrue(refusal)

    @staticmethod
    def p4b_report(gemms=4):
        rep = make_report(P=4)
        cfg = rep["configs"].pop("4")
        cfg["frozen"].update(params_id="P4b-F1")
        if gemms is not None:
            cfg["frozen"]["gemms_per_unit"] = gemms
        rep["configs"]["4b"] = cfg
        return rep

    def test_p4b_validation_supplies_four_gemm_jobs_and_its_telemetry_scheme(self):
        info, refusal = e3.resolve_workload(write_report(self.p4b_report()), 4,
                                            is_mock=False, config="4b")
        self.assertIsNone(refusal)
        self.assertEqual((info["config"], info["telemetry"]), ("4b", "sysfs"))
        self.assertEqual((info["hot"].gemms_per_unit, info["cool"].gemms_per_unit), (4, 4))
        self.assertIn("P4b", info["cold_latency_match"]["source"])

    def test_p4b_mismatches_are_refusals(self):
        cases = {
            "4b with P=1": (self.p4b_report(), 1, "4b"),
            "4b asked, only P=4 in report": (make_report(P=4), 4, "4b"),
            "4b frozen without gemms": (self.p4b_report(gemms=None), 4, "4b"),
            "four-GEMM params used as plain P=4": (
                {**make_report(P=4), "configs": {"4": self.p4b_report()["configs"]["4b"]}},
                4, None),
            "unknown config": (self.p4b_report(), 4, "4c"),
        }
        for name, (rep, P, config) in cases.items():
            with self.subTest(case=name):
                info, refusal = e3.resolve_workload(write_report(rep), P, is_mock=False,
                                                    config=config)
                self.assertIsNone(info)
                self.assertTrue(refusal)

    def test_plain_configurations_report_full_telemetry(self):
        info, _ = self.resolve(make_report())
        self.assertEqual((info["config"], info["telemetry"]), ("4", "full"))

    def test_p1_accepted_but_run_incomplete_because_p4_incomplete_is_refused(self):
        rep = make_report(P=1)
        rep["configs"]["4"] = {"status": vw.INCOMPLETE,
                               "reason": "rerun limit 2 reached before re-running acceptance A3"}
        rep["overall"] = vw.INCOMPLETE
        info, refusal = self.resolve(rep, P=1)
        self.assertIsNone(info)
        self.assertIn("not COMPLETE", refusal)

    def test_interrupted_report_is_refused_even_if_marked_complete(self):
        rep = make_report()
        rep["interrupted"] = "interrupted (SIGTERM) during V4"
        info, refusal = self.resolve(rep)
        self.assertIsNone(info)
        self.assertIn("interrupted", refusal)

    def test_mock_needs_no_validation_and_is_never_accepted(self):
        info, refusal = e3.resolve_workload(None, None, is_mock=True)
        self.assertIsNone(refusal)
        self.assertFalse(info["accepted"])


class RealHardwareRunsWithFakes(unittest.TestCase):

    def patched(self, outdir, *extra):
        FakePool.instances.clear()
        with mock.patch.object(e3.sensors, "get_backend", return_value=FakeHardware()), \
                mock.patch.object(e3.wl, "FixedWorkPool", FakePool):
            return run_main(outdir, *extra)

    def test_valid_knee_without_validation_refuses_before_any_load(self):
        rc, out = self.patched(outdir_with(E1_KNEE, None), "--parallel", "4",
                               "--reps", "1", "--k", "4")
        self.assertEqual(rc, 2)
        self.assertIn("REFUSING TO RUN: no workload validation report", out)
        self.assertEqual(FakePool.instances, [])

    def test_valid_knee_with_rejected_validation_refuses(self):
        rc, out = self.patched(outdir_with(E1_KNEE, None), "--parallel", "4",
                               "--workload-validation",
                               write_report(make_report(status=vw.REJECTED)))
        self.assertEqual(rc, 2)
        self.assertIn("REJECTED", out)
        self.assertEqual(FakePool.instances, [])

    def test_p4b_is_refused_before_any_load_until_amendment_a4(self):
        rep = WorkloadValidationGate.p4b_report()
        rc, out = self.patched(outdir_with(E1_KNEE, None), "--parallel", "4",
                               "--workload-config", "4b", "--reps", "1", "--k", "4",
                               "--workload-validation", write_report(rep))
        self.assertEqual(rc, 2)
        self.assertIn("Amendment A4", out)
        self.assertEqual(FakePool.instances, [])

    def test_accepted_validation_and_valid_knee_run_fixed_work_jobs(self):
        d = outdir_with(E1_KNEE, E2_KNEE)
        rc, out = self.patched(d, "--parallel", "4", "--reps", "1", "--k", "4",
                               "--orderings", "hot_first,alternating",
                               "--workload-validation", write_report(make_report()))
        self.assertIn(rc, (None, 0))
        pool = FakePool.instances[0]
        self.assertTrue(pool.stopped)
        self.assertTrue(all(s.P == 4 for s in pool.specs))
        self.assertEqual({(s.name, s.units_per_shard, s.sleep_per_shard_ms)
                          for s in pool.specs},
                         {("HOT", 50, 0.0), ("COOL", 18, 118.0)})
        with open(os.path.join(d, "exp3.meta.json")) as f:
            meta = json.load(f)
        self.assertTrue(meta["workload"]["accepted"])
        self.assertEqual(meta["cold_latency_match"]["gap_class"], vw.TARGET)
        self.assertTrue(meta["planned_identical_across_runs"])
        self.assertFalse(meta["energy_measured"])
        self.assertEqual(meta["knee_c"], 80.2)
        self.assertTrue(all(r["achieved"]["units_done"] == r["planned"]["units"]
                            for r in meta["runs"]))


class MockEndToEnd(unittest.TestCase):
    """Simulated mock device: sleep-based, no CPU load."""

    def test_no_valid_knee_marks_threshold_metrics_unavailable(self):
        d = outdir_with(E1_4CORE, E2_KNEE)
        rc, out = run_main(d, "--backend", "mock", "--reps", "1", "--k", "4",
                           "--orderings", "hot_first,alternating")
        self.assertIn(rc, (None, 0))
        with open(os.path.join(d, "exp3.meta.json")) as f:
            meta = json.load(f)
        self.assertIsNone(meta["knee_c"])
        self.assertFalse(meta["time_above_knee_available"])
        self.assertFalse(meta["threshold"]["confirmatory"])
        self.assertIn("79.0 C", meta["threshold"]["exp2_knee_support"])
        self.assertTrue(all(r["time_above_knee_s"] is None for r in meta["runs"]))

    def test_valid_exp1_knee_is_used_on_mock(self):
        d = outdir_with(E1_KNEE, E2_KNEE)
        rc, out = run_main(d, "--backend", "mock", "--reps", "1", "--k", "4",
                           "--orderings", "hot_first,alternating")
        with open(os.path.join(d, "exp3.meta.json")) as f:
            meta = json.load(f)
        self.assertEqual(meta["knee_c"], 80.2)
        self.assertEqual(meta["threshold"]["knee_source"], "exp1 (G1 PASS)")
        self.assertTrue(all(r["time_above_knee_s"] is not None
                            for r in meta["runs"]))


if __name__ == "__main__":
    unittest.main()
