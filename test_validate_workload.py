"""
Tests for validate_workload.py: pre-registered constants, pair plans, guards,
the paired bootstrap, criteria, the calibration/acceptance state machine
(reruns, stale parameters, INCOMPLETE), the hardware executor's guards, and
main() (--plan, refusals, interrupted report writing).

    python3 -m unittest -v test_validate_workload

SOFTWARE ONLY: a fake executor or a scripted fake sensor backend stands in for
hardware. Executor tests start small worker pools that run the 'noop' test
kernel only; no matrix multiply, no thermal load, no vcgencmd.
"""

import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import numpy as np

import validate_workload as vw
import workload as wl

HERE = os.path.dirname(os.path.abspath(__file__))
V0 = {"status": "DONE", "n_b": 128, "t_u_ms": {"1": 3.7, "4": 3.9},
      "overhead_ms": {"1": 0.5, "4": 0.9}}


def job(spec, lat_ms, cpu_ms, unit_ms=None, imbalance=0.01, overhead_ms=1.0):
    """A realistic job record built through workload.aggregate_job."""
    P = spec.P
    wall = lat_ms - overhead_ms
    walls = [wall] + [wall * (1 - imbalance * P / max(1, P - 1))] * (P - 1) if P > 1 else [wall]
    shards = []
    for i in range(P):
        u = spec.units_per_shard
        shards.append({"shard": i, "core": i, "pid": 0, "units_done": u,
                       "compute_ms": cpu_ms / P, "sleep_actual_ms": spec.sleep_per_shard_ms,
                       "cpu_ms": cpu_ms / P, "t_start": 0.0, "t_end": walls[i] / 1000,
                       "wall_ms": walls[i], "nivcsw": 0,
                       "unit_ms": (unit_ms if unit_ms is not None else None)})
    return wl.aggregate_job(spec, lat_ms / 1000.0, shards)


class FakeExec:
    """Scripted executor. `invalid` = block ids that come back invalid.
    COOL latency = HOT latency x (1 + gap). Records every call."""

    def __init__(self, invalid=(), gap=0.0, cpu_ratio=2.86, hot_lat=200.0,
                 duty=0.99, v4_shift=0.0, unit_ms_v0=None, raise_at=None,
                 interrupt_at=None):
        self.invalid, self.gap, self.cpu_ratio = set(invalid), gap, cpu_ratio
        self.hot_lat, self.duty, self.v4_shift = hot_lat, duty, v4_shift
        self.unit_ms_v0 = unit_ms_v0 or {64: 0.46, 96: 1.6, 128: 3.7}
        self.raise_at, self.interrupt_at = raise_at, interrupt_at
        self.calls = []

    def __call__(self, block_id, phase, position, plan, params_id, telemetry_on,
                 record_units):
        if self.raise_at == block_id:
            raise vw.CooldownCap("fake cooldown cap")
        if self.interrupt_at == block_id:
            raise KeyboardInterrupt("SIGTERM")
        self.calls.append({"block_id": block_id, "phase": phase, "position": position,
                           "params_id": params_id, "plan": plan,
                           "telemetry_on": telemetry_on})
        jobs = []
        rng = np.random.default_rng(len(self.calls))
        for i, item in enumerate(plan):
            s = item["spec"]
            if s.name == "UNIT":
                base = self.unit_ms_v0[s.n_b] * (1.05 if s.P == 4 else 1.0)
                units = list(base * (1 + 0.004 * rng.standard_normal(s.units_per_shard)))
                r = job(s, base * s.units_per_shard + 1.0, base * s.units_per_shard * s.P,
                        unit_ms=units)
            elif s.name == "ZERO":
                r = job(s, 0.5 if s.P == 1 else 0.9, 0.01, overhead_ms=0.1)
            else:
                noise = 1 + 0.002 * rng.standard_normal()
                lat = self.hot_lat * noise
                if s.name == "COOL":
                    lat *= 1 + self.gap
                if phase == "v4" and telemetry_on:
                    lat *= 1 + self.v4_shift
                cpu_hot = self.hot_lat * s.P * self.duty
                cpu = cpu_hot if s.name == "HOT" else cpu_hot / self.cpu_ratio
                units = [3.7 * (1 + 0.003 * x) for x in rng.standard_normal(s.units_per_shard)]
                r = job(s, lat, cpu, unit_ms=units if record_units else None)
            r.update(pair=item.get("pair"), pos=item.get("pos"), block=block_id, seq=i)
            jobs.append(r)
        valid = block_id not in self.invalid
        return vw.BlockResult(block_id, phase, position, valid,
                              None if valid else "fake guard trip", jobs,
                              params_id=params_id, telemetry_on=telemetry_on)

    def ids(self, phase=None):
        return [c["block_id"] for c in self.calls if phase in (None, c["phase"])]

    def cool_sleep(self, block_id):
        c = next(c for c in self.calls if c["block_id"] == block_id)
        return next(i["spec"].sleep_per_shard_ms for i in c["plan"]
                    if i["spec"].name == "COOL")


def quiet(*a, **k):
    pass


def run_config(P=1, **kw):
    check = kw.pop("calibration_check", None)
    fx = FakeExec(**kw)
    cr = vw.ConfigRun(P, V0, fx, calibration_check=check, log=quiet)
    return cr.run(), fx, cr


