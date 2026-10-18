#!/usr/bin/env python3
"""
EXPERIMENT 3 - identical-multiset ordering test, under Amendment A7. THE decisive experiment.

Every ordering executes the same multiset of FIXED-WORK jobs (Amendment A2):
the same jobs, the same planned compute units and the same planned sleep per
job. Achieved totals are recorded per sequence. Energy is NOT measured; equal
planned work does not imply equal energy. HOT and COOL cold latencies are
matched by the ACCEPTED P4c workload validation, so job-length/queueing
explanations are excluded within its measured tolerance.

A7 (AMENDMENT_A7_exp3.md), at four cores:
  - P4c's HOT and COOL jobs (resolve_workload(..., config="4c")), k = 600
    (300 + 300), orderings hot_first / cool_first / alternating / random, 10
    interleaved, counterbalanced repetitions (seeded);
  - before each sequence: a cooldown with no load to idle + 3 C (cap 20 min),
    then a pre-heat with back-to-back HOT jobs until the sysfs temperature
    before a job is >= 77.0 C (cap 5 min); the sequence starts at once;
  - telemetry: P4c's 5 Hz sysfs logger plus the between-job get_throttled
    read; no live ARM-clock reads (A4); time above the knee = 5 Hz samples
    in the sequence with temperature >= the G1 knee, x 0.2 s;
  - a sequence is valid if its cooldown and pre-heat met their targets, all
    jobs completed their units and no flag read showed under-voltage; an
    invalid sequence (a worker failure included: the pool is restarted) is
    rerun once at the end of the session. Under-voltage stops the session.

On real hardware it refuses, before creating any file or starting any load,
unless Experiment 1 passes G1 (--exp1-meta) and the P4c validation is
ACCEPTED (--workload-validation). --require-monitor is mandatory there
(tools/launch_exp3.sh passes it): a FRESH --outdir, no load until
<outdir>/MONITOR_OK names a verified tools/exp3_monitor.sh, and a stop if the
watchdog is lost. On SIGTERM, Ctrl+C or a lost watchdog the jobs stop, the
workers get a stop request and at most one SIGTERM each (never SIGKILL), and
exp3.meta.json is still written, marked aborted (INCONCLUSIVE for G3-G6).

    python3 exp3_ordering.py --outdir results/RUN --require-monitor 120 \\
        --exp1-meta results/exp1-.../exp1.meta.json \\
        --workload-validation results/workload_validation/wv-p4c-.../report.json
    python3 exp3_ordering.py --backend mock --reps 8 --k 60 --outdir results_mock

The mock backend simulates jobs (HOT/COOL power on the mock thermal model,
latency 196 ms x f_max / f) and may run without the barrier: pipeline checks
only, never a result.

Outputs: <outdir>/exp3.{meta.json,jobs.jsonl,telemetry.jsonl}
(+ exp3.start.json with --require-monitor)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field

import numpy as np

import analyze
import exp1_thermal as e1t
import monitor_guard as mg
import runner
import sensors
import workload as wl

AMENDMENT = "A7"
CONFIG = "4c"
P = 4
EXP3_WATCHDOG = "exp3_monitor.sh"
MOCK_BASE_MS = 196.0
EXP3_HASHED = ("exp3_ordering.py", "analyze.py", "sensors.py", "runner.py", "workload.py",
               "monitor_guard.py", "validate_workload.py", "exp1_thermal.py")


def _load_json(path):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def resolve_threshold(exp1_meta, exp2_meta, is_mock, knee_override=None):
    """Time-above-knee threshold for this run.

    Only a knee from an Experiment 1 that passes G1 (analyze.classify_g1, the
    same rule the analysis uses) may set the threshold of a confirmatory run.
    Experiment 2's knee is recorded as supporting evidence and is never
    substituted. --knee is accepted only on the mock backend.
    """
    e1 = _load_json(exp1_meta)
    state, knee, _, why = analyze.classify_g1(e1, None)
    th = {"exp1_meta": exp1_meta, "g1_state": state, "g1_reason": why,
          "exp2_knee_support": analyze.exp2_knee_support(_load_json(exp2_meta))}
    if knee_override is not None:
        if not is_mock:
            raise SystemExit("--knee is only allowed with the mock backend; a "
                             "confirmatory run takes its knee from a valid "
                             "Experiment 1 (--exp1-meta)")
        th.update(knee_c=knee_override, knee_source="mock --knee",
                  confirmatory=False)
    elif state == "PASS":
        th.update(knee_c=knee, knee_source="exp1 (G1 PASS)",
                  confirmatory=not is_mock)
    else:
        th.update(knee_c=None, knee_source=None, confirmatory=False)
    return th


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def resolve_workload(report_path, P, is_mock, config=None):
    """Fixed-work HOT/COOL specs for this run. Returns (info, refusal).

    Real hardware: only parameters frozen by a workload validation whose
    configuration P is ACCEPTED may be used, and only with the same
    workload.py that validation measured. Anything else is a refusal.
    config="4b" or "4c" selects configuration P4b or P4c (P=4, four-GEMM units,
    PREREG_workload_validation_ADDENDUM_P4b.md / _P4c.md); their jobs must then
    run under that telemetry scheme (info["telemetry"] == "sysfs").
    Mock: simulated jobs, never confirmatory, no validation needed.
    """
    import validate_workload as vw
    if is_mock:
        hot, cool = wl.JobSpec("HOT", P or 1, 0, 0), wl.JobSpec("COOL", P or 1, 0, 0)
        return ({"accepted": False, "mock": True, "hot": hot, "cool": cool,
                 "cold_latency_match": {"matched": True, "note": "mock"}}, None)
    if P is None:
        return None, "--parallel is required on real hardware (1 or 4)"
    rep = _load_json(report_path)
    if rep is None:
        return None, f"no workload validation report at {report_path!r}"
    if rep.get("overall") != "COMPLETE":
        return None, (f"workload validation is {rep.get('overall', 'missing')}, "
                      "not COMPLETE" + (f" ({rep['reason']})" if rep.get("reason") else ""))
    if rep.get("interrupted"):
        return None, f"workload validation was interrupted ({rep['interrupted']})"
    four = {"4b": vw.P4B, "4c": vw.P4C}.get(config)
    if config is not None and four is None:
        return None, f"unknown workload configuration {config!r}"
    if four is not None and P != 4:
        return None, f"configuration {config} is a P=4 configuration (--parallel 4)"
    key = config or str(P)
    cfg = (rep.get("configs") or {}).get(key) or {}
    if cfg.get("status") != vw.ACCEPTED:
        return None, (f"workload validation for {four['label'] if four else f'P={P}'} is "
                      f"{cfg.get('status', 'missing')}, not {vw.ACCEPTED}"
                      + (f" ({cfg['reason']})" if cfg.get("reason") else ""))
    ev, frozen = cfg.get("evaluation") or {}, cfg.get("frozen") or {}
    gap = ev.get("gap") or {}
    if gap.get("n_pairs") != vw.PREREG["accepted_pairs"]:
        return None, f"validation used {gap.get('n_pairs')} pairs, not {vw.PREREG['accepted_pairs']}"
    if gap.get("class") not in (vw.TARGET, vw.WITHIN):
        return None, f"cold-latency gap class is {gap.get('class')}"
    if not all(ev.get("criteria", {}).values()) or not ev.get("criteria"):
        return None, "not every acceptance criterion holds"
    if frozen.get("stale") or not all(k in frozen for k in ("P", "n_b", "u_h", "u_c", "S_ms")):
        return None, "frozen parameters are stale or incomplete"
    if frozen["P"] != P:
        return None, f"frozen parameters are for P={frozen['P']}, not P={P}"
    want_g = four["gemms_per_unit"] if four else 1
    if int(frozen.get("gemms_per_unit", 1)) != want_g:
        return None, (f"frozen parameters have {frozen.get('gemms_per_unit', 1)} GEMMs "
                      f"per unit, configuration {key} needs {want_g}")
    recorded = (rep.get("prereg_sha256") or {}).get("workload.py")
    if recorded != _sha256(wl.__file__):
        return None, ("workload.py differs from the version the validation "
                      "measured; revalidate before running")
    hot, cool = wl.make_job_specs(frozen)
    vals = ev.get("values", {})
    return ({"accepted": True, "mock": False, "hot": hot, "cool": cool,
             "config": key, "telemetry": four["telemetry"] if four else "full",
             "frozen": frozen, "validation_run": rep.get("run_id"),
             "report": report_path,
             "cold_latency_match": {
                 "hot_median_ms": vals.get("hot_median_ms"),
                 "cool_median_ms": vals.get("cool_median_ms"),
                 "relative_gap": gap.get("g_hat"),
                 "gap_ci": [gap.get("ci_lo"), gap.get("ci_hi")],
                 "gap_class": gap.get("class"),
                 "matched": True,
                 "source": f"workload validation {rep.get('run_id')} "
                           f"({four['label'] if four else f'P={P}'})"}},
            None)

# --------------------------------------------------------------------------
# Amendment A7: the session
# --------------------------------------------------------------------------
@dataclass
class Plan:
    """A7 section 2. Defaults are the amendment's values."""
    k: int = 600
    hot_frac: float = 0.5
    reps: int = 10
    orderings: tuple = ("hot_first", "cool_first", "alternating", "random")
    start_c: float = 77.0
    preheat_cap_s: float = 300.0
    idle_c: float | None = None
    margin_c: float = 3.0
    cooldown_cap_s: float = 1200.0
    hz: float = 5.0
    seed: int = 0
    poll_s: float = 2.0
    rerun_invalid: bool = True


