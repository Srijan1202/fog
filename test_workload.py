"""
Tests for the fixed-work jobs in workload.py (Amendment A2).

    python3 -m unittest -v test_workload

Pure aggregation and calibration arithmetic, plus a few real worker pools that
use only the test kernels ('noop', 'spin:<ms>'): milliseconds of CPU in total,
never the gemm kernel, no thermal load.
"""

import os
import time
import unittest

import workload as wl


def shard(i, units, compute, sleep, cpu, t0, wall):
    return {"shard": i, "core": i, "pid": 0, "units_done": units,
            "compute_ms": compute, "sleep_actual_ms": sleep, "cpu_ms": cpu,
            "t_start": t0, "t_end": t0 + wall / 1000.0, "wall_ms": wall,
            "nivcsw": 0, "unit_ms": None}


class Aggregation(unittest.TestCase):

    def test_cpu_latency_duty_overhead_imbalance_skew(self):
        spec = wl.JobSpec("HOT", 2, 128, 10, 0.0)
        shards = [shard(0, 10, 95.0, 0.0, 96.0, 100.000, 100.0),
                  shard(1, 10, 88.0, 0.0, 92.0, 100.001, 90.0)]
        r = wl.aggregate_job(spec, 0.102, shards, 0.0005, t_dispatch=99.9995)
        self.assertEqual((r["units_planned"], r["units_done"]), (20, 20))
        self.assertAlmostEqual(r["latency_ms"], 102.0)
        self.assertAlmostEqual(r["cpu_ms"], 188.0)
        self.assertAlmostEqual(r["compute_ms"], 183.0)
        self.assertAlmostEqual(r["realized_duty"], 188.0 / (102.0 * 2))
        self.assertAlmostEqual(r["overhead_ms"], 2.0)          # latency - slowest shard
        self.assertAlmostEqual(r["imbalance"], (100.0 - 95.0) / 100.0)
        self.assertAlmostEqual(r["skew_ms"], 1.0, places=6)
        self.assertAlmostEqual(r["start_lag_ms"], 0.5, places=6)
        self.assertAlmostEqual(r["parent_cpu_ms"], 0.5)

    def test_duty_is_measured_not_taken_from_settings(self):
        spec = wl.JobSpec("COOL", 1, 128, 19, 130.0)          # nominal 0.35
        r = wl.aggregate_job(spec, 0.200, [shard(0, 19, 70.0, 128.0, 50.0, 0.0, 199.0)])
        self.assertAlmostEqual(r["realized_duty"], 50.0 / 200.0)  # 0.25, not 0.35

    def test_sleep_accounting(self):
        spec = wl.JobSpec("COOL", 4, 128, 19, 120.0)
        shards = [shard(i, 19, 70.0, 121.0 + i, 70.0, 0.0, 192.0) for i in range(4)]
        r = wl.aggregate_job(spec, 0.195, shards)
        self.assertAlmostEqual(r["sleep_req_ms"], 480.0)        # 4 shards x 120 ms
        self.assertAlmostEqual(r["sleep_actual_ms"], 121 + 122 + 123 + 124)

    def test_incomplete_work_is_visible(self):
        spec = wl.JobSpec("HOT", 1, 128, 54)
        r = wl.aggregate_job(spec, 0.1, [shard(0, 53, 190.0, 0.0, 190.0, 0.0, 195.0)])
        self.assertNotEqual(r["units_done"], r["units_planned"])


class SpecsAndMultisets(unittest.TestCase):
    FROZEN = {"P": 4, "n_b": 128, "u_h": 50, "u_c": 18, "S_ms": 118.5}

    def test_make_job_specs(self):
        hot, cool = wl.make_job_specs(self.FROZEN)
        self.assertEqual((hot.name, hot.units_per_shard, hot.sleep_per_shard_ms),
                         ("HOT", 50, 0.0))
        self.assertEqual((cool.name, cool.units_per_shard, cool.sleep_per_shard_ms),
                         ("COOL", 18, 118.5))
        self.assertEqual(hot.n_b, cool.n_b)                     # same kernel
        self.assertEqual((hot.planned_units, cool.planned_units), (200, 72))

    def test_every_ordering_has_identical_planned_work(self):
        hot, cool = wl.make_job_specs(self.FROZEN)
        ms = wl.make_multiset(30, 30, hot, cool)
        ref = wl.planned_totals(ms)
        import numpy as np
        for mode in wl.ORDERINGS:
            with self.subTest(mode=mode):
                seq = wl.order_sequence(ms, mode, np.random.default_rng(1))
                self.assertEqual(wl.planned_totals(seq), ref)
                self.assertEqual(sorted(j.name for j in seq),
                                 sorted(j.name for j in ms))


class CalibrationArithmetic(unittest.TestCase):

    def test_hot_units_uses_measured_values(self):
        self.assertEqual(wl.hot_units(200.0, 0.5, 3.7), 54)
        self.assertEqual(wl.hot_units(200.0, 0.9, 3.9), 51)     # P=4, slower units
        self.assertEqual(wl.hot_units(1.0, 5.0, 3.7), 1)        # never below 1

    def test_cool_units_and_initial_sleep(self):
        self.assertEqual(wl.cool_units(54, 0.35), 19)
        self.assertEqual(wl.cool_units(1, 0.35), 1)
        self.assertAlmostEqual(wl.initial_sleep_ms(54, 19, 3.7), 35 * 3.7)

    def test_adjust_sleep_by_median_gap_and_clamp(self):
        S, clamped = wl.adjust_sleep_ms(100.0, [200.0] * 5, [210.0] * 5)
        self.assertEqual((S, clamped), (90.0, False))
        S, clamped = wl.adjust_sleep_ms(5.0, [200.0] * 5, [230.0] * 5)
        self.assertEqual((S, clamped), (0.0, True))


