"""
Tests for diag_p4_preemption.py (the non-confirmatory P=4 telemetry diagnostic).

    python3 -m unittest -v test_diag_p4_preemption

Pure statistics, the report loader, refusals before load, and short end-to-end
runs on the MOCK backend with a real four-worker pool and tiny jobs.
"""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

import diag_p4_preemption as dg


def _job(units, done=None, lat=200.0, niv=2):
    return {"unit_ms": list(units), "units_planned": len(units),
            "units_done": len(units) if done is None else done,
            "latency_ms": lat, "nivcsw": niv}


def _report(d, status="REJECTED", **fr):
    frozen = {"P": 4, "n_b": 16, "u_h": 2, "u_c": 1, "S_ms": 1.0, "params_id": "P4-F1"}
    frozen.update(fr)
    p = os.path.join(d, "report.json")
    with open(p, "w") as f:
        json.dump({"run_id": "wv-test", "configs": {"4": {"status": status, "frozen": frozen}}}, f)
    return p


class Summarize(unittest.TestCase):
    def test_long_units_and_variance_share(self):
        jobs = [_job([3.0] * 99 + [9.0])]
        s = dg.summarize(jobs, 0)
        self.assertEqual((s["jobs"], s["units"]), (1, 100))
        self.assertAlmostEqual(s["unit_median_ms"], 3.0)
        self.assertAlmostEqual(s["share_long"], 0.01)
        self.assertGreater(s["var_share_long"], 0.98)      # one outlier carries the variance
        self.assertEqual(s["unit_robust_cv"], 0.0)          # MAD ignores it
        self.assertGreater(s["unit_cv"], 0.05)

    def test_warmup_jobs_excluded(self):
        jobs = [_job([50.0] * 4, lat=999.0)] + [_job([3.0] * 4)] * 3
        s = dg.summarize(jobs, 1)
        self.assertEqual((s["jobs"], s["unit_median_ms"], s["latency_median_ms"]), (3, 3.0, 200.0))

    def test_incomplete_units_flagged(self):
        self.assertFalse(dg.summarize([_job([3.0] * 4, done=3)], 0)["units_complete"])
        self.assertTrue(dg.summarize([_job([3.0] * 4)], 0)["units_complete"])


class LoadSpec(unittest.TestCase):
    def test_frozen_p4_hot_spec(self):
        d = tempfile.mkdtemp()
        spec, src = dg.load_p4_spec(_report(d, n_b=128, u_h=53))
        self.assertEqual((spec.name, spec.P, spec.n_b, spec.units_per_shard,
                          spec.sleep_per_shard_ms), ("HOT", 4, 128, 53, 0.0))
        self.assertEqual((src["params_id"], src["status"]), ("P4-F1", "REJECTED"))

    def test_missing_or_wrong_parameters_refused(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "r.json")
        for rep in ({"configs": {"4": {"frozen": {"P": 1, "n_b": 128, "u_h": 53}}}},
                    {"configs": {"4": {"frozen": {"P": 4, "n_b": 128}}}},
                    {"configs": {"1": {"frozen": {"P": 1, "n_b": 128, "u_h": 54}}}}):
            with self.subTest(rep=rep):
                with open(p, "w") as f:
                    json.dump(rep, f)
                with self.assertRaises(SystemExit):
                    dg.load_p4_spec(p)


class FakePi:
    name = "raspberrypi"

    def __init__(self, th=0):
        self.th = th

    def read(self):
        return {"t_wall": 0.0, "temp_c": 45.0, "throttled": self.th}


def _run(argv, backend=None):
    buf = io.StringIO()
    patches = [contextlib.redirect_stdout(buf)]
    if backend is not None:
        patches.append(mock.patch.object(dg.sensors, "get_backend", return_value=backend))
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        rc = dg.main(argv)
    return rc, buf.getvalue()


class RefusalsBeforeLoad(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="diag_test_")
        self.rep = _report(self.d)
        self.gov = []
        for c in range(4):
            g = os.path.join(self.d, f"gov{c}")
            with open(g, "w") as f:
                f.write("performance\n")
            self.gov.append(g)
        t = os.path.join(self.d, "temp")
        with open(t, "w") as f:
            f.write("45000\n")
        self.patches = [mock.patch.object(dg, "GOVERNORS", self.gov),
                        mock.patch.object(dg, "THERMAL", t),
                        mock.patch.object(dg.read_sysfs_temp, "__defaults__", (t,)),
                        mock.patch.object(dg.wl, "FixedWorkPool",
                                          side_effect=AssertionError("load started"))]

    def run_hw(self, backend, name):
        out = os.path.join(self.d, name)
        with contextlib.ExitStack() as st:
            for p in self.patches:
                st.enter_context(p)
            with mock.patch.object(dg.os, "getloadavg", return_value=(0.1, 0.1, 0.1)):
                rc, text = _run(["--report", self.rep, "--outdir", out], backend)
        return rc, text, out

    def test_governor_not_performance(self):
        with open(self.gov[2], "w") as f:
            f.write("ondemand\n")
        rc, text, out = self.run_hw(FakePi(), "g")
        self.assertEqual(rc, 2)
        self.assertIn("not performance", text)
        self.assertFalse(os.path.exists(out))

    def test_throttle_flags_set(self):
        rc, text, out = self.run_hw(FakePi(th=0x80000), "t")
        self.assertEqual(rc, 2)
        self.assertIn("0x80000", text)
        self.assertFalse(os.path.exists(out))

    def test_hot_start(self):
        with open(os.path.join(self.d, "temp"), "w") as f:
            f.write("56000\n")
        rc, text, out = self.run_hw(FakePi(), "h")
        self.assertEqual(rc, 2)
        self.assertIn("need < 55.0", text)

    def test_existing_outdir_untouched(self):
        out = os.path.join(self.d, "exists")
        os.makedirs(out)
        with open(os.path.join(out, "keep"), "w") as f:
            f.write("x")
        with mock.patch.object(dg.wl, "FixedWorkPool", side_effect=AssertionError("load")):
            rc, text = _run(["--backend", "mock", "--report", self.rep, "--outdir", out])
        self.assertEqual(rc, 2)
        self.assertEqual(os.listdir(out), ["keep"])