def make_schedule(reps, modes, rng):
    """Interleaved and counterbalanced, as before A7: each repetition is one block
    of all orderings, alternate blocks reversed, then shuffled within the block."""
    out = []
    for r in range(reps):
        block = list(modes)
        if r % 2 == 1:
            block = block[::-1]
        rng.shuffle(block)
        out.extend((r, m) for m in block)
    return out


class Exp3Guard(mg.MonitorGuard):
    """monitor_guard's barrier for Experiment 3 (as exp2_curve.Exp2Guard), but the
    watchdog must be tools/exp3_monitor.sh: argv [..., exp3_monitor.sh, <pid>, <outdir>]."""

    def _watchdog_problem(self, mpid, mstart, check_exe=False):
        if mg.proc_start(mpid) != mstart:
            return f"watchdog pid {mpid} is not running with start time {mstart}"
        argv = mg.proc_argv(mpid)
        if (not argv or len(argv) < 3 or not argv[-3].endswith(EXP3_WATCHDOG)
                or argv[-2] != str(self.pid) or argv[-1] != self.root):
            return f"watchdog pid {mpid} argv {argv!r} does not match"
        if check_exe:
            exe = mg.proc_exe(mpid)
            if not exe or os.path.basename(exe) != "bash":
                return f"watchdog pid {mpid} executable {exe!r} is not bash"
        return None


