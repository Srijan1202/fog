"""
Regression tests for exp3_ordering.py (Amendment A7): where the time-above-knee
threshold may come from, the workload-validation prerequisite, refusals before
any load, the A7 session (cooldown, pre-heat, validity, reruns) with fakes, and
a mock session through analyze.py.

    python3 -m unittest -v test_exp3_ordering

No hardware load: a fake backend and a fake worker pool for the real-hardware
paths, the simulated mock backend (sleep-based) for end-to-end runs.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

import analyze as an
import exp3_ordering as e3
import monitor_guard as mg
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

    def test_p4c_validation_is_accepted_under_its_own_key(self):
        rep = self.p4b_report()
        cfg = rep["configs"].pop("4b")
        cfg["frozen"]["params_id"] = "P4c-F1"
        rep["configs"]["4c"] = cfg
        info, refusal = e3.resolve_workload(write_report(rep), 4, is_mock=False, config="4c")
        self.assertIsNone(refusal)
        self.assertEqual((info["config"], info["telemetry"], info["hot"].gemms_per_unit),
                         ("4c", "sysfs", 4))
        self.assertIn("P4c", info["cold_latency_match"]["source"])
        info, refusal = e3.resolve_workload(write_report(self.p4b_report()), 4,
                                            is_mock=False, config="4c")   # a P4b report
        self.assertIsNone(info)
        self.assertIn("P4c", refusal)

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


def p4c_report():
    rep = WorkloadValidationGate.p4b_report()
    cfg = rep["configs"].pop("4b")
    cfg["frozen"]["params_id"] = "P4c-F1"
    rep["configs"]["4c"] = cfg
    return rep


class FakePi:
    name = "raspberrypi"


def run_main(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = e3.main(argv)
    return rc, buf.getvalue()


class A7Refusals(unittest.TestCase):
    """Real hardware: every refusal comes before any file or load."""

    def setUp(self):
        self.out = os.path.join(tempfile.mkdtemp(prefix="exp3_test_"), "run")
        self.e1 = os.path.join(outdir_with(E1_KNEE, None), "exp1.meta.json")
        for target, value in ((e3.sensors, "get_backend"),):
            p = mock.patch.object(target, value, return_value=FakePi())
            p.start()
            self.addCleanup(p.stop)
        self.boom = mock.patch.object(e3.wl, "FixedWorkPool",
                                      side_effect=AssertionError("no load may start"))
        self.boom.start()
        self.addCleanup(self.boom.stop)

    def argv(self, *extra, e1=None, report=None):
        return ["--outdir", self.out, "--require-monitor", "1", "--exp1-meta", e1 or self.e1,
                "--workload-validation", report or write_report(p4c_report()), *extra]

    def test_require_monitor_is_mandatory(self):
        rc, out = run_main(["--outdir", self.out])
        self.assertEqual(rc, 2)
        self.assertIn("--require-monitor SECONDS is mandatory", out)
        self.assertFalse(os.path.exists(self.out))

    def test_g1_must_pass(self):
        bad = os.path.join(outdir_with(E1_4CORE, None), "exp1.meta.json")
        rc, out = run_main(self.argv(e1=bad))
        self.assertEqual(rc, 2)
        self.assertIn("does not pass G1", out)
        self.assertFalse(os.path.exists(self.out))

    def test_only_an_accepted_p4c_validation(self):
        for name, rep in (("plain P=4", make_report(P=4)),
                          ("P4b", WorkloadValidationGate.p4b_report()),
                          ("P4c rejected", {**p4c_report(), "configs": {"4c": {
                              **p4c_report()["configs"]["4c"], "status": vw.REJECTED}}})):
            with self.subTest(case=name):
                rc, out = run_main(self.argv(report=write_report(rep)))
                self.assertEqual(rc, 2)
                self.assertIn("refused: workload:", out)
                self.assertFalse(os.path.exists(self.out))

    def test_no_watchdog_means_no_load_and_an_aborted_record(self):
        with mock.patch.object(e3.e1t, "measure_idle_baseline") as idle:
            rc, out = run_main(self.argv())
        self.assertEqual(rc, 2)
        self.assertIn("[exp3] run run: waiting up to 1 s for a verified watchdog", out)
        idle.assert_not_called()
        with open(os.path.join(self.out, "exp3.start.json")) as f:
            self.assertEqual(set(json.load(f)["sha256"]), set(e3.EXP3_HASHED))
        with open(os.path.join(self.out, "exp3.meta.json")) as f:
            meta = json.load(f)
        self.assertEqual((meta["amendment"], meta["k_jobs"], meta["start_c"]), ("A7", 600, 77.0))
        self.assertIn("refused: monitoring not established", meta["abort_reason"])
        self.assertIn("aborted", an.e3_invalid(meta))
        self.assertEqual(run_main(self.argv())[0], 2)                # never overwrite a run


class Thermal:
    """Fake die: HOT +0.8 C and COOL +0.2 C per job; each idle poll cools 2 C (not below 40)."""
    name = "fake"

    def __init__(self, temp=40.0):
        self.temp = temp

    def read(self):
        return {"t_wall": time.monotonic(), "temp_c": round(self.temp, 3), "freq_mhz": 1800.0,
                "throttled": 0}

    def quick(self):
        return {"temp_c": round(self.temp, 3), "freq_mhz": 1800.0}


class TPool:
    made = 0

    def __init__(self, b, fail_at=None, short_at=None, alive=True):
        TPool.made += 1
        self.b, self.n, self.fail_at, self.short_at, self._alive = b, 0, fail_at, short_at, alive
        self.stopped, self.unstopped, self.specs = False, [], []

    def alive(self):
        return self._alive and not self.stopped

    def run_job(self, spec, record_units=False):
        self.n += 1
        self.specs.append(spec)
        if self.fail_at == self.n:
            raise wl.JobTimeout("no reply from shard(s) [1]")
        self.b.temp += 0.8 if spec.name == "HOT" else 0.2
        done = spec.planned_units - (1 if self.short_at == self.n else 0)
        lat = 196.0 + (12.0 if self.b.temp >= 80.8 else 0.0)
        return {"latency_ms": lat, "units_planned": spec.planned_units, "units_done": done,
                "cpu_ms": 780.0, "compute_ms": 770.0, "sleep_req_ms": spec.planned_sleep_ms,
                "sleep_actual_ms": spec.planned_sleep_ms, "realized_duty": 0.99,
                "overhead_ms": 1.0, "nivcsw": 3}

    def stop(self):
        self.stopped = True
        return []


HOT4 = wl.JobSpec("HOT", 4, 128, 13, 0.0, 4)
COOL4 = wl.JobSpec("COOL", 4, 128, 5, 119.55, 4)


def session(pool_kw=None, flags=None, check=None, temp=40.0, **plan_kw):
    b = Thermal(temp)
    pools = []

    def make_pool():
        p = TPool(b, **(pool_kw or {}) if not pools else {})
        pools.append(p)
        return p

    def sleep(s):
        b.temp = max(40.0, b.temp - 2.0)
    base = dict(k=10, hot_frac=0.5, reps=2, orderings=("hot_first", "alternating"), start_c=60.0,
                preheat_cap_s=5.0, idle_c=40.0, margin_c=3.0, cooldown_cap_s=5.0, hz=50.0,
                poll_s=0.0, seed=0)
    base.update(plan_kw)
    s = e3.Session(b, make_pool, HOT4, COOL4, e3.Plan(**base), 80.828, check=check,
                   flags=flags or (lambda: 0x0), quick=b.quick, log=lambda m: None, sleep=sleep)
    return s.run(), pools, b


class A7Session(unittest.TestCase):
    def test_complete_valid_session(self):
        s, pools, _ = session()
        self.assertTrue(s.session_complete)
        self.assertIsNone(s.abort_reason)
        self.assertEqual(len(pools), 1)
        self.assertTrue(pools[0].stopped)
        self.assertEqual([(r["rep"], r["mode"]) for r in s.runs], [tuple(x) for x in s.schedule])
        self.assertEqual(sorted(m for _, m in s.schedule), ["alternating"] * 2 + ["hot_first"] * 2)
        self.assertEqual({r for r, _ in s.schedule[:2]}, {0})      # interleaved: rep 0 first
        for r in s.runs:
            self.assertTrue(r["valid"], r)
            self.assertEqual(r["n_jobs"], 10)
            self.assertGreaterEqual(r["start_temp_c"], 60.0)        # pre-heated
            self.assertGreater(r["preheat_jobs"], 0)
            self.assertEqual(r["planned"], s.planned)               # identical multiset (A2)
            self.assertIsNotNone(r["time_above_knee_s"])
            self.assertEqual(r["achieved"]["units_done"], 5 * 52 + 5 * 20)   # HOT 4x13, COOL 4x5
        self.assertEqual(len(s.jobs), 40)
        self.assertTrue(all("throttled_after" in j for j in s.jobs))

    def test_sequence_jobs_follow_the_ordering(self):
        s, _, _ = session(reps=1, orderings=("hot_first", "cool_first", "alternating"))
        by = {r["mode"]: [j["class"] for j in s.jobs if j["run_index"] == r["run_index"]]
              for r in s.runs}
        self.assertEqual(by["hot_first"], ["HOT"] * 5 + ["COOL"] * 5)
        self.assertEqual(by["cool_first"], ["COOL"] * 5 + ["HOT"] * 5)
        self.assertEqual(by["alternating"][:4], ["HOT", "COOL", "HOT", "COOL"])

    def test_preheat_cap_makes_the_sequence_invalid_and_it_is_rerun_once(self):
        s, _, _ = session(start_c=999.0, preheat_cap_s=-1.0, reps=1)   # cap hit at once
        self.assertTrue(s.session_complete)
        self.assertEqual(len(s.runs), 4)                            # 2 + 2 reruns
        self.assertTrue(all(not r["valid"] and "pre-heat did not reach" in r["invalid_reason"]
                            for r in s.runs))
        self.assertEqual([r["rerun_of"] for r in s.runs], [None, None, 0, 1])

    def test_cooldown_cap(self):
        s, _, _ = session(temp=90.0, cooldown_cap_s=0.0, reps=1, idle_c=10.0, margin_c=0.0)
        self.assertTrue(all("cooldown:" in r["invalid_reason"] for r in s.runs))
        self.assertTrue(s.session_complete)

    def test_job_timeout_restarts_the_pool_and_the_rerun_counts(self):
        s, pools, _ = session(pool_kw={"fail_at": 3}, reps=1)
        self.assertEqual(len(pools), 2)                             # replaced after the timeout
        self.assertTrue(pools[0].stopped and pools[1].stopped)
        bad = [r for r in s.runs if not r["valid"]]
        self.assertEqual(len(bad), 1)
        self.assertIn("JobTimeout", bad[0]["invalid_reason"])
        rerun = [r for r in s.runs if r["rerun_of"] == bad[0]["run_index"]]
        self.assertEqual(len(rerun), 1)
        self.assertTrue(rerun[0]["valid"])

    def test_short_job_is_invalid(self):
        s, _, _ = session(pool_kw={"short_at": 30}, reps=1)
        self.assertTrue(any("did 51 of 52 units" in (r["invalid_reason"] or "") for r in s.runs))

    def test_undervoltage_stops_the_session(self):
        n = {"k": 0}

        def flags():
            n["k"] += 1
            return 0x10001 if n["k"] == 25 else 0x0
        s, pools, _ = session(flags=flags)
        self.assertTrue(s.undervoltage)
        self.assertIn("under-voltage", s.abort_reason)
        self.assertFalse(s.session_complete)
        self.assertTrue(pools[0].stopped)
        meta = {"amendment": "A7", "abort_reason": s.abort_reason, "session_complete": False}
        self.assertIn("aborted", an.e3_invalid(meta))

    def test_live_flags_before_a_sequence_make_it_invalid(self):
        n = {"k": 0}

        def flags():
            n["k"] += 1
            return 0x4 if n["k"] == 1 else 0x0
        s, _, _ = session(flags=flags, reps=1)
        self.assertIn("live throttle flags before the sequence", s.runs[0]["invalid_reason"])
        self.assertEqual(sum(r["valid"] for r in s.runs), 2)        # the rerun is valid

    def test_lost_watchdog_interrupts(self):
        n = {"k": 0}

        def check():
            n["k"] += 1
            if n["k"] == 40:
                raise mg.MonitorLost("watchdog lost: monitor.log missing")
        s, pools, _ = session(check=check)
        self.assertIn("watchdog lost", s.interrupted)
        self.assertFalse(s.session_complete)
        self.assertTrue(pools[0].stopped)
        self.assertEqual(s.runs[-1]["invalid_reason"], "interrupted")

    def test_no_rerun_option(self):
        s, _, _ = session(start_c=999.0, preheat_cap_s=-1.0, reps=1, rerun_invalid=False)
        self.assertEqual(len(s.runs), 2)


class A7MockEndToEnd(unittest.TestCase):
    """Simulated mock device (sleep-based, no CPU load): the whole pipeline into analyze."""

    def test_session_into_analyze(self):
        out = os.path.join(tempfile.mkdtemp(prefix="exp3_mock_"), "m")
        rc, text = run_main(["--backend", "mock", "--reps", "8", "--k", "40",
                             "--orderings", "hot_first,cool_first", "--mock-tau", "1.5",
                             "--mock-job-ms", "50", "--idle-window-s", "0.3", "--outdir", out])
        self.assertEqual(rc, 0, text)
        with open(os.path.join(out, "exp3.meta.json")) as f:
            meta = json.load(f)
        self.assertEqual(meta["a7_validity"], "valid")
        self.assertTrue(meta["session_complete"])
        self.assertEqual(meta["n_valid"], {"hot_first": 8, "cool_first": 8})
        self.assertIn("MOCK, NOT A RESULT", text)
        tak = {m: [r["time_above_knee_s"] for r in meta["runs"] if r["mode"] == m]
               for m in ("hot_first", "cool_first")}
        self.assertGreater(min(tak["hot_first"]), max(tak["cool_first"]))   # mock physics
        d = os.path.dirname(out)
        shutil.copy(os.path.join(out, "exp3.meta.json"), d)
        with open(os.path.join(d, "exp1.meta.json"), "w") as f:
            json.dump(E1_KNEE, f)
        with open(os.path.join(d, "exp2.meta.json"), "w") as f:
            json.dump(E2_PASS, f)
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["analyze.py", "--outdir", d]), \
                contextlib.redirect_stdout(buf):
            result = an.main()
        self.assertTrue(result["gates"]["G4_mediator"])
        self.assertIsNotNone(result["gates"]["G5_outcome"])
        self.assertIn("P99 pairs surviving Holm", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