class PreregisteredConstants(unittest.TestCase):

    def test_constants_are_the_preregistered_values(self):
        p = vw.PREREG
        self.assertEqual((p["d_cool"], p["l_target_ms"]), (0.35, 200.0))
        self.assertEqual(p["accept_blocks"], {"1": [20, 20, 15], "4": [15, 15, 15, 15]})
        self.assertEqual((p["warmup_pairs"], p["accepted_pairs"], p["calib_pairs"]), (5, 40, 10))
        self.assertEqual((p["reruns_per_config"], p["v0_reruns"], p["v4_reruns"]), (2, 2, 1))
        self.assertEqual((p["guard_max_temp_c"], p["guard_min_freq_mhz"],
                          p["idle_margin_c"], p["cooldown_cap_s"]), (65.0, 1800.0, 2.0, 600.0))
        self.assertEqual((p["n_boot"], p["seed"], p["gap_target"], p["gap_hard"]),
                         (10000, 0, 0.02, 0.05))
        self.assertEqual(p["A2_hot_duty_min"], {"1": 0.95, "4": 0.90})
        self.assertEqual((p["A3_cool_duty_tol"], p["A4_contrast_lo_min"],
                          p["A8_overhead_frac_max"]), (0.05, 2.0, 0.05))
        self.assertEqual(p["A6_unit_cv_max"], {"1": 0.02, "4": 0.03})
        self.assertEqual(p["A6_hot_lat_cv_max"], {"1": 0.03, "4": 0.04})
        self.assertEqual(p["A9_imbalance_max"], {"4": 0.05})
        self.assertEqual((p["unit_min_ms"], p["unit_cv_max_v0"], p["unit_sizes"]),
                         (2.0, 0.02, [64, 96, 128]))

    def test_prereg_document_states_the_same_values(self):
        with open(os.path.join(HERE, "PREREG_workload_validation.md"), encoding="utf-8") as f:
            doc = f.read()
        for s in ("`d = 0.35`", "`L* = 200 ms`", "20, 20 and 15 pairs",
                  "4 blocks of 15 pairs", "first **5 pairs of every block**",
                  "**At most 2 reruns per\n  configuration**", "T ≥ **65 °C**",
                  "below **1800 MHz**", "10 000", "default_rng(0)",
                  "−0.02 ≤ lo and hi ≤ +0.02", "−0.05 ≤ lo and hi ≤ +0.05",
                  "| ≥ 0.95 | ≥ 0.90 |", "| ≥ 2.0 | ≥ 2.0 |", "| ≤ 2%; ≤ 3% | ≤ 3%; ≤ 4% |",
                  "| ≤ 5% | ≤ 5% |", "**67 °C**", "≥ 2.0 ms", "≤ 2%"):
            with self.subTest(text=s):
                self.assertIn(s, doc)


class PairsAndPlans(unittest.TestCase):
    HOT, COOL = wl.JobSpec("HOT", 1, 128, 54), wl.JobSpec("COOL", 1, 128, 19, 129.5)

    def test_pair_order_alternates(self):
        plan = vw.pair_plan(4, self.HOT, self.COOL)
        names = [p["spec"].name for p in plan]
        self.assertEqual(names, ["HOT", "COOL", "COOL", "HOT", "HOT", "COOL", "COOL", "HOT"])
        self.assertEqual([p["pair"] for p in plan], [0, 0, 1, 1, 2, 2, 3, 3])

    def test_warmup_is_by_position_only_and_outliers_stay(self):
        jobs = [job(i["spec"], 200.0, 198.0) for i in vw.pair_plan(8, self.HOT, self.COOL)]
        for j, i in zip(jobs, vw.pair_plan(8, self.HOT, self.COOL)):
            j.update(pair=i["pair"])
        jobs[14]["latency_ms"] = 5000.0                       # pair 7: huge outlier
        acc = vw.accepted_pairs(jobs)
        self.assertEqual([k for k, _, _ in acc], [5, 6, 7])
        self.assertIn(5000.0, [j["latency_ms"] for _, h, c in acc for j in (h, c)])

    def test_block_plans_give_exactly_40_accepted_pairs(self):
        for P, sizes in vw.PREREG["accept_blocks"].items():
            self.assertEqual(sum(n - vw.PREREG["warmup_pairs"] for n in sizes), 40)


class Guards(unittest.TestCase):

    def test_sample_guard(self):
        ok = {"temp_c": 50.0, "freq_mhz": 1800.0, "throttled": 0x0}
        self.assertIsNone(vw.sample_violation(ok))
        self.assertIsNone(vw.sample_violation({**ok, "throttled": 0x50000}))  # sticky only
        for bad in ({**ok, "temp_c": 65.0}, {**ok, "freq_mhz": 1799.0},
                    {**ok, "freq_mhz": None}, {**ok, "temp_c": None},
                    {**ok, "throttled": 0x1}, {**ok, "throttled": 0x4}):
            with self.subTest(sample=bad):
                self.assertIsNotNone(vw.sample_violation(bad))

    def test_sticky_audit(self):
        self.assertIsNone(vw.sticky_violation(0x0, 0x0))
        self.assertIsNone(vw.sticky_violation(0x10000, 0x10000))  # from before the block
        self.assertIsNotNone(vw.sticky_violation(0x0, 0x40000))
        self.assertIsNotNone(vw.sticky_violation(0x0, 0x4))
        self.assertIsNotNone(vw.sticky_violation(None, 0x0))


class PairedBootstrap(unittest.TestCase):

    def data(self, ratio=1.0, n=40):
        h = 200.0 + 10.0 * np.random.default_rng(3).standard_normal(n)
        return h, h * ratio

    def test_reproducible_with_seed_0(self):
        h, c = self.data(1.003)
        h2 = h + np.random.default_rng(9).standard_normal(40)
        a = vw.paired_bootstrap(h2, c, h * 1.0, h / 2.86)
        b = vw.paired_bootstrap(h2, c, h * 1.0, h / 2.86)
        self.assertEqual(a, b)
        self.assertEqual((a["n_boot"], a["seed"], a["n_pairs"]), (10000, 0, 40))

    def test_whole_pairs_are_resampled(self):
        # c = 1.01 h exactly: every paired draw gives g = 0.01 exactly.
        # Independent resampling of h and c could not do this.
        h, c = self.data(1.01)
        r = vw.paired_bootstrap(h, c, h, h / 2.86)
        self.assertAlmostEqual(r["ci_lo"], 0.01, places=12)
        self.assertAlmostEqual(r["ci_hi"], 0.01, places=12)
        self.assertEqual(r["class"], vw.TARGET)
        self.assertAlmostEqual(r["cpu_ratio_lo"], 2.86, places=10)  # same draws

    def test_refuses_unless_exactly_40_pairs(self):
        for n in (39, 41):
            h, c = self.data(1.0, n)
            with self.assertRaises(vw.SampleCountError):
                vw.paired_bootstrap(h, c, h, h)

    def test_classification_bounds_are_inclusive_and_unrounded(self):
        self.assertEqual(vw.classify_gap(-0.02, 0.02), vw.TARGET)
        self.assertEqual(vw.classify_gap(-0.0200000001, 0.0), vw.WITHIN)
        self.assertEqual(vw.classify_gap(0.0, 0.0200000001), vw.WITHIN)
        self.assertEqual(vw.classify_gap(-0.05, 0.05), vw.WITHIN)
        self.assertEqual(vw.classify_gap(-0.0500000001, 0.0), vw.FAIL)
        self.assertEqual(vw.classify_gap(0.0, 0.0500000001), vw.FAIL)

    def test_limitation_is_reported(self):
        h, c = self.data(1.0)
        self.assertIn("interchangeable", vw.paired_bootstrap(h, c, h, h)["limitation"])