class MockPool:
    """Simulated fixed-work jobs on the mock thermal model: HOT and COOL set the
    mock power; latency = 196 ms x f_max / f (+ noise). Pipeline checks only."""

    def __init__(self, backend, job_ms=5.0, seed=0, hot_power=1.45, cool_power=0.55):
        self.b, self.job_s = backend, job_ms / 1000.0
        self.hot_power, self.cool_power = hot_power, cool_power
        self.rng = np.random.default_rng(seed)
        self.unstopped = []

    def alive(self):
        return True

    def run_job(self, spec, record_units=False):
        self.b.set_power(self.hot_power if spec.name == "HOT" else self.cool_power)
        time.sleep(self.job_s)
        f = self.b.read().get("freq_mhz") or self.b.f_max
        return {"class": spec.name, "units_planned": spec.planned_units,
                "units_done": spec.planned_units, "cpu_ms": None, "compute_ms": None,
                "sleep_req_ms": spec.planned_sleep_ms, "sleep_actual_ms": None,
                "latency_ms": float(MOCK_BASE_MS * self.b.f_max / f + self.rng.normal(0.0, 1.5))}

    def stop(self):
        self.b.set_power(0.0)
        return []


class SessionAbort(Exception):
    """Stops the whole session (under-voltage, a pool that cannot start)."""


