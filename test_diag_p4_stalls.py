"""
Tests for diag_p4_stalls.py (non-confirmatory P4b stall diagnostic).

    python3 -m unittest -v test_diag_p4_stalls

A fake /proc tree for the snapshots, synthetic jobs for the stall flags and the
summary, and a short end-to-end run on the MOCK backend with a real four-worker
pool and tiny jobs.
"""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest

import diag_p4_stalls as ds

IRQ = """           CPU0       CPU1       CPU2       CPU3
 11:     {a}     100     100     100    GICv2  30 Level     arch_timer
 40:     {m}       0       0       0    GICv2 158 Level     mmc1, mmc0
IPI1:     5     {f}     5     5       Function call interrupts
"""
SOFT = """                    CPU0       CPU1       CPU2       CPU3
       TIMER:     10     10     10     10
     TASKLET:     {t}      0      0      0
"""


def fake_proc(root, tasks, a=100, m=0, f=5, t=0):
    """tasks: {(pid, tid): (comm, runtime_ns)}"""
    for (pid, tid), (comm, ns) in tasks.items():
        d = os.path.join(root, str(pid), "task", str(tid))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "schedstat"), "w") as fh:
            fh.write(f"{ns} 0 1\n")
        with open(os.path.join(d, "comm"), "w") as fh:
            fh.write(comm + "\n")
    os.makedirs(os.path.join(root, "self"), exist_ok=True)      # non-numeric: ignored
    with open(os.path.join(root, "interrupts"), "w") as fh:
        fh.write(IRQ.format(a=a, m=m, f=f))
    with open(os.path.join(root, "softirqs"), "w") as fh:
        fh.write(SOFT.format(t=t))


class SnapshotAndDiff(unittest.TestCase):
    def test_foreign_threads_parent_and_counters(self):
        root = tempfile.mkdtemp(prefix="fakeproc_")
        base = {(10, 10): ("python3", 0), (10, 11): ("python3", 0),       # parent + its logger
                (20, 20): ("python3", 0),                                  # a worker
                (30, 30): ("kworker/1:2", 0), (40, 40): ("avahi-daemon", 0)}
        fake_proc(root, base)
        snap = ds.Snapshot(proc=root)
        a = snap.take()
        fake_proc(root, {**base, (10, 11): ("python3", 3_000_000),       # parent logger 3 ms
                         (20, 20): ("python3", 190_000_000),             # worker: excluded
                         (30, 30): ("kworker/1:2", 25_000_000),          # 25 ms on a core
                         (40, 40): ("avahi-daemon", 100_000)},           # 0.1 ms: below floor
                  a=104, m=7, f=60, t=9)
        d = ds.diff(snap, a, snap.take(), own_pids={20}, parent_pid=10)
        self.assertEqual(d["foreign"], [("kworker/1:2", 30, 25.0), ("parent:python3", 11, 3.0)])
        self.assertEqual(d["foreign_ms"], 28.0)
        self.assertEqual(d["irq"]["11 GICv2 30 Level arch_timer"], [4, 0, 0, 0])
        self.assertEqual(d["irq"]["40 GICv2 158 Level mmc1, mmc0"], [7, 0, 0, 0])
        self.assertEqual(d["irq"]["IPI1 Function call interrupts"], [0, 55, 0, 0])
        self.assertEqual(d["softirq"], {"TASKLET": [9, 0, 0, 0]})          # unchanged TIMER omitted

    def test_vanished_and_new_threads_are_safe(self):
        root = tempfile.mkdtemp(prefix="fakeproc_")
        fake_proc(root, {(30, 30): ("kworker", 5)})
        snap = ds.Snapshot(proc=root)
        a = snap.take()
        os.rename(os.path.join(root, "30"), os.path.join(root, "gone"))
        fake_proc(root, {(50, 50): ("newthread", 9_000_000)})
        d = ds.diff(snap, a, snap.take(), own_pids=set())
        self.assertEqual(d["foreign"], [])            # new thread: start unknown -> 0


def _job(block, cls, lat, units, cond="F", foreign=()):
    return {"block": block, "class": cls, "condition": cond, "latency_ms": lat,
            "unit_ms": list(units),
            "during": {"foreign_ms": sum(x[2] for x in foreign), "foreign": list(foreign)}}