class UnitSizeChoice(unittest.TestCase):

    def test_smallest_overhead_among_qualifying(self):
        stats = {"64": {"t_u_ms": 0.46, "cv": 0.01, "overhead_frac": 0.10, "qualifies": False},
                 "96": {"t_u_ms": 2.1, "cv": 0.01, "overhead_frac": 0.02, "qualifies": True},
                 "128": {"t_u_ms": 3.7, "cv": 0.01, "overhead_frac": 0.01, "qualifies": True}}
        self.assertEqual(vw.choose_unit_size(stats), 128)
        for v in stats.values():
            v["qualifies"] = False
        self.assertIsNone(vw.choose_unit_size(stats))


class StateMachine(unittest.TestCase):

    def test_clean_run_accepts_with_frozen_params_and_separate_data(self):
        out, fx, cr = run_config(1)
        self.assertEqual(out["status"], vw.ACCEPTED)
        self.assertEqual(out["reruns_used"], 0)
        self.assertEqual(fx.ids(), ["P1-C1-e1", "P1-C2-e1", "P1-A1-e1", "P1-A2-e1", "P1-A3-e1"])
        fid = out["frozen"]["params_id"]
        for c in fx.calls:
            if c["phase"] == "acceptance":
                self.assertEqual(c["params_id"], fid)
            else:
                self.assertNotIn(c["block_id"], out["acceptance_blocks"])
        self.assertEqual(out["evaluation"]["gap"]["n_pairs"], 40)
        self.assertEqual(out["evaluation"]["gap"]["class"], vw.TARGET)
        # acceptance block sizes 20/20/15 pairs = 40/40/30 jobs
        self.assertEqual([len(c["plan"]) for c in fx.calls if c["phase"] == "acceptance"],
                         [40, 40, 30])

    def test_p4_uses_its_own_calibration_and_four_blocks(self):
        out, fx, _ = run_config(4)
        self.assertEqual(out["status"], vw.ACCEPTED)
        self.assertEqual(out["base"]["u_h"], wl.hot_units(200.0, 0.9, 3.9))
        self.assertEqual([len(c["plan"]) for c in fx.calls if c["phase"] == "acceptance"],
                         [30, 30, 30, 30])

    def test_invalid_c1_is_rerun_from_the_initial_sleep(self):
        out, fx, _ = run_config(1, invalid={"P1-C1-e1"})
        self.assertEqual(out["reruns_used"], 1)
        self.assertEqual(fx.cool_sleep("P1-C1-e1"), fx.cool_sleep("P1-C1-e2"))
        self.assertAlmostEqual(fx.cool_sleep("P1-C1-e2"), out["base"]["S0_ms"])
        self.assertEqual(out["frozen"]["calibration"][0]["block_id"], "P1-C1-e2")

    def test_invalid_c2_before_freeze_reruns_c2_only_from_s1(self):
        out, fx, _ = run_config(1, invalid={"P1-C2-e1"})
        self.assertEqual(fx.ids("calibration"), ["P1-C1-e1", "P1-C2-e1", "P1-C2-e2"])
        s1 = out["frozen"]["calibration"][0]["S_out_ms"]
        self.assertAlmostEqual(fx.cool_sleep("P1-C2-e1"), s1)
        self.assertAlmostEqual(fx.cool_sleep("P1-C2-e2"), s1)
        self.assertEqual(out["reruns_used"], 1)

    def test_invalid_acceptance_block_rerun_same_size_and_position(self):
        out, fx, _ = run_config(1, invalid={"P1-A2-e1"})
        self.assertEqual(out["status"], vw.ACCEPTED)
        self.assertIn("P1-A2-e2", out["acceptance_blocks"])
        self.assertNotIn("P1-A2-e1", out["acceptance_blocks"])
        sizes = {c["block_id"]: len(c["plan"]) for c in fx.calls}
        self.assertEqual(sizes["P1-A2-e1"], sizes["P1-A2-e2"])

    def test_rerun_budget_exhaustion_is_incomplete_and_unclassified(self):
        out, fx, _ = run_config(1, invalid={"P1-C1-e1", "P1-A1-e1", "P1-A3-e1"})
        self.assertEqual(out["status"], vw.INCOMPLETE)
        self.assertEqual(out["reruns_used"], 2)
        self.assertNotIn("evaluation", out)
        self.assertNotIn("P1-A3-e2", fx.ids())                 # never exceeds the limit

    def test_budget_counts_calibration_and_acceptance_together(self):
        out, _, _ = run_config(1, invalid={"P1-C2-e1", "P1-A1-e1"})
        self.assertEqual((out["status"], out["reruns_used"]), (vw.ACCEPTED, 2))

    def test_failed_criterion_is_rejected_not_rerun(self):
        out, fx, _ = run_config(1, gap=0.10)
        self.assertEqual(out["status"], vw.REJECTED)
        self.assertEqual(out["evaluation"]["gap"]["class"], vw.FAIL)
        self.assertEqual(out["reruns_used"], 0)
        self.assertEqual(len(fx.calls), 5)

    def test_low_cpu_contrast_is_rejected(self):
        out, _, _ = run_config(1, cpu_ratio=1.5)
        self.assertEqual(out["status"], vw.REJECTED)
        self.assertFalse(out["evaluation"]["criteria"]["A4"])

    def test_stale_calibration_before_acceptance_is_recalibrated(self):
        seen = []

        def check(frozen):
            seen.append(frozen["params_id"])
            return "calibration block found invalid later" if len(seen) == 1 else None

        out, fx, cr = run_config(1, calibration_check=check)
        self.assertEqual(out["status"], vw.ACCEPTED)
        stale = [f for f in cr.frozen_versions if f["stale"]]
        self.assertEqual([f["params_id"] for f in stale], ["P1-F1"])
        self.assertEqual(out["frozen"]["params_id"], "P1-F2")
        self.assertFalse(out["frozen"]["stale"])
        for c in fx.calls:
            if c["phase"] == "acceptance":
                self.assertEqual(c["params_id"], "P1-F2")  # stale params never used
        self.assertEqual(out["reruns_used"], 2)             # C1 and C2 re-executed

    def test_stale_after_acceptance_started_exhausts_budget(self):
        calls = []

        def check(frozen):
            calls.append(1)
            return "invalidated" if len(calls) == 2 else None

        out, fx, cr = run_config(1, calibration_check=check)
        self.assertEqual(out["status"], vw.INCOMPLETE)
        self.assertEqual(out["stale_blocks"], ["P1-A1-e1"])
        self.assertNotIn("evaluation", out)
        self.assertTrue(cr.frozen_versions[0]["stale"])

    def test_cooldown_cap_makes_config_incomplete(self):
        out, _, _ = run_config(1, raise_at="P1-A2-e1")
        self.assertEqual(out["status"], vw.INCOMPLETE)
        self.assertIn("cooldown", out["reason"])

    def test_evaluation_refuses_other_than_40_pairs(self):
        out, fx, _ = run_config(1)
        hot, cool = wl.make_job_specs(out["frozen"])
        res = fx("X-e1", "acceptance", "X", vw.pair_plan(44, hot, cool), "x", True, True)
        with self.assertRaises(vw.SampleCountError):
            vw.evaluate_config(1, [res.as_dict()])          # 39 accepted pairs


