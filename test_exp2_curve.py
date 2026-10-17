"""
Tests for exp2_curve.py (Experiment 2 under Amendment A4).

    python3 -m unittest -v test_exp2_curve

The ramp loop runs with fake backends, pools, clocks and flag readers (the
hardware path, without hardware); main() runs end to end on the MOCK backend,
which is simulated and sleep-based (no CPU load). No hardware load runs here.
"""

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import analyze as an
import exp2_curve as e2
import monitor_guard as mg
import workload as wl

HOT = wl.JobSpec("HOT", 4, 128, 13, 0.0, 4)
HERE = os.path.dirname(os.path.abspath(__file__))


class FakeBackend:
    """Temperature rises 1 C per job while powered; falls 5 C per read when not."""
    name = "fake"

    def __init__(self, temp=40.0):
        self.temp = temp

    def read(self):
        return {"t_wall": time.monotonic(), "temp_c": self.temp, "freq_mhz": 1800.0,
                "throttled": 0}


class FakePool:
    def __init__(self, b, fail_at=None, short_at=None, exc=wl.JobTimeout):
        self.b, self.n, self.fail_at, self.short_at, self.exc = b, 0, fail_at, short_at, exc
        self.stopped = False

    def alive(self):
        return True

    def run_job(self, spec, record_units=False):
        self.n += 1
        if self.fail_at == self.n:
            raise self.exc("no reply from shard(s) [2]")
        time.sleep(0.002)
        self.b.temp += 1.0
        done = spec.planned_units - (1 if self.short_at == self.n else 0)
        return {"class": spec.name, "units_planned": spec.planned_units, "units_done": done,
                "latency_ms": 196.0 + 0.1 * self.b.temp, "cpu_ms": 780.0}

    def stop(self):
        self.stopped = True
        return []


class FakeClock:
    def __init__(self, values=(1800.0,)):
        self.values, self.i = list(values), 0

    def read(self):
        v = self.values[self.i % len(self.values)]
        self.i += 1
        return v


def flags_seq(*vals, default=0x0):
    it = iter(vals)
    return lambda: next(it, default)


def plan(**kw):
    base = dict(ramps=2, ramp_s=0.3, idle_c=40.0, margin_c=3.0, cooldown_cap_s=2.0, hz=50.0,
                clock_hz=50.0, poll_s=0.01, progress_s=100.0)
    base.update(kw)
    return e2.Plan(**base)


def ramps(b=None, pool=None, flags=None, check=None, clock=None, **kw):
    b = b or FakeBackend()
    pool = pool or FakePool(b)
    power = []

    def pw(on):
        power.append(on)
        if not on:
            b.temp = 40.0                    # instant cooldown between fake ramps
    r = e2.Ramps(b, pool, HOT, clock or FakeClock(), plan(**kw), check=check,
                 flags=flags or (lambda: 0x0), power=pw, log=lambda s: None).run()
    return r, power