class StallsAndSummary(unittest.TestCase):
    def test_stall_flag_by_latency_or_long_unit(self):
        jobs = [_job(0, "HOT", 197.0, [15.0] * 13) for _ in range(8)]
        jobs.append(_job(0, "HOT", 246.9, [15.0] * 12 + [30.7]))         # both rules
        jobs.append(_job(0, "HOT", 199.0, [15.0] * 12 + [23.0]))         # long unit only
        jobs.append(_job(0, "COOL", 400.0, [15.0] * 5))                  # COOL never flagged
        ds.mark_stalls(jobs)
        self.assertEqual([j.get("stall") for j in jobs],
                         [False] * 8 + [True, True, None])

    def test_summary_ranks_threads_with_extra_cpu_in_stalls(self):
        ok = [_job(0, "HOT", 197.0, [15.0] * 13, foreign=[("kworker", 1, 0.5)])
              for _ in range(9)]
        st = _job(0, "HOT", 240.0, [15.0] * 12 + [30.0],
                  foreign=[("vchiq-slot", 2, 30.0), ("kworker", 1, 0.5)])
        jobs = ds.mark_stalls(ok + [st] + [_job(1, "HOT", 197.0, [15.0] * 13, cond="N")
                                         for _ in range(3)])
        s = ds.summarize(jobs)
        self.assertEqual((s["F"]["hot_jobs"], s["F"]["stalls"]), (10, 1))
        self.assertEqual(s["F"]["excess_by_thread_in_stalls"][0], ("vchiq-slot", 30.0))
        self.assertEqual(s["F"]["foreign_ms_stall_mean"], 30.5)
        self.assertEqual((s["N"]["hot_jobs"], s["N"]["stalls"]), (3, 0))


def _report(d):
    p = os.path.join(d, "report.json")
    with open(p, "w") as f:
        json.dump({"run_id": "wv-test", "configs": {"4b": {"status": "REJECTED", "frozen": {
            "P": 4, "n_b": 16, "u_h": 2, "u_c": 1, "S_ms": 2.0, "gemms_per_unit": 4,
            "params_id": "P4b-F1"}}}}, f)
    return p


class LoadSpecs(unittest.TestCase):
    def test_frozen_p4b(self):
        hot, cool, src = ds.load_p4b_specs(_report(tempfile.mkdtemp()))
        self.assertEqual((hot.P, hot.gemms_per_unit, hot.units_per_shard), (4, 4, 2))
        self.assertEqual((cool.units_per_shard, cool.sleep_per_shard_ms), (1, 2.0))
        self.assertEqual(src["params_id"], "P4b-F1")

    def test_not_p4b_refused(self):
        p = os.path.join(tempfile.mkdtemp(), "r.json")
        with open(p, "w") as f:
            json.dump({"configs": {"4": {"frozen": {"P": 4, "n_b": 128, "u_h": 53, "u_c": 19,
                                                    "S_ms": 128.0}}}}, f)
        with self.assertRaises(SystemExit):
            ds.load_p4b_specs(p)


class EndToEndMock(unittest.TestCase):
    def test_short_run_records_every_job(self):
        d = tempfile.mkdtemp(prefix="stalls_e2e_")
        out = os.path.join(d, "run")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ds.main(["--backend", "mock", "--report", _report(d), "--outdir", out,
                          "--pairs-per-block", "2", "--poll-s", "0.01"])
        self.assertEqual(rc, 0, buf.getvalue())
        with open(os.path.join(out, "stalls.meta.json")) as f:
            m = json.load(f)
        self.assertTrue(m["complete"])
        self.assertEqual([b["condition"] for b in m["blocks"]], list(ds.ORDER))
        with open(os.path.join(out, "stalls.jobs.jsonl")) as f:
            jobs = [json.loads(x) for x in f]
        self.assertEqual(len(jobs), 16 * 4)
        self.assertTrue(all("during" in j and "shard_walls" in j and "shards" not in j
                            for j in jobs))
        self.assertTrue(all(("throttled_after" in j) == (j["condition"] == "F") for j in jobs))
        self.assertEqual(m["summary"]["F"]["hot_jobs"], 16)
        self.assertEqual(m["unstopped_workers"], [])

    def test_existing_outdir_refused(self):
        d = tempfile.mkdtemp(prefix="stalls_e2e_")
        out = os.path.join(d, "x")
        os.makedirs(out)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = ds.main(["--backend", "mock", "--report", _report(d), "--outdir", out])
        self.assertEqual((rc, os.listdir(out)), (2, []))