class WholeRun(unittest.TestCase):

    def test_complete_run_and_v4_is_descriptive(self):
        rep = vw.run_all(FakeExec(), vw.Store(None), log=quiet)
        self.assertEqual(rep["overall"], "COMPLETE")
        self.assertEqual(rep["v0"]["n_b"], 128)
        self.assertEqual([rep["configs"][k]["status"] for k in ("1", "4")],
                         [vw.ACCEPTED, vw.ACCEPTED])
        self.assertEqual(rep["v4"]["status"], "DONE")
        self.assertIn("DESCRIPTIVE ONLY", rep["v4"]["label"])

    def test_v4_result_cannot_change_acceptance(self):
        a = vw.run_all(FakeExec(v4_shift=0.0), vw.Store(None), log=quiet)
        b = vw.run_all(FakeExec(v4_shift=0.5), vw.Store(None), log=quiet)
        self.assertGreater(b["v4"]["HOT"]["shift"], 0.4)
        for k in ("1", "4"):
            self.assertEqual(a["configs"][k]["status"], b["configs"][k]["status"])
            self.assertEqual(a["configs"][k]["evaluation"]["criteria"],
                             b["configs"][k]["evaluation"]["criteria"])

    def test_incomplete_p1_stops_validation(self):
        fx = FakeExec(invalid={"P1-C1-e1", "P1-C1-e2", "P1-C1-e3"})
        rep = vw.run_all(fx, vw.Store(None), log=quiet)
        self.assertEqual(rep["overall"], vw.INCOMPLETE)
        self.assertEqual(rep["configs"]["1"]["status"], vw.INCOMPLETE)
        self.assertEqual(rep["configs"]["4"]["status"], vw.NOT_RUN)
        self.assertEqual(rep["v4"]["status"], "NOT_AVAILABLE")
        self.assertFalse(any(i.startswith("P4") for i in fx.ids()))

    def test_no_qualifying_unit_size_is_incomplete(self):
        fx = FakeExec(unit_ms_v0={64: 0.4, 96: 1.2, 128: 1.9})
        rep = vw.run_all(fx, vw.Store(None), log=quiet)
        self.assertEqual(rep["overall"], vw.INCOMPLETE)
        self.assertEqual(rep["configs"]["1"]["status"], vw.NOT_RUN)


class Interruption(unittest.TestCase):
    """C2: finished results survive; the part in progress is INCOMPLETE."""

    def run_interrupted(self, at):
        root = tempfile.mkdtemp(prefix="vw_int_")
        store, report = vw.Store(root), vw.new_report()
        with self.assertRaises(KeyboardInterrupt):
            vw.run_all(FakeExec(interrupt_at=at), store, log=quiet, report=report,
                       checkpoint=lambda: store.json("report.json", report))
        with open(os.path.join(root, "report.json")) as f:
            on_disk = json.load(f)
        leftovers = [n for _, _, fs in os.walk(root) for n in fs if n.endswith(".tmp")]
        return report, on_disk, leftovers

    def test_interrupt_in_p4_keeps_p1_and_marks_p4_incomplete(self):
        report, on_disk, leftovers = self.run_interrupted("P4-A2-e1")
        for r in (report, on_disk):
            self.assertEqual(r["configs"]["1"]["status"], vw.ACCEPTED)
            self.assertEqual(r["configs"]["4"]["status"], vw.INCOMPLETE)
            self.assertIn("interrupted", r["configs"]["4"]["reason"])
            self.assertEqual(r["overall"], vw.INCOMPLETE)
            self.assertIn("SIGTERM", r["interrupted"])
        self.assertNotIn("evaluation", report["configs"]["4"])
        self.assertEqual(leftovers, [])

    def test_interrupt_in_v0_leaves_configs_not_run(self):
        report, on_disk, _ = self.run_interrupted("V0b-e1")
        self.assertEqual(on_disk["v0"]["status"], vw.INCOMPLETE)
        self.assertEqual(on_disk["configs"]["1"]["status"], vw.NOT_RUN)
        self.assertEqual(on_disk["overall"], vw.INCOMPLETE)

    def test_interrupt_in_v4_still_makes_run_incomplete(self):
        report, on_disk, _ = self.run_interrupted("V4on-e1")
        self.assertEqual(on_disk["configs"]["1"]["status"], vw.ACCEPTED)
        self.assertEqual(on_disk["configs"]["4"]["status"], vw.ACCEPTED)
        self.assertEqual(on_disk["v4"]["status"], "NOT_AVAILABLE")
        self.assertEqual(on_disk["overall"], vw.INCOMPLETE)