class Session:
    """Runs the A7 schedule. Every outside part is injected (backend, pool factory,
    flag reader, watchdog check), so the hardware path is tested with fakes. Never
    raises for a run problem: the reason lands in the sequence record, or in
    abort_reason / interrupted for the session."""

    WORK_FIELDS = ("units_planned", "units_done", "cpu_ms", "compute_ms", "sleep_req_ms",
                   "sleep_actual_ms", "realized_duty", "overhead_ms", "nivcsw")

    def __init__(self, backend, make_pool, hot, cool, plan: Plan, knee_c, check=None,
                 flags=None, quick=None, idle=None, log=print, sleep=time.sleep):
        import validate_workload as vw
        self.vw = vw
        self.b, self.make_pool, self.hot, self.cool = backend, make_pool, hot, cool
        self.plan, self.knee_c, self.check = plan, knee_c, check
        self.flags = flags or (lambda: vw._flags(backend))
        self.quick = quick or (lambda: vw._quick_read(backend))
        self.idle, self.log, self.sleep = idle, log, sleep
        self.rng = np.random.default_rng(plan.seed)
        n_hot = int(round(plan.k * plan.hot_frac))
        self.multiset = wl.make_multiset(n_hot, plan.k - n_hot, hot, cool)
        self.planned = wl.planned_totals(self.multiset)
        self.schedule = make_schedule(plan.reps, list(plan.orderings), self.rng)
        self.pool = None
        self.runs, self.jobs, self.tele, self.unstopped = [], [], [], []
        self.abort_reason = self.interrupted = None
        self.undervoltage = self.session_complete = False

    # -- helpers ----------------------------------------------------------------
    def _check(self):
        if self.check is not None:
            self.check()                  # raises mg.MonitorLost (a KeyboardInterrupt)

    def _go_idle(self):
        if self.idle is not None:
            self.idle()

    def _pool(self):
        if self.pool is None or not self.pool.alive():
            if self.pool is not None:
                self._stop_pool()
            try:
                self.pool = self.make_pool()
            except (wl.WorkerFailure, OSError) as e:
                raise SessionAbort(f"pool start failed: {type(e).__name__}: {e}")
        return self.pool

    def _stop_pool(self):
        if self.pool is not None:
            self.pool.stop()              # graceful, then at most one SIGTERM; never SIGKILL
            self.unstopped.extend(getattr(self.pool, "unstopped", None) or [])
            self.pool = None

    def _flag(self, th, where):
        """None if fine. Under-voltage stops the session; unreadable flags or (where
        asked) live bits make the sequence invalid (returned as a reason)."""
        if th is None:
            return f"throttle flags unreadable {where}"
        if th & analyze.UNDERVOLT_BITS:
            self.undervoltage = True
            raise SessionAbort(f"under-voltage {where} ({hex(th)})")
        return None

    def cooldown(self):
        target = self.plan.idle_c + self.plan.margin_c
        t0 = time.monotonic()
        while True:
            self._check()
            t = self.quick().get("temp_c")
            if t is not None and t <= target:
                return t, time.monotonic() - t0
            if time.monotonic() - t0 > self.plan.cooldown_cap_s:
                raise self.vw.CooldownCap(f"did not cool to {target:.1f} C within "
                                          f"{self.plan.cooldown_cap_s:.0f} s (at {t} C)")
            self.sleep(self.plan.poll_s)

    # -- the session ----------------------------------------------------------------
    def run(self):
        try:
            for rep, mode in self.schedule:
                self._sequence(rep, mode, None)
            if self.plan.rerun_invalid:
                for s in [s for s in self.runs if not s["valid"]]:
                    self._sequence(s["rep"], s["mode"], s["run_index"])
            self.session_complete = True
        except SessionAbort as e:
            self.abort_reason = str(e)
        except KeyboardInterrupt as e:    # SIGTERM, Ctrl+C or monitor_guard.MonitorLost
            self.interrupted = f"interrupted ({e or 'KeyboardInterrupt'})"
            self.abort_reason = self.abort_reason or self.interrupted
        finally:
            self._go_idle()
            self._stop_pool()
        return self

    def _sequence(self, rep, mode, rerun_of):
        p, i = self.plan, len(self.runs)
        n_planned = len(self.schedule)
        tag = (f"[exp3] seq {i + 1}/{n_planned}" + (f" rerun of {rerun_of + 1}" if rerun_of is not None
                                                    else "") + f" ({rep}, {mode})")
        s = {"run_index": i, "rep": rep, "mode": mode, "rerun_of": rerun_of, "valid": False,
             "invalid_reason": "interrupted"}
        self.runs.append(s)
        seq = wl.order_sequence(self.multiset, mode, self.rng)   # drawn first: one rng stream
        s["planned"] = wl.planned_totals(seq)
        pool = self._pool()               # started before the cooldown: start-up heat never counts
        self._go_idle()
        target = p.idle_c + p.margin_c
        self.log(f"{tag}: cooling with no load to <= {target:.1f} C")
        try:
            s["cooldown_end_c"], waited = self.cooldown()
        except self.vw.CooldownCap as e:
            s["invalid_reason"] = f"cooldown: {e}"
            self.log(f"{tag}: INVALID - {s['invalid_reason']}")
            return
        s["cooldown_s"] = round(waited, 1)
        th0 = self.flags()
        s["flags_before"] = hex(th0) if th0 is not None else None
        why = self._flag(th0, "before the sequence")
        if why is None and th0 & self.vw.LIVE_FLAG_MASK:
            why = f"live throttle flags before the sequence ({hex(th0)})"
        if why:
            s["invalid_reason"] = why
            self.log(f"{tag}: INVALID - {why}")
            return
        jobs, tele = [], []
        try:
            why = self._preheat(pool, s, tag)
            if why is None:
                why, jobs, tele = self._run_sequence(pool, seq, s, i, rep, mode)
        except (wl.JobTimeout, wl.WorkerFailure) as e:
            why = f"{type(e).__name__}: {e}"
            self._stop_pool()             # restarted before the next sequence's cooldown
        finally:
            self._go_idle()
        th1 = self.flags()
        s["flags_after"] = hex(th1) if th1 is not None else None
        why = why or self._flag(th1, "after the sequence")
        s.update(self._summary(jobs, tele, s.get("t_wall_s")))
        self.jobs.extend(jobs)
        self.tele.extend(tele)
        if why is None and len(jobs) != p.k:
            why = f"only {len(jobs)} of {p.k} jobs ran"
        s["valid"], s["invalid_reason"] = why is None, why
        self.log(f"{tag}: {'done' if why is None else 'INVALID - ' + why}: "
                 f"p99 {s.get('p99_ms')} ms, above knee {s.get('time_above_knee_s')} s, "
                 f"peak {s.get('peak_temp_c')} C, start {s.get('start_temp_c')} C")

    def _preheat(self, pool, s, tag):
        p = self.plan
        self.log(f"{tag}: pre-heat with HOT jobs to >= {p.start_c:.1f} C")
        t0, n = time.monotonic(), 0
        while True:
            self._check()
            t = self.quick().get("temp_c")
            if t is not None and t >= p.start_c:
                s.update(preheat_jobs=n, preheat_s=round(time.monotonic() - t0, 1), start_temp_c=t)
                return None
            if time.monotonic() - t0 > p.preheat_cap_s:
                s.update(preheat_jobs=n, preheat_s=round(time.monotonic() - t0, 1))
                return f"pre-heat did not reach {p.start_c:.1f} C within {p.preheat_cap_s:.0f} s (at {t} C)"
            rec = pool.run_job(self.hot)
            n += 1
            if rec.get("units_done") != rec.get("units_planned"):
                return f"pre-heat job {n} did {rec.get('units_done')} of {rec.get('units_planned')} units"
            why = self._flag(self.flags(), "during the pre-heat")
            if why:
                return why

    def _run_sequence(self, pool, seq, s, i, rep, mode):
        logger = runner.TelemetryLogger(self.vw.SysfsReader(self.b), hz=self.plan.hz)
        jobs, why = [], None
        t_seq = time.monotonic()
        logger.start()
        try:
            for idx, job in enumerate(seq):
                self._check()
                q = self.quick()
                ta = time.monotonic()
                rec = pool.run_job(job)
                jr = {"run_index": i, "rep": rep, "mode": mode, "idx": idx, "class": job.name,
                      "latency_ms": rec["latency_ms"], "t_start": ta - t_seq,
                      "temp_before_c": q.get("temp_c"), "freq_before_mhz": q.get("freq_mhz"),
                      **{k: rec.get(k) for k in self.WORK_FIELDS}}
                jobs.append(jr)
                if rec.get("units_done") != rec.get("units_planned"):
                    why = f"job {idx} did {rec.get('units_done')} of {rec.get('units_planned')} units"
                    break
                th = self.flags()             # P4c: between jobs, outside the latency
                jr["throttled_after"] = th
                why = self._flag(th, "between jobs")
                if why:
                    break
        finally:
            tele = logger.stop()
            s["t_wall_s"] = time.monotonic() - t_seq
            for x in tele:
                x.update(run_index=i, mode=mode)
        return why, jobs, tele

    def _summary(self, jobs, tele, wall):
        if not jobs:
            return {"n_jobs": 0}
        lats = [j["latency_ms"] for j in jobs]
        temps = [x["temp_c"] for x in tele if x.get("temp_c") is not None]
        dt = 1.0 / self.plan.hz
        flagged = [j for j in jobs if (j.get("throttled_after") or 0) & self.vw.LIVE_FLAG_MASK]
        return {
            "wall_s": wall, "n_jobs": len(jobs),
            "mean_ms": float(np.mean(lats)),
            "p50_ms": float(np.percentile(lats, 50)),
            "p95_ms": float(np.percentile(lats, 95)),
            "p99_ms": float(np.percentile(lats, 99)),
            "throughput_jobs_s": len(jobs) / wall if wall else None,
            "peak_temp_c": max(temps) if temps else None,
            "mean_temp_c": float(np.mean(temps)) if temps else None,
            "telemetry_samples": len(temps),
            "time_above_knee_s": (float(sum(1 for t in temps if t >= self.knee_c) * dt)
                                  if self.knee_c is not None and temps else None),
            "jobs_with_live_throttle_flags": len(flagged),
            "achieved": {"units_done": sum(j.get("units_done") or 0 for j in jobs),
                         "cpu_ms": sum(j.get("cpu_ms") or 0 for j in jobs),
                         "sleep_actual_ms": sum(j.get("sleep_actual_ms") or 0 for j in jobs)},
        }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def _sha256_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except (OSError, TypeError):
        return None