class WatchdogMode(unittest.TestCase):
    def test_poll_intervals_and_overlap(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "poller.log")
        with open(p, "w") as f:
            f.write("100.00 start\n100.05 end\n105.10 start\n105.16 end\n110.20 start\n")
        iv = ds.poll_intervals(p, offset=100.0)            # uptime - monotonic
        self.assertEqual([tuple(round(x, 2) for x in i) for i in iv], [(0.0, 0.05), (5.1, 5.16)])
        jobs = [{"t0_mono": -0.1, "t1_mono": 0.02}, {"t0_mono": 1.0, "t1_mono": 1.2},
                {"t0_mono": 5.15, "t1_mono": 5.35}]
        ds.mark_overlap(jobs, iv)
        self.assertEqual([j["poll_overlap"] for j in jobs], [True, False, True])

    def test_summary_splits_stalls_by_overlap(self):
        jobs = [{**_job(0, "HOT", 197.0, [15.0] * 13, cond="W"), "poll_overlap": False}
                for _ in range(8)]
        jobs += [{**_job(0, "HOT", 240.0, [15.0] * 12 + [30.0], cond="W"), "poll_overlap": True},
                 {**_job(0, "HOT", 197.0, [15.0] * 13, cond="W"), "poll_overlap": True}]
        s = ds.summarize(ds.mark_stalls(jobs), ds.MODES["watchdog"][1])["W"]
        self.assertEqual((s["hot_jobs_poll_overlap"], s["stalls_poll_overlap"],
                          s["hot_jobs_no_overlap"], s["stalls_no_overlap"]), (2, 1, 8, 0))

    def test_watchdog_mode_end_to_end_poller_stops_without_signals(self):
        d = tempfile.mkdtemp(prefix="stalls_wd_")
        out = os.path.join(d, "run")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ds.main(["--backend", "mock", "--report", _report(d), "--outdir", out,
                          "--mode", "watchdog", "--pairs-per-block", "4", "--poll-s", "0.01"])
        self.assertEqual(rc, 0, buf.getvalue())
        with open(os.path.join(out, "stalls.meta.json")) as f:
            m = json.load(f)
        self.assertTrue(m["complete"])
        self.assertEqual(m["mode"], "watchdog")
        self.assertEqual([b["condition"] for b in m["blocks"]], list(ds.MODES["watchdog"][0]))
        self.assertGreaterEqual(m["poll_iterations"], 8)            # at least one per W block
        self.assertEqual(m["poller_unstopped"], [])
        self.assertIn("hot_jobs_poll_overlap", m["summary"]["W"])
        with open(os.path.join(out, "stalls.jobs.jsonl")) as f:
            jobs = [json.loads(x) for x in f]
        self.assertTrue(all("throttled_after" in j and "poll_overlap" in j and
                            j["t1_mono"] > j["t0_mono"] for j in jobs))
        self.assertTrue(any(j["poll_overlap"] for j in jobs if j["condition"] == "W"))
        self.assertFalse(any(j["poll_overlap"] for j in jobs if j["condition"] == "N"))
        self.assertEqual(sorted(f for f in os.listdir(out) if f.endswith(".stop")),
                         [f"poller{i}.stop" for i in (0, 11, 12, 15, 3, 4, 7, 8)])


class ForkFreePollerP4c(unittest.TestCase):
    def test_poller_nf_uses_no_external_command(self):
        body = ds.POLLER_NF.replace("$((", "ARITH(")            # arithmetic is builtin
        self.assertNotIn("$(", body)                             # no command substitution
        self.assertIsNone(re.search(r"(?<!\|)\|(?!\|)", body))  # no pipes (|| is fine)
        for cmd in ("sleep", "cat ", "date", " bc", "pgrep", "cut ", "vcgencmd", "awk", "tr "):
            self.assertNotIn(cmd, body)
        self.assertIn("exec {SFD}<> <(:)", body)                 # the one start-up fork

    def test_watchdog_nf_mode_end_to_end(self):
        d = tempfile.mkdtemp(prefix="stalls_nf_")
        out = os.path.join(d, "run")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = ds.main(["--backend", "mock", "--report", _report(d), "--outdir", out,
                          "--mode", "watchdog-nf", "--pairs-per-block", "4", "--poll-s", "0.01"])
        self.assertEqual(rc, 0)
        with open(os.path.join(out, "stalls.meta.json")) as f:
            m = json.load(f)
        self.assertTrue(m["complete"])
        self.assertEqual(m["mode"], "watchdog-nf")
        self.assertIn("fork-free", m["conditions"]["W"])
        self.assertGreaterEqual(m["poll_iterations"], 8)
        self.assertEqual(m["poller_unstopped"], [])
        with open(os.path.join(out, "poller0.log.readings")) as f:
            self.assertRegex(f.readline(), r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d +\d+\.\d   uv=")


class RestoresSignalHandlers(unittest.TestCase):
    def test_handlers_restored_after_main(self):
        import signal
        before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        d = tempfile.mkdtemp(prefix="stalls_sig_")
        with contextlib.redirect_stdout(io.StringIO()):
            ds.main(["--backend", "mock", "--report", _report(d), "--outdir",
                     os.path.join(d, "run"), "--pairs-per-block", "1", "--poll-s", "0.01"])
        self.assertEqual((signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)),
                         before)


class NeverSignalsItself(unittest.TestCase):
    def test_source_sends_no_signals(self):
        with open(ds.__file__) as f:
            src = f.read()
        hit = re.search(r"signal\.SIGKILL|\.kill\(|os\.kill|killpg|\.terminate\(|\.send_signal\(", src)
        self.assertIsNone(hit, hit and hit.group(0))


if __name__ == "__main__":
    unittest.main()