class P4bConfiguration(unittest.TestCase):
    """PREREG_workload_validation_ADDENDUM_P4b.md: V0b', V0c', then P4b judged
    on PREREG's unchanged P=4 criteria."""

    def test_p4b_constants_are_the_addendum_values(self):
        self.assertEqual(vw.P4B, {"label": "P4b", "P": 4, "n_b": 128, "gemms_per_unit": 4,
                                  "v0b_jobs": 10, "v0b_units": 50, "v0c_jobs": 50,
                                  "telemetry": "sysfs"})

    def test_addendum_states_the_same_values(self):
        with open(os.path.join(os.path.dirname(vw.__file__), vw.P4B_ADDENDUM)) as f:
            doc = f.read()
        for s in ("4 consecutive 128×128 float32 matrix multiplies",
                  "`gemms_per_unit = 4`", "10 P=4 UNIT jobs of 50 units",
                  "50 P=4 ZERO jobs", "15, 15, 15, 15 pairs", "P = 4 column",
                  "Criteria A1–A9", "A REJECTED P4b is final"):
            self.assertIn(s, doc)

    def test_complete_p4b_run(self):
        fx = FakeExec()
        rep = vw.run_p4b(fx, vw.Store(None), log=quiet)
        self.assertEqual((rep["overall"], rep["config_set"]), ("COMPLETE", "P4b"))
        c = rep["configs"]["4b"]
        self.assertEqual((c["status"], c["config"]), (vw.ACCEPTED, "P4b"))
        self.assertEqual(c["frozen"]["params_id"], "P4b-F1")
        self.assertEqual(c["frozen"]["gemms_per_unit"], 4)
        self.assertIn("A9", c["evaluation"]["criteria"])             # P=4 criteria
        self.assertEqual(rep["v0"]["config"], "P4b")
        self.assertEqual(rep["v4"]["status"], "NOT_APPLICABLE")
        self.assertEqual(sorted(rep["configs"]), ["4b"])
        specs = [i["spec"] for call in fx.calls for i in call["plan"]]
        self.assertTrue(specs)
        self.assertTrue(all(s.P == 4 and s.n_b == 128 and s.gemms_per_unit == 4
                            for s in specs))                         # no P=1, no 1-GEMM job
        self.assertEqual(fx.ids("v0"), ["V0b-e1", "V0c-e1"])         # no V0a size selection
        self.assertEqual([i for i in fx.ids() if not i.startswith("V0")],
                         ["P4b-C1-e1", "P4b-C2-e1", "P4b-A1-e1", "P4b-A2-e1",
                          "P4b-A3-e1", "P4b-A4-e1"])
        v0b = next(cl for cl in fx.calls if cl["block_id"] == "V0b-e1")
        self.assertEqual(len(v0b["plan"]), 10)
        self.assertEqual(v0b["plan"][0]["spec"].units_per_shard, 50)
        v0c = next(cl for cl in fx.calls if cl["block_id"] == "V0c-e1")
        self.assertEqual(len(v0c["plan"]), 50)

    def test_p4b_failed_criterion_is_rejected(self):
        rep = vw.run_p4b(FakeExec(gap=0.08), vw.Store(None), log=quiet)
        self.assertEqual(rep["configs"]["4b"]["status"], vw.REJECTED)
        self.assertEqual(rep["overall"], "COMPLETE")

    def test_v0_incomplete_leaves_p4b_not_run(self):
        rep = vw.run_p4b(FakeExec(invalid={"V0b-e1", "V0b-e2", "V0b-e3"}),
                         vw.Store(None), log=quiet)
        self.assertEqual((rep["overall"], rep["v0"]["status"]), (vw.INCOMPLETE, vw.INCOMPLETE))
        self.assertEqual(rep["configs"]["4b"]["status"], vw.NOT_RUN)

    def test_interrupt_in_p4b_is_incomplete(self):
        rep = vw.new_report_p4b()
        with self.assertRaises(KeyboardInterrupt):
            vw.run_p4b(FakeExec(interrupt_at="P4b-A2-e1"), vw.Store(None), log=quiet,
                       report=rep)
        self.assertEqual(rep["overall"], vw.INCOMPLETE)
        self.assertEqual(rep["configs"]["4b"]["status"], vw.INCOMPLETE)
        self.assertIn("during P4b", rep["interrupted"])

    def test_ordinary_configurations_are_unchanged(self):
        out, fx, _ = run_config(P=4)
        self.assertNotIn("gemms_per_unit", out["frozen"])
        self.assertNotIn("config", out)
        self.assertEqual(out["frozen"]["params_id"], "P4-F1")
        self.assertTrue(all(i.startswith("P4-") for i in fx.ids()))
        self.assertTrue(all(i["spec"].gemms_per_unit == 1 for c in fx.calls for i in c["plan"]))


class FakeBackend:
    """Scripted sensor backend. Each *_fn gets the 1-based read count."""
    name = "fakehw"

    def __init__(self, temp=45.0, freq=1800.0, throttled=0,
                 temp_fn=None, freq_fn=None, th_fn=None, events=None):
        self.temp, self.freq, self.th = temp, freq, throttled
        self.temp_fn, self.freq_fn, self.th_fn = temp_fn, freq_fn, th_fn
        self.n, self.lock, self.events = 0, threading.Lock(), events

    def read(self):
        with self.lock:
            self.n += 1
            n = self.n
        if self.events is not None:
            self.events.append("read")
        return {"t_wall": time.monotonic(),
                "temp_c": self.temp_fn(n) if self.temp_fn else self.temp,
                "freq_mhz": self.freq_fn(n) if self.freq_fn else self.freq,
                "throttled": self.th_fn(n) if self.th_fn else self.th,
                "energy_uj": None}