class RampLoop(unittest.TestCase):
    def test_two_complete_ramps(self):
        b = FakeBackend()
        # throttling live during jobs (+ sticky): expected in Experiment 2, not an abort
        r, power = ramps(b=b, flags=lambda: 0x20002 if b.temp > 40.0 else 0x20000)
        self.assertIsNone(r.abort_reason)
        self.assertEqual(r.ramps_completed, 2)
        self.assertEqual(power, [True, False, True, False])
        self.assertTrue(r.jobs_complete)
        self.assertFalse(r.undervoltage)
        self.assertEqual({j["ramp"] for j in r.jobs}, {0, 1})
        j = r.jobs[0]
        self.assertEqual(j["temp_before_c"], 40.0)                  # read before dispatch
        self.assertLessEqual(j["t_start"], j["t_end"])
        self.assertEqual(j["throttled_after"], 0x20002)
        self.assertEqual([x["seq"] for x in r.jobs], list(range(len(r.jobs))))
        self.assertTrue(r.tele and all("ramp" in s and s["throttled"] is None for s in r.tele))
        self.assertTrue(r.clock_samples)
        self.assertEqual((r.ramp_info[0]["flags_before"], r.ramp_info[0]["flags_after"]),
                         ("0x20000", "0x20000"))

    def test_cooldown_cap_is_incomplete_without_load(self):
        b = FakeBackend(temp=60.0)
        r, power = ramps(b=b, cooldown_cap_s=0.05)
        self.assertIn("INCOMPLETE: ramp 0 cooldown", r.abort_reason)
        self.assertEqual((r.jobs, r.ramps_completed, power), ([], 0, []))

    def test_cooldown_waits_for_idle_plus_margin(self):
        b = FakeBackend(temp=50.0)
        calls = []

        def quick():
            calls.append(b.temp)
            b.temp -= 2.0
            return {"temp_c": calls[-1]}
        rr = e2.Ramps(b, FakePool(b), HOT, FakeClock(), plan(), quick=quick, log=lambda s: None)
        t, _ = rr.cooldown()
        self.assertEqual(t, 42.0)                                  # first reading <= 43.0
        self.assertEqual(calls, [50.0, 48.0, 46.0, 44.0, 42.0])

    def test_undervoltage_between_jobs_aborts(self):
        r, _ = ramps(flags=flags_seq(0x0, 0x0, 0x0, 0x10001))
        self.assertTrue(r.undervoltage)
        self.assertIn("under-voltage between jobs (0x10001)", r.abort_reason)
        self.assertEqual((len(r.jobs), r.ramps_completed), (3, 0))

    def test_sticky_undervoltage_before_ramp_aborts_before_load(self):
        r, power = ramps(flags=lambda: 0x10000)
        self.assertTrue(r.undervoltage)
        self.assertIn("before ramp 0", r.abort_reason)
        self.assertEqual((r.jobs, power), ([], []))

    def test_live_flags_before_ramp_abort(self):
        r, _ = ramps(flags=flags_seq(0x4))
        self.assertIn("live throttle flags before ramp 0", r.abort_reason)
        self.assertFalse(r.undervoltage)
        self.assertEqual(r.jobs, [])

    def test_unreadable_flags_abort(self):
        r, _ = ramps(flags=flags_seq(0x0, 0x0, None))
        self.assertIn("throttle flags unreadable between jobs", r.abort_reason)

    def test_sticky_undervoltage_after_ramp_aborts(self):
        b = FakeBackend()
        rr = e2.Ramps(b, FakePool(b), HOT, FakeClock(), plan(ramps=1), log=lambda s: None)
        # 0x0 before the ramp and between jobs; the sticky bit only in the audit after
        # it (the ramp's summary, with "jobs", is written just before that read)
        rr.flags = lambda: 0x10000 if "jobs" in rr.ramp_info[-1] else 0x0
        rr.run()
        self.assertGreater(len(rr.jobs), 0)
        self.assertTrue(rr.undervoltage)
        self.assertIn("under-voltage after ramp 0", rr.abort_reason)
        self.assertEqual(rr.ramps_completed, 0)

    def test_job_timeout_aborts_and_marks_incomplete(self):
        b = FakeBackend()
        r, power = ramps(b=b, pool=FakePool(b, fail_at=4))
        self.assertIn("JobTimeout", r.abort_reason)
        self.assertFalse(r.jobs_complete)
        self.assertEqual((len(r.jobs), power), (3, [True, False]))

    def test_worker_failure_aborts(self):
        b = FakeBackend()
        r, _ = ramps(b=b, pool=FakePool(b, fail_at=2, exc=wl.WorkerFailure))
        self.assertIn("WorkerFailure", r.abort_reason)
        self.assertFalse(r.jobs_complete)

    def test_short_job_aborts(self):
        b = FakeBackend()
        r, _ = ramps(b=b, pool=FakePool(b, short_at=2))
        self.assertFalse(r.jobs_complete)
        self.assertIn("did 51 of 52 units", r.abort_reason)

    def test_max_temp_stops_before_the_job(self):
        r, _ = ramps(max_temp=45.0)
        self.assertIn("max_temp: 45.0 C >= 45.0", r.abort_reason)
        self.assertEqual([j["temp_before_c"] for j in r.jobs], [40.0, 41.0, 42.0, 43.0, 44.0])

    def test_lost_watchdog_interrupts_and_cleans_up(self):
        n = {"k": 0}

        def check():
            n["k"] += 1
            if n["k"] == 6:
                raise mg.MonitorLost("watchdog lost: monitor.log missing")
        r, power = ramps(check=check)
        self.assertIn("interrupted (watchdog lost", r.interrupted)
        self.assertEqual(r.abort_reason, r.interrupted)
        self.assertEqual(power, [True, False])
        self.assertTrue(r.tele and r.clock_samples)               # kept, threads stopped
        self.assertEqual(r.ramps_completed, 0)

    def test_interrupt_during_cooldown(self):
        def check():
            raise KeyboardInterrupt("SIGTERM")
        r, power = ramps(check=check)
        self.assertEqual(r.interrupted, "interrupted (SIGTERM)")
        self.assertEqual((r.jobs, power), ([], []))