class PoolWithTestKernels(unittest.TestCase):
    """Real worker processes; test kernels only (no gemm, no thermal load)."""

    def test_fixed_work_completes_and_latency_tracks_unit_speed(self):
        lat = {}
        for kern in ("spin:1", "spin:3"):
            pool = wl.FixedWorkPool(2, kernel=kern)
            try:
                r = pool.run_job(wl.JobSpec("HOT", 2, 8, 5))
            finally:
                pool.stop()
            self.assertEqual(r["units_done"], r["units_planned"])
            lat[kern] = r["latency_ms"]
        # Fixed work: three times slower units -> clearly longer latency.
        self.assertGreater(lat["spin:3"], 1.8 * lat["spin:1"])

    def test_cpu_time_reflects_compute_not_sleep(self):
        pool = wl.FixedWorkPool(2, kernel="spin:2")
        try:
            r = pool.run_job(wl.JobSpec("COOL", 2, 8, 4, 40.0), record_units=True)
        finally:
            pool.stop()
        self.assertEqual(r["units_done"], 8)
        self.assertGreaterEqual(r["sleep_actual_ms"], 0.95 * r["sleep_req_ms"])
        self.assertLess(r["cpu_ms"], 0.5 * r["latency_ms"] * 2)  # mostly asleep
        self.assertLess(r["realized_duty"], 0.5)
        self.assertEqual(len(r["unit_ms"]), 8)

    def test_worker_cpu_counts_every_thread(self):
        # C4: work done in a helper thread must appear in the job's CPU time.
        # time.thread_time() in the worker would report ~0 here.
        pool = wl.FixedWorkPool(1, kernel="threadspin:5")
        try:
            r = pool.run_job(wl.JobSpec("HOT", 1, 8, 4))
        finally:
            pool.stop()
        self.assertGreaterEqual(r["cpu_ms"], 0.8 * 4 * 5.0)

    def test_single_thread_cpu_matches_compute(self):
        # With the single-threaded kernel, CPU time ~= time inside the kernel,
        # so duty and contrast calculations are unchanged by process_time.
        pool = wl.FixedWorkPool(1, kernel="spin:3")
        try:
            r = pool.run_job(wl.JobSpec("HOT", 1, 8, 6))
        finally:
            pool.stop()
        self.assertAlmostEqual(r["cpu_ms"] / r["compute_ms"], 1.0, delta=0.15)
        self.assertAlmostEqual(r["realized_duty"], r["cpu_ms"] / r["latency_ms"])

    def test_zero_job_measures_coordination_overhead(self):
        pool = wl.FixedWorkPool(2, kernel="noop")
        try:
            r = pool.zero_job(8)
        finally:
            pool.stop()
        self.assertEqual((r["units_planned"], r["units_done"]), (0, 0))
        self.assertGreater(r["latency_ms"], 0.0)
        self.assertEqual(len(r["shards"]), 2)

    def test_timeout_raises_and_stop_leaves_no_workers(self):
        pool = wl.FixedWorkPool(2, kernel="noop", timeout_s=0.3)
        try:
            with self.assertRaises(wl.JobTimeout):
                pool.run_job(wl.JobSpec("COOL", 2, 8, 1, 2000.0))
        finally:
            pool.stop()
        self.assertFalse(any(p.is_alive() for p in pool.procs))

    def test_spec_for_other_P_is_rejected(self):
        pool = wl.FixedWorkPool(1, kernel="noop")
        try:
            with self.assertRaises(ValueError):
                pool.run_job(wl.JobSpec("HOT", 2, 8, 1))
        finally:
            pool.stop()

    def test_worker_gone_before_dispatch_is_a_worker_failure(self):
        # A worker that exited between two jobs must give WorkerFailure (block
        # invalid, as PREREG specifies), not an uncaught BrokenPipeError.
        pool = wl.FixedWorkPool(2, kernel="noop")
        try:
            pool.procs[1].terminate()          # test kernel worker only
            pool.procs[1].join(3)
            with self.assertRaises(wl.WorkerFailure):
                pool.run_job(wl.JobSpec("HOT", 2, 8, 1))
        finally:
            pool.stop()
        self.assertEqual(pool.unstopped, [])
        self.assertFalse(any(p.is_alive() for p in pool.procs))

    @unittest.skipUnless(hasattr(os, "sched_setaffinity") and (os.cpu_count() or 1) >= 2,
                         "core pinning needs Linux and 2+ cores")
    def test_workers_are_pinned_to_cores(self):
        pool = wl.FixedWorkPool(2, kernel="noop")
        try:
            self.assertEqual(pool.pinned, [0, 1])
            r = pool.run_job(wl.JobSpec("HOT", 2, 8, 1))
            self.assertEqual(sorted(s["core"] for s in r["shards"]), [0, 1])
        finally:
            pool.stop()


class LegacyHeatGeneratorUnchanged(unittest.TestCase):

    def test_deadline_workload_still_runs_for_exp1(self):
        w = wl.Workload("HOT", 16, duty=1.0, target_ms=20.0)
        t0 = time.perf_counter()
        lat = w.run_once()
        self.assertGreaterEqual(lat, 20.0)
        self.assertLess(time.perf_counter() - t0, 1.0)


if __name__ == "__main__":
    unittest.main()