TEST_PREREG = {**vw.PREREG, "cooldown_cap_s": 0.3, "job_timeout_s": 0.4}
T_HOT = wl.JobSpec("HOT", 1, 8, 2)
T_COOL = wl.JobSpec("COOL", 1, 8, 1, 1.0)
# A block lasting > 300 ms, so the 5 Hz telemetry thread is certain to sample.
T_COOL_SLOW = wl.JobSpec("COOL", 1, 8, 1, 150.0)


class ExecutorGuards(unittest.TestCase):
    """HardwareExecutor.execute with a fake backend and the 'noop' kernel."""

    def executor(self, backend):
        ex = vw.HardwareExecutor(backend, 45.0, prereg=TEST_PREREG, kernel="noop",
                                 log=quiet, poll_s=0.01)
        self.addCleanup(ex.close)
        return ex

    def run_block(self, backend, plan=None, telemetry=True):
        ex = self.executor(backend)
        res = ex.execute("T-e1", "acceptance", "T",
                         plan or vw.pair_plan(2, T_HOT, T_COOL_SLOW),
                         "pid", telemetry, False)
        return ex, res

    def test_clean_block_is_valid(self):
        _, res = self.run_block(FakeBackend())
        self.assertTrue(res.valid, res.reason)
        self.assertEqual(len(res.jobs), 4)
        self.assertTrue(res.telemetry)
        self.assertEqual((res.sticky_before, res.sticky_after), (0, 0))

    def test_live_temperature_guard(self):
        # reads: 1 cooldown, 2 flags before, 3 sysfs check after job 1 -> 66 C
        b = FakeBackend(temp_fn=lambda n: 45.0 if n <= 2 else 66.0)
        _, res = self.run_block(b, telemetry=False)
        self.assertFalse(res.valid)
        self.assertIn("temperature 66.0", res.reason)
        self.assertEqual(len(res.jobs), 1)            # stopped after one job

    def test_live_frequency_guard(self):
        _, res = self.run_block(FakeBackend(freq_fn=lambda n: 1800.0 if n <= 2 else 1500.0))
        self.assertFalse(res.valid)
        self.assertIn("frequency 1500", res.reason)

    def test_live_throttle_flag_guard(self):
        _, res = self.run_block(FakeBackend(th_fn=lambda n: 0 if n <= 2 else 0x4))
        self.assertFalse(res.valid)
        self.assertIn("flags", res.reason)

    def test_flags_set_before_block_run_no_jobs(self):
        _, res = self.run_block(FakeBackend(throttled=0x1))
        self.assertFalse(res.valid)
        self.assertIn("before block", res.reason)
        self.assertEqual(res.jobs, [])

    def test_sticky_flag_audit(self):
        # telemetry off: reads 1 cooldown, 2 before, 3-4 after each job, 5 after
        b = FakeBackend(th_fn=lambda n: 0 if n <= 4 else 0x40000)
        _, res = self.run_block(b, vw.pair_plan(1, T_HOT, T_COOL), telemetry=False)
        self.assertFalse(res.valid)
        self.assertIn("sticky", res.reason)
        self.assertEqual(len(res.jobs), 2)

    def test_timeout_invalidates_block_and_resets_pool(self):
        ex = self.executor(FakeBackend())
        old = ex.pool(1)
        plan = [{"spec": wl.JobSpec("COOL", 1, 8, 1, 2000.0), "pair": 0, "pos": 0}]
        res = ex.execute("T-e1", "acceptance", "T", plan, "pid", True, False)
        self.assertFalse(res.valid)
        self.assertIn("JobTimeout", res.reason)
        self.assertNotIn(1, ex.pools)
        self.assertFalse(any(p.is_alive() for p in old.procs))

    def test_pool_start_failure_invalidates_without_cooldown(self):
        b = FakeBackend()
        boom = mock.Mock(side_effect=wl.WorkerFailure("worker 0 died during startup"))
        with mock.patch.object(vw.wl, "FixedWorkPool", boom):
            ex = self.executor(b)
            res = ex.execute("T-e1", "acceptance", "T", vw.pair_plan(1, T_HOT, T_COOL),
                             "pid", True, False)
        self.assertFalse(res.valid)
        self.assertIn("pool start failed", res.reason)
        self.assertEqual((res.jobs, b.n), ([], 0))     # no cooldown, no reads

    def test_pools_start_before_the_cooldown(self):
        events = []

        class RecPool:
            def __init__(self, P, **kw):
                events.append("pool")
                self.P = P

            def alive(self):
                return True

            def stop(self):
                pass

            def run_job(self, spec, record_units=False):
                sh = [{"shard": 0, "core": 0, "units_done": spec.units_per_shard,
                       "compute_ms": 1.0, "sleep_actual_ms": 0.0, "cpu_ms": 1.0,
                       "t_start": 0.0, "t_end": 0.001, "wall_ms": 1.0, "nivcsw": 0,
                       "unit_ms": None}]
                return wl.aggregate_job(spec, 0.0011, sh)

        with mock.patch.object(vw.wl, "FixedWorkPool", RecPool):
            ex = self.executor(FakeBackend(events=events))
            res = ex.execute("T-e1", "acceptance", "T", vw.pair_plan(1, T_HOT, T_COOL),
                             "pid", False, False)
        self.assertTrue(res.valid, res.reason)
        self.assertEqual(events[0], "pool")
        self.assertEqual(events[1], "read")            # the cooldown read comes after

    def test_cooldown_cap_raises(self):
        ex = self.executor(FakeBackend(temp=60.0))     # never within 2 C of 45
        with self.assertRaises(vw.CooldownCap):
            ex.execute("T-e1", "acceptance", "T", vw.pair_plan(1, T_HOT, T_COOL),
                       "pid", True, False)


class FlagBackend(FakeBackend):
    """FakeBackend with a separate throttle-flag reader (vcgencmd on the Pi)."""

    def __init__(self, flag_fn=lambda n: 0, **kw):
        super().__init__(**kw)
        self.flag_fn, self.flag_reads = flag_fn, 0

    def _throttle(self):
        self.flag_reads += 1
        return self.flag_fn(self.flag_reads)