class Descriptive(unittest.TestCase):
    def test_clock_summary(self):
        s = [{"t_start": 0.0, "t_end": 0.001, "arm_clock_mhz": 1800.0},
             {"t_start": 1.0, "t_end": 1.003, "arm_clock_mhz": None}]
        ck = e2.clock_summary(s)
        self.assertEqual((ck["reads"], ck["readable"], ck["readable_fraction"]), (2, 1, 0.5))
        self.assertAlmostEqual(ck["read_wall_ms_max"], 3.0, places=3)

    def test_attach_clock_uses_reads_inside_the_job(self):
        jobs = [{"t_start": 0.0, "t_end": 0.2}, {"t_start": 0.3, "t_end": 0.5},
                {"t_start": 0.6, "t_end": 0.8}]
        s = [{"t_start": 0.09, "t_end": 0.11, "arm_clock_mhz": 1800.0},
             {"t_start": 0.39, "t_end": 0.41, "arm_clock_mhz": 1500.0},
             {"t_start": 0.42, "t_end": 0.44, "arm_clock_mhz": 1600.0},
             {"t_start": 0.69, "t_end": 0.71, "arm_clock_mhz": None}]
        e2.attach_clock(jobs, s)
        self.assertEqual([j["arm_clock_mhz"] for j in jobs], [1800.0, 1550.0, None])

    def test_mediation_predicts_from_the_fastest_level(self):
        jobs = ([{"arm_clock_mhz": 1800.0, "latency_ms": 196.0}] * 3
                + [{"arm_clock_mhz": 1500.0, "latency_ms": 235.0}] * 2
                + [{"arm_clock_mhz": None, "latency_ms": 500.0}])
        m = e2.mediation(jobs)
        self.assertEqual(m["jobs_with_clock"], 5)
        lv = m["levels"]
        self.assertEqual([(x["clock_mhz"], x["n"]) for x in lv], [(1800.0, 3), (1500.0, 2)])
        self.assertAlmostEqual(lv[1]["predicted_ms"], 196.0 * 1800 / 1500)

    def test_support_knee_pairs_clock_with_nearest_temperature(self):
        tele = [{"t_wall": float(t), "temp_c": 70.0 + t} for t in range(15)]
        s = [{"t_start": t - 0.01, "t_end": t + 0.01,
              "arm_clock_mhz": 1800.0 if 70 + t < 81 else 1600.0} for t in range(15)]
        s.append({"t_start": 100.0, "t_end": 100.01, "arm_clock_mhz": 1000.0})  # no temp near
        k = e2.support_knee(s, tele)
        self.assertEqual((k["knee_c"], k["f_min"]), (81.0, 1600.0))

    def test_outcome_follows_classify_g2(self):
        import test_analyze as ta
        self.assertEqual(e2.exp2_outcome(ta.make_e2(0.8))[0], "PASS")
        self.assertEqual(e2.exp2_outcome(ta.make_e2(0.0))[0], "FAIL")
        st, lines = e2.exp2_outcome({**ta.make_e2(0.8), "interrupted": "interrupted (SIGTERM)"})
        self.assertEqual(st, "INCONCLUSIVE")
        self.assertNotIn("KILL", "\n".join(lines))


class FakePi:
    name = "raspberrypi"


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = e2.main(argv)
    return rc, out.getvalue()


ACCEPTED = ({"accepted": True, "mock": False, "hot": HOT, "cool": HOT, "config": "4c",
             "telemetry": "sysfs", "frozen": {}, "validation_run": "wv-p4c", "report": None},
            None)