def _sigterm(signum, frame):
    raise KeyboardInterrupt("SIGTERM")


def _workload_record(info):
    out = {k: v for k, v in info.items() if k not in ("hot", "cool")}
    out["hot"], out["cool"] = asdict(info["hot"]), asdict(info["cool"])
    out["report_sha256"] = _sha256_file(info.get("report"))
    return out


def build_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None)
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--exp1-meta", default=None,
                    help="exp1.meta.json of an Experiment 1 that passes G1 (its knee is the threshold)")
    ap.add_argument("--exp2-meta", default=None, help="supporting evidence only")
    ap.add_argument("--workload-validation", default=None,
                    help="report.json of an ACCEPTED P4c workload validation (real hardware)")
    ap.add_argument("--k", type=int, default=600, help="jobs per sequence")
    ap.add_argument("--hot-frac", type=float, default=0.5)
    ap.add_argument("--reps", type=int, default=10, help="repetitions per ordering")
    ap.add_argument("--orderings", default="hot_first,cool_first,alternating,random")
    ap.add_argument("--start-c", type=float, default=77.0, help="pre-heat target (C)")
    ap.add_argument("--preheat-cap-s", type=float, default=300.0)
    ap.add_argument("--margin-c", type=float, default=3.0)
    ap.add_argument("--cooldown-cap-s", type=float, default=1200.0)
    ap.add_argument("--idle-window-s", type=float, default=30.0)
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-rerun", action="store_true", help="do not rerun invalid sequences")
    ap.add_argument("--require-monitor", type=float, default=None, metavar="SECONDS",
                    help="MANDATORY on real hardware (tools/launch_exp3.sh passes it)")
    ap.add_argument("--mock-knee", type=float, default=80.0)
    ap.add_argument("--mock-tau", type=float, default=4.0)
    ap.add_argument("--mock-job-ms", type=float, default=5.0)
    args = ap.parse_args(argv)
    if args.k < 2 or not 0.0 < args.hot_frac < 1.0 or args.reps < 1:
        ap.error("need --k >= 2, 0 < --hot-frac < 1 and --reps >= 1")
    if args.require_monitor is not None and not args.require_monitor > 0:
        ap.error("--require-monitor must be > 0")
    return args