class ExecutorSysfsTelemetryP4b(unittest.TestCase):
    """P4b scheme: sysfs-only samples during jobs, flags read between jobs."""

    def run_block(self, backend, plan=None):
        ex = vw.HardwareExecutor(backend, 45.0, prereg=TEST_PREREG, kernel="noop",
                                 log=quiet, poll_s=0.01, telemetry="sysfs")
        self.addCleanup(ex.close)
        return ex.execute("T-e1", "acceptance", "T",
                          plan or vw.pair_plan(2, T_HOT, T_COOL_SLOW), "pid", True, False)

    def test_clean_block_reads_flags_once_per_job_between_jobs(self):
        b = FlagBackend()
        res = self.run_block(b)
        self.assertTrue(res.valid, res.reason)
        self.assertEqual(b.flag_reads, len(res.jobs))
        self.assertTrue(all(j["throttled_after"] == 0 for j in res.jobs))
        self.assertTrue(res.telemetry)
        self.assertTrue(all(s["throttled"] is None for s in res.telemetry))   # no flags in samples

    def test_live_flag_between_jobs_invalidates(self):
        res = self.run_block(FlagBackend(flag_fn=lambda n: 0 if n < 2 else 0x4))
        self.assertFalse(res.valid)
        self.assertIn("live throttle/under-voltage flags 0x4", res.reason)
        self.assertEqual(len(res.jobs), 2)

    def test_unreadable_flags_between_jobs_invalidate(self):
        res = self.run_block(FlagBackend(flag_fn=lambda n: None))
        self.assertFalse(res.valid)
        self.assertIn("unreadable between jobs", res.reason)

    def test_temperature_guard_still_applies(self):
        b = FlagBackend(temp_fn=lambda n: 45.0 if n <= 2 else 66.0)
        res = self.run_block(b)
        self.assertFalse(res.valid)
        self.assertIn("temperature 66.0", res.reason)

    def test_full_mode_never_reads_flags_between_jobs(self):
        b = FlagBackend()
        ex = vw.HardwareExecutor(b, 45.0, prereg=TEST_PREREG, kernel="noop", log=quiet,
                                 poll_s=0.01)
        self.addCleanup(ex.close)
        res = ex.execute("T-e1", "acceptance", "T", vw.pair_plan(1, T_HOT, T_COOL),
                         "pid", True, False)
        self.assertTrue(res.valid, res.reason)
        self.assertEqual(b.flag_reads, 0)
        self.assertNotIn("throttled_after", res.jobs[0])

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            vw.HardwareExecutor(FakeBackend(), 45.0, telemetry="vcgencmd")


class FakeGuard:
    """Stands in for monitor_guard.MonitorGuard in main() tests (software only;
    the real guard is tested against dummy watchdog processes)."""
    why, made = None, []

    def __init__(self, root, run_id, **kw):
        self.root, self.run_id = root, run_id

    def wait(self, timeout_s):
        FakeGuard.made.append((self.root, self.run_id, timeout_s))
        return FakeGuard.why

    def raise_if_lost(self):
        pass

    def describe(self):
        return {"fake_guard": True}


class WorkerFailureInvalidatesBlock(unittest.TestCase):
    """A worker failure - including a worker that exited between two jobs of a
    block (formerly an uncaught BrokenPipeError) - makes the block invalid: it is
    stored under discarded/, rerun at the same position, and never counted."""

    def test_worker_killed_between_jobs_invalidates_the_block(self):
        holder = {}

        def temp(n):
            # telemetry off: read 1 cooldown, 2 flags before, 3 check after job 1
            if n == 3:
                p = holder["ex"].pools[1].procs[0]
                p.terminate()                      # 'noop' test worker only
                p.join(3)
            return 45.0

        ex = vw.HardwareExecutor(FakeBackend(temp_fn=temp), 45.0, prereg=TEST_PREREG,
                                 kernel="noop", log=quiet, poll_s=0.01)
        self.addCleanup(ex.close)
        holder["ex"] = ex
        res = ex.execute("T-e1", "acceptance", "T", vw.pair_plan(2, T_HOT, T_COOL),
                         "pid", False, False)
        self.assertFalse(res.valid)
        self.assertTrue(res.reason.startswith("WorkerFailure"), res.reason)
        self.assertEqual(len(res.jobs), 1)
        self.assertNotIn(1, ex.pools)              # the broken pool was replaced
        self.assertEqual(ex.close(), [])

    def test_worker_failure_block_is_discarded_rerun_and_not_counted(self):
        fx = FakeExec()

        def execute(block_id, phase, position, plan, params_id, tel, rec):
            res = fx(block_id, phase, position, plan, params_id, tel, rec)
            if block_id == "P1-A2-e1":
                return vw.BlockResult(block_id, phase, position, False,
                                      "WorkerFailure: worker 0 gone before dispatch "
                                      "(BrokenPipeError)", res.jobs[:3],
                                      params_id=params_id, telemetry_on=tel)
            return res

        tmp = tempfile.mkdtemp(prefix="vw_wf_")
        out = vw.ConfigRun(1, V0, execute, vw.Store(tmp), log=quiet).run()
        self.assertEqual(out["status"], vw.ACCEPTED)
        self.assertNotIn("P1-A2-e1", out["acceptance_blocks"])
        self.assertIn("P1-A2-e2", out["acceptance_blocks"])
        self.assertEqual(out["reruns_used"], 1)
        bad = next(h for h in out["history"] if h["block_id"] == "P1-A2-e1")
        self.assertFalse(bad["valid"])
        self.assertTrue(bad["reason"].startswith("WorkerFailure"))
        self.assertTrue(os.path.exists(os.path.join(tmp, "discarded", "P1-A2-e1.json")))
        self.assertFalse(os.path.exists(os.path.join(tmp, "blocks", "P1-A2-e1.json")))
        self.assertEqual(out["evaluation"]["gap"]["n_pairs"], 40)