class Refusals(unittest.TestCase):
    """Real hardware: every refusal happens before any file or load."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "run")
        p = mock.patch.object(e2.sensors, "get_backend", return_value=FakePi())
        p.start()
        self.addCleanup(p.stop)

    def argv(self, *extra):
        return ["--outdir", self.out, "--require-monitor", "1", "--exp1-meta", "e1.json",
                "--workload-validation", "rep.json", *extra]

    def test_require_monitor_is_mandatory(self):
        rc, out = run_main(["--outdir", self.out])
        self.assertEqual(rc, 2)
        self.assertIn("--require-monitor SECONDS is mandatory", out)
        self.assertFalse(os.path.exists(self.out))

    def test_g1_must_pass(self):
        with mock.patch.object(e2.analyze, "classify_g1",
                               return_value=("INCONCLUSIVE", None, None, "aborted")):
            rc, out = run_main(self.argv())
        self.assertEqual(rc, 2)
        self.assertIn("does not pass G1 (INCONCLUSIVE: aborted)", out)
        self.assertFalse(os.path.exists(self.out))

    def test_workload_must_be_accepted_p4c(self):
        with mock.patch.object(e2.analyze, "classify_g1", return_value=("PASS", 80.8, 0.095, "ok")), \
                mock.patch.object(e2.e3, "resolve_workload",
                                  return_value=(None, "workload validation for P4c is REJECTED")) as rw:
            rc, out = run_main(self.argv())
        self.assertEqual(rc, 2)
        self.assertEqual(rw.call_args.kwargs["config"], "4c")
        self.assertEqual(rw.call_args.args[1:], (4, False))
        self.assertIn("workload: workload validation for P4c is REJECTED", out)
        self.assertFalse(os.path.exists(self.out))

    def test_clock_must_open_and_read(self):
        with mock.patch.object(e2.analyze, "classify_g1", return_value=("PASS", 80.8, 0.095, "ok")), \
                mock.patch.object(e2.e3, "resolve_workload", return_value=ACCEPTED):
            with mock.patch.object(e2.fwclock, "ArmClockMailbox",
                                   side_effect=PermissionError(13, "Permission denied")):
                rc, out = run_main(self.argv())
            self.assertEqual(rc, 2)
            self.assertIn("ARM clock: cannot open /dev/vcio", out)
            dead = mock.Mock()
            dead.read.return_value = None
            with mock.patch.object(e2.fwclock, "ArmClockMailbox", return_value=dead):
                rc, out = run_main(self.argv())
            self.assertEqual(rc, 2)
            self.assertIn("mailbox read returned no value", out)
            dead.close.assert_called_once()
        self.assertFalse(os.path.exists(self.out))

    def test_no_watchdog_means_no_load_and_an_aborted_record(self):
        clock = mock.Mock()
        clock.read.return_value = 1800.0
        with mock.patch.object(e2.analyze, "classify_g1", return_value=("PASS", 80.8, 0.095, "ok")), \
                mock.patch.object(e2.e3, "resolve_workload", return_value=ACCEPTED), \
                mock.patch.object(e2.fwclock, "ArmClockMailbox", return_value=clock), \
                mock.patch.object(e2.wl, "FixedWorkPool") as pool, \
                mock.patch.object(e2.e1t, "measure_idle_baseline") as idle:
            rc, out = run_main(self.argv())
        self.assertEqual(rc, 2)
        self.assertIn(f"[exp2] run run: waiting up to 1 s for a verified watchdog", out)
        pool.assert_not_called()
        idle.assert_not_called()
        clock.close.assert_called_once()
        with open(os.path.join(self.out, "exp2.start.json")) as f:
            start = json.load(f)
        self.assertEqual(set(start["sha256"]), set(e2.EXP2_HASHED))
        with open(os.path.join(self.out, "exp2.meta.json")) as f:
            meta = json.load(f)
        self.assertIn("refused: monitoring not established", meta["abort_reason"])
        self.assertEqual(an.classify_g2(meta)[0], "INCONCLUSIVE")
        rc, out = run_main(self.argv())                             # never overwrite a run
        self.assertEqual(rc, 2)


class MockEndToEnd(unittest.TestCase):
    ARGS = ["--backend", "mock", "--ramps", "1", "--ramp-minutes", "0.25", "--mock-tau", "3",
            "--mock-job-ms", "5", "--idle-window-s", "0.5"]

    def run_mock(self, *extra):
        out = os.path.join(tempfile.mkdtemp(), "m")
        before = signal.getsignal(signal.SIGTERM)
        rc, text = run_main([*self.ARGS, "--outdir", out, *extra])
        self.assertIs(signal.getsignal(signal.SIGTERM), before)    # handlers restored
        with open(os.path.join(out, "exp2.meta.json")) as f:
            return rc, text, json.load(f), out

    def test_rising_latency_passes_g2(self):
        rc, text, meta, out = self.run_mock()
        self.assertEqual(rc, 0)
        self.assertEqual((meta["amendment"], meta["exp2_outcome"]), ("A4", "PASS"))
        self.assertEqual(an.classify_g2(meta)[0], "PASS")           # the analysis agrees
        self.assertIn("MOCK, NOT A RESULT", text)
        self.assertEqual(meta["clock"]["readable"], meta["clock"]["reads"])
        self.assertAlmostEqual(meta["knee_c"], 80.0, delta=1.5)
        self.assertTrue(os.path.exists(os.path.join(out, "exp2.clock.jsonl")))

    def test_flat_latency_fails_g2(self):
        rc, _, meta, _ = self.run_mock("--mock-flat")
        self.assertEqual(rc, 0)
        self.assertEqual(meta["exp2_outcome"], "FAIL")

    def test_sigterm_saves_an_aborted_record(self):
        out = os.path.join(tempfile.mkdtemp(), "m")
        p = subprocess.Popen([sys.executable, "exp2_curve.py", *self.ARGS, "--ramp-minutes", "1",
                              "--outdir", out], cwd=HERE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             preexec_fn=lambda: signal.signal(signal.SIGTERM, signal.SIG_DFL))
        deadline = time.monotonic() + 30
        for line in p.stdout:
            if "HOT jobs back to back" in line or time.monotonic() > deadline:
                break
        time.sleep(1.0)
        p.send_signal(signal.SIGTERM)
        rest, _ = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 130, rest)
        with open(os.path.join(out, "exp2.meta.json")) as f:
            meta = json.load(f)
        self.assertEqual(meta["interrupted"], "interrupted (SIGTERM)")
        self.assertEqual(an.classify_g2(meta)[0], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