def main(argv=None):
    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        return _main(build_args(argv))
    finally:
        signal.signal(signal.SIGTERM, old[0])
        signal.signal(signal.SIGINT, old[1])


def _main(args):
    if args.backend == "mock":
        b = sensors.get_backend("mock", knee=args.mock_knee, tau=args.mock_tau)
    else:
        b = sensors.get_backend(args.backend)
    is_mock = b.name == "mock"
    if not is_mock and args.require_monitor is None:
        print("refused: --require-monitor SECONDS is mandatory on real hardware; launch "
              "with tools/launch_exp3.sh, which starts and verifies the watchdog.")
        return 2
    # ---- preflight: before any file or load ----
    if is_mock:
        th = {"knee_c": args.mock_knee, "knee_source": "mock --mock-knee", "g1_state": "MOCK",
              "g1_reason": "mock", "confirmatory": False, "exp1_meta": None,
              "exp2_knee_support": None}
    else:
        th = resolve_threshold(args.exp1_meta, args.exp2_meta, False)
        if th["g1_state"] != "PASS":
            print(f"refused: Experiment 1 does not pass G1 ({th['g1_state']}: {th['g1_reason']}); "
                  "time above the knee (G4's mediator) needs a valid knee (nothing written, no load)")
            return 2
    th["exp1_meta_sha256"] = _sha256_file(args.exp1_meta)
    wk, refusal = resolve_workload(args.workload_validation, P, is_mock, config=CONFIG)
    if refusal:
        print(f"refused: workload: {refusal} (nothing written, no load)")
        return 2
    if not is_mock and wk.get("telemetry") != "sysfs":
        print(f"refused: workload telemetry is {wk.get('telemetry')!r}, P4c needs 'sysfs'")
        return 2
    knee = th["knee_c"]
    modes = [m.strip() for m in args.orderings.split(",") if m.strip()]
    bad = [m for m in modes if m not in wl.ORDERINGS]
    if bad or len(modes) < 2:
        print(f"refused: orderings {modes} (need >= 2 of {wl.ORDERINGS})")
        return 2
    print(f"[exp3] backend={b.name} amendment {AMENDMENT}: knee {knee:.1f} C ({th['knee_source']}); "
          f"k={args.k} hot_frac={args.hot_frac} reps={args.reps} orderings={modes} "
          f"start {args.start_c:.1f} C")

    root = args.outdir.rstrip("/") or args.outdir
    guard = check = None
    if args.require_monitor is not None:
        run_id = os.path.basename(root)
        try:
            os.makedirs(root)
        except FileExistsError:
            print(f"refused: {root} exists; refusing to overwrite")
            return 2
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, "exp3.start.json"), "w") as f:
            json.dump({"pid": os.getpid(), "run_id": run_id, "argv": sys.argv,
                       "written": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "exp1_meta_sha256": th["exp1_meta_sha256"],
                       "workload_report_sha256": _sha256_file(wk.get("report")),
                       "sha256": {m: _sha256_file(os.path.join(here, m)) for m in EXP3_HASHED}},
                      f, indent=1)
    signal.signal(signal.SIGTERM, _sigterm)
    ctx = {"th": th, "wk": wk, "modes": modes}
    if args.require_monitor is not None:
        guard = Exp3Guard(root, run_id)
        print(f"[exp3] run {run_id}: waiting up to {args.require_monitor:.0f} s for a "
              "verified watchdog (no load until then)", flush=True)
        try:
            why = guard.wait(args.require_monitor)
        except KeyboardInterrupt as e:
            return _save_early(root, b, args, ctx, f"interrupted at the barrier ({e})", None, 130)
        if why:
            return _save_early(root, b, args, ctx, f"refused: monitoring not established ({why})",
                               None, 2)
        print(f"[exp3] monitoring verified: {guard.describe()}", flush=True)
        check = guard.raise_if_lost
    return _run(args, b, is_mock, ctx, root, guard, check)