class Main(unittest.TestCase):
    """main(): --plan, refusals, and report writing. Software only."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vw_main_")
        self.out = os.path.join(self.tmp, "out")
        self.pool_boom = mock.Mock(side_effect=AssertionError("no pool may start"))
        FakeGuard.why, FakeGuard.made = None, []

    def call(self, argv, backend=None, run_all=None, window=0.2):
        patches = [mock.patch.object(vw.wl, "FixedWorkPool", self.pool_boom),
                   mock.patch.object(vw.mg, "MonitorGuard", FakeGuard),
                   mock.patch.dict(vw.PREREG, {"idle_window_s": window})]
        if backend is not None:
            patches.append(mock.patch.object(vw.sensors, "get_backend",
                                             return_value=backend))
        if run_all is not None:
            patches.append(mock.patch.object(vw, "run_all", run_all))
        buf = io.StringIO()
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(buf))
            rc = vw.main(argv)
        return rc, buf.getvalue()

    def report(self, run_id):
        with open(os.path.join(self.out, run_id, "report.json")) as f:
            return json.load(f)

    def test_plan_reads_no_sensor_starts_no_pool_writes_nothing(self):
        gb = mock.Mock(side_effect=AssertionError("no sensor may be read"))
        with mock.patch.object(vw.sensors, "get_backend", gb):
            rc, out = self.call(["--plan", "--outdir", self.out])
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(doc["prereg"]["accepted_pairs"], 40)
        self.assertEqual(sorted(doc["sha256"]), sorted(vw.HASHED_FILES))
        gb.assert_not_called()
        self.pool_boom.assert_not_called()
        self.assertFalse(os.path.exists(self.out))

    def test_p4b_plan_names_the_addendum_and_reads_nothing(self):
        gb = mock.Mock(side_effect=AssertionError("no sensor may be read"))
        with mock.patch.object(vw.sensors, "get_backend", gb):
            rc, out = self.call(["--plan", "--config", "4b", "--outdir", self.out])
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(doc["p4b"], vw.P4B)
        self.assertEqual(doc["prereg"]["A6_unit_cv_max"], {"1": 0.02, "4": 0.03})   # unchanged
        self.assertEqual(sorted(doc["sha256"]), sorted(vw.HASHED_FILES + [vw.P4B_ADDENDUM]))
        gb.assert_not_called()
        self.pool_boom.assert_not_called()
        self.assertFalse(os.path.exists(self.out))

    def test_mock_backend_is_refused(self):
        m = mock.Mock()
        m.name = "mock"
        rc, _ = self.call(["--require-monitor", "1", "--outdir", self.out], backend=m)
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(self.out))

    def test_existing_run_directory_is_refused(self):
        os.makedirs(os.path.join(self.out, "r1"))
        rc, out = self.call(["--require-monitor", "1", "--outdir", self.out, "--run-id", "r1"], backend=FakeBackend())
        self.assertEqual(rc, 2)
        self.assertIn("refusing to overwrite", out)

    def test_no_idle_readings_refuses_before_any_pool(self):
        rc, _ = self.call(["--require-monitor", "1", "--outdir", self.out, "--run-id", "r2"],
                          backend=FakeBackend(temp=None))
        self.assertEqual(rc, 2)
        rep = self.report("r2")
        self.assertEqual(rep["overall"], vw.INCOMPLETE)
        self.assertIn("no valid temperature readings", rep["reason"])
        self.assertNotIn("idle_baseline_c", rep)
        self.pool_boom.assert_not_called()

    def test_interrupt_writes_report_with_finished_results(self):
        def fake_run_all(execute, store, report=None, checkpoint=None, **kw):
            report["configs"]["1"] = {"status": vw.ACCEPTED}
            checkpoint()
            raise KeyboardInterrupt("SIGTERM")

        rc, _ = self.call(["--require-monitor", "1", "--outdir", self.out, "--run-id", "r3"],
                          backend=FakeBackend(), run_all=fake_run_all)
        self.assertEqual(rc, 130)
        rep = self.report("r3")
        self.assertEqual(rep["overall"], vw.INCOMPLETE)
        self.assertIn("SIGTERM", rep["interrupted"])
        self.assertEqual(rep["configs"]["1"]["status"], vw.ACCEPTED)
        self.assertEqual(rep["idle_baseline_c"], 45.0)
        self.assertEqual(rep["monitor"], {"fake_guard": True})
        self.assertTrue(os.path.exists(os.path.join(self.out, "r3", "report.txt")))
        self.assertFalse(any(n.endswith(".tmp")
                             for n in os.listdir(os.path.join(self.out, "r3"))))

    def test_run_without_require_monitor_is_refused_before_anything(self):
        for argv in (["--outdir", self.out, "--run-id", "r5"],
                     ["--require-monitor", "0", "--outdir", self.out, "--run-id", "r5"],
                     ["--require-monitor", "-3", "--outdir", self.out, "--run-id", "r5"],
                     ["--require-monitor", "nan", "--outdir", self.out, "--run-id", "r5"]):
            gb = mock.Mock(side_effect=AssertionError("no sensor may be read"))
            with mock.patch.object(vw.sensors, "get_backend", gb):
                rc, out = self.call(argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("--require-monitor SECONDS (> 0) is mandatory", out)
            gb.assert_not_called()
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(FakeGuard.made, [])
        self.pool_boom.assert_not_called()

    def test_barrier_refusal_writes_report_reads_nothing_starts_nothing(self):
        FakeGuard.why = "no verified watchdog within 1 s: MONITOR_OK not written"
        b = FakeBackend()
        run_all = mock.Mock(side_effect=AssertionError("run_all must not start"))
        rc, _ = self.call(["--require-monitor", "1", "--outdir", self.out, "--run-id", "r6"],
                          backend=b, run_all=run_all)
        self.assertEqual(rc, 2)
        rep = self.report("r6")
        self.assertEqual(rep["overall"], vw.INCOMPLETE)
        self.assertIn("refused: monitoring not established", rep["reason"])
        self.assertNotIn("idle_baseline_c", rep)
        self.assertEqual(b.n, 0)                       # no idle-window read
        run_all.assert_not_called()
        self.pool_boom.assert_not_called()
        self.assertEqual(FakeGuard.made[0][1:], ("r6", 1.0))
        self.assertTrue(os.path.exists(os.path.join(self.out, "r6", "prereg.json")))

    def test_signal_handlers_restored_after_main(self):
        import signal
        before = signal.getsignal(signal.SIGTERM)
        self.call(["--require-monitor", "1", "--outdir", self.out, "--run-id", "r4"], backend=FakeBackend(temp=None))
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)


if __name__ == "__main__":
    unittest.main()
