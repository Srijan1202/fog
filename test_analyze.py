"""
Regression tests for analyze.py: G1 classification and the overall verdict.

    python3 -m unittest -v test_analyze

Synthetic metadata only; no hardware, no experiment runs.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import analyze as an

FULL = {"per_core_busy": [1.0, 0.999, 0.999, 0.999], "avg_cores_busy": 3.997}
ONE = {"per_core_busy": [1.0, 0.01, 0.01, 0.01], "avg_cores_busy": 1.03}
NO_KNEE = {"knee_c": None, "f_max": 1800.0, "f_min": 1800.0, "depth": 0.0,
           "throttled_samples": 0}
KNEE = {"knee_c": 80.2, "f_max": 1800.0, "f_min": 1500.0, "depth": 1 / 6,
        "throttled_samples": 40}

# Field values copied from the archived results/exp1_4core/exp1.meta.json
# (written by exp1_thermal.py 9fd02fc4, before p4_status existed).
E1_4CORE = {"experiment": "exp1_thermal", "backend": "raspberrypi",
            "mock": False, "knee": NO_KNEE, "workers": 4,
            "abort_reason": "max_temp: 75.5 C >= 75.0 C",
            "cpu_util_heating": FULL}
E1_SAFETY_NEW = {**E1_4CORE, "p4_status": "SAFETY_STOPPED"}
E1_ABORTED = {**E1_4CORE, "abort_reason": "worker(s) exited early: ['heat-2']",
              "p4_status": "ABORTED"}
E1_PARTIAL = {**E1_4CORE, "abort_reason": None, "workers": 1,
              "cpu_util_heating": ONE, "p4_status": "PARTIAL_LOAD"}
# Same key set as the archived exp1_1core (original exp1_thermal.py).
E1_1CORE_LEGACY = {"experiment": "exp1_thermal", "backend": "raspberrypi",
                   "mock": False, "knee": NO_KNEE, "idle_temp_c": 44.79}
# Amendment A3: a valid Pi knee record names the firmware ARM clock and its counts.
CLK = {"clock_source": "arm_clock_mhz", "clock_samples": 4000, "clock_missing": 0}
NO_KNEE_A3, KNEE_A3 = {**NO_KNEE, **CLK}, {**KNEE, **CLK}
E1_VALID_FAIL = {**E1_4CORE, "abort_reason": None, "p4_status": "P4_FAIL",
                 "knee": NO_KNEE_A3}
E1_KNEE = {**E1_4CORE, "abort_reason": None, "knee": KNEE_A3,
           "p4_status": "NO_KILL"}
# Field values of exp1-4core-20261016-01 (exp1_thermal.py 7968af5, before A3):
# completed, full load, no knee in scaling_cur_freq, no ARM clock record.
E1_PRE_A3 = {"experiment": "exp1_thermal", "backend": "raspberrypi", "mock": False,
             "knee": NO_KNEE, "workers": 4, "abort_reason": None,
             "p4_status": "P4_FAIL", "cpu_util_heating": FULL,
             "heat_minutes": 15.0, "heat_wall_s": 900.7,
             "heat_summary": {"throttled_or": "0x80008"}}
E1_KNEE_THEN_STOPPED = {**E1_4CORE, "knee": KNEE, "p4_status": "SAFETY_STOPPED"}
E1_KNEE_PARTIAL = {**E1_PARTIAL, "knee": KNEE, "p4_status": "NO_KILL"}
E1_MOCK_FAIL = {"experiment": "exp1_thermal", "backend": "mock", "mock": True,
                "knee": NO_KNEE, "abort_reason": None, "p4_status": "P4_FAIL",
                "cpu_util_heating": {"per_core_busy": [0.0] * 4,
                                     "avg_cores_busy": 0.0}}

E2_PASS = {"slope": {"slope_ms_per_C": 0.8, "ci_lo": 0.5, "ci_hi": 1.1,
                     "excludes_zero": True},
           "spearman_temp_latency": 0.7, "knee_c": None}
E2_FLAT = {"slope": {"slope_ms_per_C": 0.01, "ci_lo": -0.2, "ci_hi": 0.2,
                     "excludes_zero": False},
           "spearman_temp_latency": 0.05, "knee_c": None}


def make_e3(moves=True):
    """Two orderings x 6 runs. moves=True: mediator and P99 separate cleanly."""
    runs = []
    for i in range(6):
        for mode, off in (("alternating", 0), ("hot_first", 1)):
            k = off if moves else 0
            runs.append({"mode": mode, "time_above_knee_s": 100 + 50 * k + i,
                         "p99_ms": 300 + 60 * k + i, "p95_ms": 290 + 60 * k + i,
                         "mean_ms": 250 + i, "throughput_jobs_s": 4.0 + 0.01 * i})
    return {"cold_latency_match": {"hot_median_ms": 236.5, "cool_median_ms": 236.0,
                                   "relative_gap": 0.002, "matched": True},
            "runs": runs}


def write_outdir(e1=None, e2=None, e3=None):
    d = tempfile.mkdtemp(prefix="analyze_test_")
    for name, m in (("exp1", e1), ("exp2", e2), ("exp3", e3)):
        if m is not None:
            with open(os.path.join(d, f"{name}.meta.json"), "w") as f:
                json.dump(m, f)
    return d


def run(e1=None, e2=None, e3=None):
    argv = ["analyze.py", "--outdir", write_outdir(e1, e2, e3)]
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
        result = an.main()
    return result, buf.getvalue()


class G1Classification(unittest.TestCase):

    def test_safety_stopped_archived_4core_is_inconclusive(self):
        state, *_, why = an.classify_g1(E1_4CORE, None)
        self.assertEqual(state, "INCONCLUSIVE")
        self.assertIn("SAFETY-STOPPED: INCONCLUSIVE FOR KNEE DETECTION", why)

    def test_safety_stopped_new_format_is_inconclusive(self):
        self.assertEqual(an.classify_g1(E1_SAFETY_NEW, None)[0], "INCONCLUSIVE")

    def test_aborted_run_is_inconclusive(self):
        state, *_, why = an.classify_g1(E1_ABORTED, None)
        self.assertEqual(state, "INCONCLUSIVE")
        self.assertIn("aborted", why)

    def test_partial_load_is_inconclusive(self):
        self.assertEqual(an.classify_g1(E1_PARTIAL, None)[0], "INCONCLUSIVE")

    def test_legacy_metadata_without_load_record_is_inconclusive(self):
        self.assertEqual(an.classify_g1(E1_1CORE_LEGACY, None)[0], "INCONCLUSIVE")

    def test_incomplete_metadata_is_never_fail(self):
        for e1 in ({"experiment": "exp1_thermal"}, {"knee": None},
                   {"knee": "garbage"}):
            with self.subTest(e1=e1):
                self.assertEqual(an.classify_g1(e1, None)[0], "INCONCLUSIVE")
        self.assertEqual(an.classify_g1(None, None)[0], "NOT_TESTED")
        self.assertEqual(an.classify_g1({}, None)[0], "INCONCLUSIVE")  # present but empty
        self.assertEqual(an.classify_g1(None, E2_FLAT)[0], "NOT_TESTED")

    def test_incomplete_knee_record_is_inconclusive(self):
        for missing in ("f_max", "f_min", "depth"):
            with self.subTest(missing=missing):
                e1 = {**E1_KNEE, "knee": {**KNEE, missing: None}}
                self.assertEqual(an.classify_g1(e1, None)[0], "INCONCLUSIVE")

    def test_no_frequency_record_cannot_fail(self):
        e1 = {**E1_VALID_FAIL, "knee": {**NO_KNEE, "f_max": None}}
        self.assertEqual(an.classify_g1(e1, None)[0], "INCONCLUSIVE")

    def test_short_heating_cannot_fail(self):
        e1 = {**E1_VALID_FAIL, "heat_minutes": 15.0, "heat_wall_s": 118.2}
        self.assertEqual(an.classify_g1(e1, None)[0], "INCONCLUSIVE")
        full = {**E1_VALID_FAIL, "heat_minutes": 15.0, "heat_wall_s": 900.4}
        self.assertEqual(an.classify_g1(full, None)[0], "FAIL")

    def test_undervoltage_invalidates_both_pass_and_fail(self):
        uv = {"heat_summary": {"throttled_or": "0x50005"}}
        self.assertEqual(an.classify_g1({**E1_KNEE, **uv}, None)[0], "INCONCLUSIVE")
        self.assertEqual(an.classify_g1({**E1_VALID_FAIL, **uv}, None)[0],
                         "INCONCLUSIVE")
        thermal_only = {"heat_summary": {"throttled_or": "0x60006"}}
        self.assertEqual(an.classify_g1({**E1_KNEE, **thermal_only}, None)[0],
                         "PASS")

    def test_knee_recorded_before_safety_stop_is_inconclusive(self):
        state, knee, _, why = an.classify_g1(E1_KNEE_THEN_STOPPED, None)
        self.assertEqual((state, knee), ("INCONCLUSIVE", None))
        self.assertIn("SAFETY-STOPPED", why)
        self.assertIn("knee recorded at 80.2 C does not establish G1", why)

    def test_knee_recorded_under_partial_load_is_inconclusive(self):
        state, knee, _, why = an.classify_g1(E1_KNEE_PARTIAL, None)
        self.assertEqual((state, knee), ("INCONCLUSIVE", None))
        self.assertIn("loaded only", why)

    def test_knee_in_any_invalid_exp1_never_passes(self):
        for name, e1 in (
                ("safety", E1_KNEE_THEN_STOPPED), ("partial", E1_KNEE_PARTIAL),
                ("aborted", {**E1_ABORTED, "knee": KNEE}),
                ("legacy", {**E1_1CORE_LEGACY, "knee": KNEE}),
                ("short", {**E1_KNEE, "heat_minutes": 15.0, "heat_wall_s": 300.0}),
                ("undervolt", {**E1_KNEE,
                               "heat_summary": {"throttled_or": "0x10004"}})):
            for e2 in (None, E2_PASS, {**E2_PASS, "knee_c": 79.0}):
                with self.subTest(e1=name, e2=e2 and e2.get("knee_c")):
                    self.assertEqual(an.classify_g1(e1, e2)[0], "INCONCLUSIVE")

    def test_control_valid_exp1_knee_passes(self):
        self.assertEqual(an.classify_g1(E1_KNEE, None)[:2], ("PASS", 80.2))
        full = {**E1_KNEE, "heat_minutes": 15.0, "heat_wall_s": 900.4}
        self.assertEqual(an.classify_g1(full, None)[0], "PASS")

    def test_control_completed_full_load_run_without_knee_is_fail(self):
        self.assertEqual(an.classify_g1(E1_VALID_FAIL, None)[0], "FAIL")

    def test_mock_pipeline_check_behavior_is_preserved(self):
        self.assertEqual(an.classify_g1(E1_MOCK_FAIL, None)[0], "FAIL")


class AmendmentA3ArmClock(unittest.TestCase):
    """On the Pi, G1 counts only a knee measured in the firmware ARM clock."""

    def test_knee_clock_per_backend(self):
        self.assertEqual(an.knee_clock("raspberrypi"), "arm_clock_mhz")
        for b in ("linux_cpu", "nvidia", "mock", "windows", None):
            self.assertEqual(an.knee_clock(b), "freq_mhz")

    def test_pre_a3_completed_no_knee_run_is_inconclusive_not_fail(self):
        state, _, _, why = an.classify_g1(E1_PRE_A3, None)
        self.assertEqual(state, "INCONCLUSIVE")
        self.assertIn("Amendment A3", why)
        self.assertIn("recorded outcome is unchanged", why)

    def test_pre_a3_knee_never_passes(self):
        m = {**E1_PRE_A3, "knee": KNEE, "p4_status": "NO_KILL"}
        self.assertEqual(an.classify_g1(m, None)[0], "INCONCLUSIVE")

    def test_knee_in_scaling_cur_freq_on_the_pi_is_inconclusive(self):
        for knee in ({**KNEE, **CLK, "clock_source": "freq_mhz"},
                     {**NO_KNEE, **CLK, "clock_source": "freq_mhz"}):
            with self.subTest(knee=knee["knee_c"]):
                m = {**E1_PRE_A3, "knee": knee}
                self.assertEqual(an.classify_g1(m, None)[0], "INCONCLUSIVE")

    def test_missing_clock_readings_limit_is_five_percent_inclusive(self):
        for missing, state in ((200, "FAIL"), (201, "INCONCLUSIVE")):
            with self.subTest(missing=missing):
                m = {**E1_PRE_A3, "knee": {**NO_KNEE_A3, "clock_missing": missing}}
                self.assertEqual(an.classify_g1(m, None)[0], state)
        m = {**E1_PRE_A3, "knee": {**KNEE_A3, "clock_missing": 201}}
        self.assertEqual(an.classify_g1(m, None)[0], "INCONCLUSIVE")

    def test_missing_or_zero_sample_counts_are_inconclusive(self):
        for over in ({"clock_samples": None}, {"clock_missing": None},
                     {"clock_samples": 0}, {"clock_samples": "4000"}):
            with self.subTest(over=over):
                m = {**E1_PRE_A3, "knee": {**NO_KNEE_A3, **over}}
                self.assertEqual(an.classify_g1(m, None)[0], "INCONCLUSIVE")

    def test_a3_runs_decide_g1(self):
        self.assertEqual(an.classify_g1({**E1_PRE_A3, "knee": NO_KNEE_A3}, None)[0],
                         "FAIL")
        self.assertEqual(an.classify_g1({**E1_PRE_A3, "knee": KNEE_A3,
                                         "p4_status": "NO_KILL"}, None)[:2],
                         ("PASS", 80.2))

    def test_other_safety_rules_still_come_first(self):
        m = {**E1_PRE_A3, "abort_reason": "max_temp: 85.1 C >= 85.0 C",
             "p4_status": "SAFETY_STOPPED"}
        self.assertIn("SAFETY-STOPPED", an.classify_g1(m, None)[3])

    def test_non_pi_and_mock_backends_are_unchanged(self):
        linux = {**E1_PRE_A3, "backend": "linux_cpu", "knee": KNEE,
                 "p4_status": "NO_KILL"}
        self.assertEqual(an.classify_g1(linux, None)[0], "PASS")
        self.assertEqual(an.classify_g1({**linux, "knee": NO_KNEE}, None)[0], "FAIL")
        pi_mock = {**E1_MOCK_FAIL, "backend": "raspberrypi"}
        self.assertEqual(an.classify_g1(pi_mock, None)[0], "FAIL")

    def test_pre_a3_run_end_to_end_is_further_evidence_not_kill(self):
        result, out = run(E1_PRE_A3)                     # exp1 only: no verdict yet
        self.assertIsNone(result["verdict"])
        self.assertIn("further evidence required", out)
        self.assertNotIn("FAIL: no throttle knee", out)
        result, _ = run(E1_PRE_A3, E2_FLAT, make_e3(False))
        self.assertEqual(result["verdict"], "FURTHER_EVIDENCE")


E2_KNEE = {**E2_PASS, "knee_c": 79.0}


class Exp2IsSupportingEvidenceOnly(unittest.TestCase):
    """Option B: G1 needs valid Experiment 1 evidence; exp2 only supports."""

    def test_safety_stopped_exp1_plus_exp2_knee_is_inconclusive(self):
        state, knee, _, why = an.classify_g1(E1_4CORE, E2_KNEE)
        self.assertEqual((state, knee), ("INCONCLUSIVE", None))
        self.assertIn("SAFETY-STOPPED", why)

    def test_partial_load_exp1_plus_exp2_knee_is_inconclusive(self):
        state, *_, why = an.classify_g1(E1_PARTIAL, E2_KNEE)
        self.assertEqual(state, "INCONCLUSIVE")
        self.assertIn("loaded only", why)

    def test_missing_exp1_plus_exp2_knee_is_not_tested(self):
        state, *_, why = an.classify_g1(None, E2_KNEE)
        self.assertEqual(state, "NOT_TESTED")
        self.assertIn("exp2 knee alone does not establish G1", why)

    def test_valid_exp1_knee_is_pass_and_uses_exp1_value(self):
        self.assertEqual(an.classify_g1(E1_KNEE, None)[:2], ("PASS", 80.2))
        self.assertEqual(an.classify_g1(E1_KNEE, E2_KNEE)[:2], ("PASS", 80.2))

    def test_valid_full_load_exp1_without_knee_is_fail(self):
        self.assertEqual(an.classify_g1(E1_VALID_FAIL, E2_PASS)[0], "FAIL")

    def test_exp2_knee_never_produces_pass_for_any_unestablished_exp1(self):
        for name, e1 in (("safety", E1_4CORE), ("safety-new", E1_SAFETY_NEW),
                         ("aborted", E1_ABORTED), ("partial", E1_PARTIAL),
                         ("legacy", E1_1CORE_LEGACY), ("empty", {}),
                         ("no-knee-field", {"experiment": "exp1_thermal"}),
                         ("missing", None)):
            with self.subTest(e1=name):
                self.assertIn(an.classify_g1(e1, E2_KNEE)[0],
                              ("INCONCLUSIVE", "NOT_TESTED"))

    def test_exp2_knee_contradicting_valid_exp1_blocks_fail(self):
        state, *_, why = an.classify_g1(E1_VALID_FAIL, E2_KNEE)
        self.assertEqual(state, "INCONCLUSIVE")
        self.assertIn("conflicting evidence", why)

    def test_support_text_is_reported_not_discarded(self):
        self.assertIn("79.0 C", an.exp2_knee_support(E2_KNEE))
        self.assertIsNone(an.exp2_knee_support(E2_PASS))
        self.assertIsNone(an.exp2_knee_support(None))


class OverallVerdict(unittest.TestCase):

    def test_g1_unknown_is_never_kill(self):
        for g2 in (True, False, None):
            for g4 in (True, False, None):
                with self.subTest(g2=g2, g4=g4):
                    verdict, _ = an.overall_verdict(
                        {"G1_knee": None, "G2_sT": g2, "G4_mediator": g4,
                         "G5_outcome": False})
                    self.assertEqual(verdict, "FURTHER_EVIDENCE")

    def test_control_g1_fail_is_kill(self):
        verdict, failed = an.overall_verdict(
            {"G1_knee": False, "G2_sT": True, "G4_mediator": True,
             "G5_outcome": True})
        self.assertEqual((verdict, failed), ("KILL", ["G1_knee"]))


class EndToEnd(unittest.TestCase):

    def assertNotKilled(self, out, result):
        self.assertNotEqual(result["verdict"], "KILL")
        self.assertNotIn("KILL TDAS", out)
        self.assertNotIn("TDAS DEAD", out)

    def test_safety_stopped_exp1_full_pipeline_is_further_evidence(self):
        result, out = run(E1_4CORE, E2_PASS, make_e3(True))
        self.assertIsNone(result["gates"]["G1_knee"])
        self.assertEqual(result["verdict"], "FURTHER_EVIDENCE")
        self.assertIn("FURTHER EVIDENCE REQUIRED", out)
        self.assertIn("NOT TESTED/INCONCLUSIVE  G1_knee", out)
        self.assertNotKilled(out, result)

    def test_safety_stopped_with_failing_g2_and_g4_is_still_not_kill(self):
        result, out = run(E1_4CORE, E2_FLAT, make_e3(False))
        self.assertEqual(result["verdict"], "FURTHER_EVIDENCE")
        self.assertIn("Also FAILED: G2_sT, G4_mediator", out)
        self.assertNotKilled(out, result)

    def test_aborted_and_incomplete_exp1_are_further_evidence(self):
        for e1 in (E1_ABORTED, E1_PARTIAL, E1_1CORE_LEGACY,
                   {"experiment": "exp1_thermal"}, None):
            with self.subTest(e1=e1 and e1.get("abort_reason")):
                result, out = run(e1, E2_PASS, make_e3(True))
                self.assertEqual(result["verdict"], "FURTHER_EVIDENCE")
                self.assertNotKilled(out, result)

    def test_exp1_only_safety_stop_reports_further_evidence(self):
        result, out = run(E1_4CORE)
        self.assertIsNone(result["verdict"])
        self.assertIn("further evidence required", out)
        self.assertNotIn("FAIL: no throttle knee", out)

    def test_control_valid_g1_failure_still_kills(self):
        result, out = run(E1_VALID_FAIL, E2_PASS, make_e3(True))
        self.assertIs(result["gates"]["G1_knee"], False)
        self.assertEqual(result["verdict"], "KILL")
        self.assertIn("KILL TDAS", out)
        self.assertIn("Failed structural gate(s): G1_knee", out)

    def test_valid_completed_run_with_knee_proceeds(self):
        result, out = run(E1_KNEE, E2_PASS, make_e3(True))
        self.assertEqual(result["verdict"], "PROCEED")
        self.assertIn("PROCEED (conditionally)", out)

    def test_safety_stop_plus_exp2_knee_end_to_end_keeps_support(self):
        result, out = run(E1_4CORE, E2_KNEE, make_e3(True))
        self.assertIsNone(result["gates"]["G1_knee"])
        self.assertEqual(result["verdict"], "FURTHER_EVIDENCE")
        self.assertIn("supporting: exp2 observed a frequency drop from 79.0 C", out)
        self.assertIn("Supporting: exp2 observed", out)
        self.assertIn("G1_support", result["notes"])
        self.assertNotKilled(out, result)

    def test_missing_exp1_plus_exp2_knee_end_to_end(self):
        result, out = run(None, E2_KNEE, make_e3(True))
        self.assertEqual(result["verdict"], "FURTHER_EVIDENCE")
        self.assertIn("NOT TESTED: no exp1 data", out)
        self.assertNotKilled(out, result)

    def test_valid_knee_with_flat_s_t_still_kills(self):
        result, out = run(E1_KNEE, E2_FLAT, make_e3(True))
        self.assertEqual(result["verdict"], "KILL")
        self.assertIn("Failed structural gate(s): G2_sT", out)
        self.assertIn("TDAS DEAD", out)


if __name__ == "__main__":
    unittest.main()