class EndToEndMock(unittest.TestCase):
    """Real four-worker pool, tiny gemm jobs (n_b 16, 2 units per shard)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="diag_e2e_")
        self.rep = _report(self.d)

    def meta(self, out):
        with open(os.path.join(out, "diag.meta.json")) as f:
            return json.load(f)

    def test_complete_run_interleaves_all_conditions(self):
        out = os.path.join(self.d, "ok")
        rc, text = _run(["--backend", "mock", "--report", self.rep, "--outdir", out,
                         "--jobs-per-block", "4", "--warmup", "1"])
        self.assertEqual(rc, 0, text)
        m = self.meta(out)
        self.assertTrue(m["complete"])
        self.assertEqual([b["condition"] for b in m["blocks"]], list(dg.ORDER))
        self.assertEqual([b["jobs_run"] for b in m["blocks"]], [4] * 6)
        for c in dg.CONDITIONS:
            s = m["by_condition"][c]
            self.assertEqual((s["jobs"], s["units"]), (6, 6 * 8))
            self.assertTrue(s["units_complete"])
        self.assertEqual(m["unstopped_workers"], [])
        self.assertIn("DIAGNOSTIC", m["label"])
        with open(os.path.join(out, "diag.jobs.jsonl")) as f:
            jobs = [json.loads(x) for x in f]
        self.assertEqual(len(jobs), 24)
        self.assertTrue(all(len(j["unit_ms"]) == 8 and "shards" not in j for j in jobs))
        with open(os.path.join(out, "diag.telemetry.jsonl")) as f:
            conds = {json.loads(x)["condition"] for x in f}
        self.assertNotIn("T0", conds)                        # T0 = no telemetry

    def test_temperature_stop_before_any_job(self):
        out = os.path.join(self.d, "hot")
        rc, text = _run(["--backend", "mock", "--report", self.rep, "--outdir", out,
                         "--jobs-per-block", "4", "--warmup", "1", "--max-temp", "40"])
        self.assertEqual(rc, 0)
        m = self.meta(out)
        self.assertFalse(m["complete"])
        self.assertIn("max_temp", m["stop_reason"])
        self.assertEqual(len(m["blocks"]), 1)
        self.assertEqual(m["blocks"][0]["jobs_run"], 0)

    def test_interrupt_saves_data_and_stops_pool(self):
        out = os.path.join(self.d, "int")
        real = dg.wl.FixedWorkPool
        made = []

        class Pool(real):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.n = 0
                made.append(self)

            def run_job(self, *a, **k):
                self.n += 1
                if self.n == 7:
                    raise KeyboardInterrupt("SIGTERM")
                return super().run_job(*a, **k)

        with mock.patch.object(dg.wl, "FixedWorkPool", Pool):
            rc, text = _run(["--backend", "mock", "--report", self.rep, "--outdir", out,
                             "--jobs-per-block", "4", "--warmup", "1"])
        self.assertEqual(rc, 130)
        m = self.meta(out)
        self.assertEqual(m["interrupted"], "interrupted (SIGTERM)")
        self.assertFalse(m["complete"])
        self.assertEqual([b["jobs_run"] for b in m["blocks"]], [4, 2])  # partial block kept
        with open(os.path.join(out, "diag.jobs.jsonl")) as f:
            self.assertEqual(sum(1 for _ in f), 6)
        self.assertTrue(made[0]._stopped)
        self.assertFalse(any(p.is_alive() for p in made[0].procs))


class NeverSigkill(unittest.TestCase):
    def test_source_sends_no_signals_itself(self):
        with open(dg.__file__) as f:
            src = f.read()
        # code that would send a signal; the docstring's "never SIGKILL" is not one
        hit = re.search(r"signal\.SIGKILL|\.kill\(|os\.kill|killpg|\.terminate\(|\.send_signal\(", src)
        self.assertIsNone(hit, hit and hit.group(0))


if __name__ == "__main__":
    unittest.main()