def _base_meta(b, args, ctx):
    wk, th = ctx["wk"], ctx["th"]
    return {"experiment": "exp3_ordering", "amendment": AMENDMENT, "backend": b.name,
            "mock": b.name == "mock", "knee_c": th["knee_c"], "g1_knee_c": th["knee_c"],
            "threshold": th, "time_above_knee_available": th["knee_c"] is not None,
            "k_jobs": args.k, "hot_frac": args.hot_frac, "reps": args.reps,
            "orderings": ctx["modes"], "start_c": args.start_c,
            "cold_latency_match": wk["cold_latency_match"], "workload": _workload_record(wk),
            "parallel": P, "energy_measured": False}


def _save_early(root, b, args, ctx, reason, guard, rc):
    meta = dict(_base_meta(b, args, ctx), abort_reason=reason, interrupted=None,
                session_complete=False, monitor=guard.describe() if guard is not None else None,
                runs=[])
    runner.save_run(root, "exp3", meta, [], [])
    print(f"[exp3] stopped before load: {reason} (no load ran)")
    return rc


def _run(args, b, is_mock, ctx, root, guard, check):
    try:
        idle_stats = e1t.measure_idle_baseline(b, args.idle_window_s, check=check)
    except KeyboardInterrupt as e:
        return _save_early(root, b, args, ctx, f"interrupted before load ({e or 'KeyboardInterrupt'})",
                           guard, 130)
    if not idle_stats:
        return _save_early(root, b, args, ctx, "idle temperature unreadable", guard, 3)
    idle = idle_stats["baseline_c"]
    print(f"[exp3] idle baseline: {idle} C (median of {args.idle_window_s:g} s; "
          f"{idle_stats['min_c']}-{idle_stats['max_c']} C, stable={idle_stats['stable']})", flush=True)
    plan = Plan(k=args.k, hot_frac=args.hot_frac, reps=args.reps, orderings=tuple(ctx["modes"]),
                start_c=args.start_c, preheat_cap_s=args.preheat_cap_s, idle_c=idle,
                margin_c=args.margin_c, cooldown_cap_s=args.cooldown_cap_s, hz=args.hz,
                seed=args.seed, poll_s=0.25 if is_mock else 2.0, rerun_invalid=not args.no_rerun)
    hot, cool = ctx["wk"]["hot"], ctx["wk"]["cool"]
    if is_mock:
        mp = MockPool(b, job_ms=args.mock_job_ms, seed=args.seed)
        make_pool, idle_fn = (lambda: mp), (lambda: b.set_power(0.0))
    else:
        import validate_workload as vw
        print(f"[exp3] starting {P} worker processes (P4c pool) ...", flush=True)
        make_pool = lambda: wl.FixedWorkPool(P, timeout_s=vw.PREREG["job_timeout_s"])  # noqa: E731
        idle_fn = None
    session = Session(b, make_pool, hot, cool, plan, ctx["th"]["knee_c"], check=check, idle=idle_fn,
                      log=lambda s: print(s, flush=True))
    session.run()
    # a second signal must not stop the analysis or the metadata write
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    n_valid = {m: sum(1 for r in session.runs if r["mode"] == m and r["valid"]) for m in plan.orderings}
    meta = dict(_base_meta(b, args, ctx),
                idle_temp_c=idle, idle_window=idle_stats, plan=asdict(plan),
                schedule=[list(x) for x in session.schedule],
                multiset_planned=session.planned,
                planned_identical_across_runs=all(r.get("planned") == session.planned
                                                  for r in session.runs),
                session_complete=session.session_complete, abort_reason=session.abort_reason,
                interrupted=session.interrupted, undervoltage=session.undervoltage,
                monitor=guard.describe() if guard is not None else None,
                runs=session.runs, n_valid=n_valid,
                unstopped_workers=list(session.unstopped))
    why = analyze.e3_invalid(meta)
    meta["a7_validity"] = why or "valid"
    runner.save_run(root, "exp3", meta, session.jobs, session.tele)

    print("\n" + "=" * 62)
    print("EXPERIMENT 3 SESSION (Amendment A7)" + (" - MOCK, NOT A RESULT" if is_mock else ""))
    print("=" * 62)
    print(f"sequences run      : {len(session.runs)} ({len(session.schedule)} scheduled, "
          f"{len(session.runs) - len(session.schedule)} reruns)")
    print(f"valid per ordering : {n_valid}")
    for m in plan.orderings:
        v = [r for r in session.runs if r["mode"] == m and r["valid"]]
        if v:
            print(f"  {m:12s} median p99 {np.median([r['p99_ms'] for r in v]):7.1f} ms, "
                  f"median time above knee {np.median([r['time_above_knee_s'] for r in v]):6.1f} s")
    for r in session.runs:
        if not r["valid"]:
            print(f"  INVALID seq {r['run_index'] + 1} ({r['rep']}, {r['mode']}): {r['invalid_reason']}")
    if session.abort_reason:
        print(f"SESSION STOPPED EARLY: {session.abort_reason}")
    print(f"A7 validity        : {meta['a7_validity']}")
    print("=" * 62)
    if meta["unstopped_workers"]:
        print(f"INTERVENTION REQUIRED: worker pid(s) {meta['unstopped_workers']} still alive after "
              "one SIGTERM; no SIGKILL was sent")
    print(f"\nwrote {root}/exp3.*   (gates: python3 analyze.py --outdir <dir with exp1/exp2/exp3 meta>)")
    if session.interrupted:
        return 130
    return 3 if session.abort_reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
