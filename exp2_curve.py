#!/usr/bin/env python3
"""
EXPERIMENT 2 - service time vs temperature, s(T), under Amendment A4.

The decisive cheap measurement: if s(T) is flat across the accessible
temperature range, thermal state cannot affect latency and TDAS is dead.

A4 (AMENDMENT_A4_exp2.md): on the Pi 4 the firmware releases throttling within
0.15 s of load ending, so "pre-heat, then probe" never sees a throttled probe.
Experiment 2 therefore measures s(T) under its own four-core load:

  - the job is P4c's accepted HOT job (frozen P4c-F1, resolve_workload(...,
    config="4c")), run back to back on all four cores; the job stream is its
    own heater, so the load mix is the same at every temperature;
  - two ramps of 12 min each, each after a cooldown with no load to idle + 3 C
    (at most 20 min, else the run stops INCOMPLETE);
  - per job: the sysfs temperature just before dispatch, the latency, CPU time
    and units, and P4c's between-job `vcgencmd get_throttled` read;
  - during jobs: P4c's 5 Hz sysfs logger, plus the firmware ARM clock read
    in-process through the mailbox (fwclock.py: one ioctl, no process) at about
    1 Hz with seeded jitter U(0.5, 1.5) x 1 s;
  - G2 (analyze.classify_g2): OLS slope of bin-median latency on bin-median
    temperature in 2 C bins per ramp (>= 20 jobs), cluster bootstrap over
    (ramp, bin). Validity as A4 section 4.

A5 (AMENDMENT_A5_exp2.md): four cores heat the die from ~46 C to 70 C in about
12 s, so below knee - 10 C each 2 C bin got fewer than 20 jobs (run
exp2-a4-20261018-01). Each ramp now starts with a paced warm-up (Pacer): below
G1 knee - 10 C the next job is dispatched only while the temperature is under
the top of the lowest bin that has fewer than 30 jobs; otherwise no load until
it is. When those bins are full (or 300 s after the ramp starts) the jobs run
back to back for the rest of the 12 min. The job and the analysis are unchanged.
--no-pace gives the A4 procedure.

A6 (AMENDMENT_A6_exp2.md): the die reaches 85.2 C single readings 5-7 min into
back-to-back load, and run exp2-a4-20261018-02 was safety-stopped at one. Each
ramp now ends 240 s after its paced warm-up ends (--post-pace-s; --full-ramp
gives A5). The 85.0 C watchdog stop is unchanged.

On real hardware it refuses, before creating any file or starting any load,
unless Experiment 1 passes G1 (--exp1-meta), the P4c validation is ACCEPTED
(--workload-validation) and the mailbox clock reads. --require-monitor is
mandatory there (tools/launch_exp2.sh passes it): a FRESH --outdir, no load
until <outdir>/MONITOR_OK names a verified tools/exp2_monitor.sh, and a stop if
the watchdog is lost. On SIGTERM, Ctrl+C or a lost watchdog the jobs stop, the
workers get a stop request and at most one SIGTERM each (never SIGKILL), and
exp2.meta.json is still written, marked ABORTED (INCONCLUSIVE for G2).

    python3 exp2_curve.py --outdir results/RUN --require-monitor 120 \\
        --exp1-meta results/exp1-.../exp1.meta.json \\
        --workload-validation results/workload_validation/wv-p4c-.../report.json
    python3 exp2_curve.py --backend mock --ramp-minutes 0.5 --outdir results_mock

The mock backend simulates jobs (latency 196 ms x f_max / f, from the mock
throttle model) and may run without the barrier: pipeline checks only.

Outputs: <outdir>/exp2.{meta.json,jobs.jsonl,telemetry.jsonl,clock.jsonl}
(+ exp2.start.json with --require-monitor)
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass

import numpy as np

import analyze
import exp1_thermal as e1t
import exp3_ordering as e3
import fwclock
import monitor_guard as mg
import runner
import sensors
import validate_workload as vw
import workload as wl

EXP2_WATCHDOG = "exp2_monitor.sh"
AMENDMENT = "A4"
CONFIG = "4c"
P = 4
MOCK_BASE_MS = 196.0

EXP2_HASHED = ("exp2_curve.py", "analyze.py", "sensors.py", "runner.py", "workload.py",
               "monitor_guard.py", "fwclock.py", "validate_workload.py", "exp3_ordering.py",
               "exp1_thermal.py")


@dataclass
class Plan:
    """A4 section 2. Defaults are the amendment's values."""
    ramps: int = 2
    ramp_s: float = 720.0
    idle_c: float | None = None
    margin_c: float = 3.0
    cooldown_cap_s: float = 1200.0
    hz: float = 5.0
    clock_hz: float = 1.0
    clock_jitter: float = 0.5
    seed: int = 0
    max_temp: float | None = None
    poll_s: float = 2.0
    progress_s: float = 60.0
    # Amendment A5: paced warm-up below pace_limit_c (= G1 knee - 10 C). None = no pacing.
    pace_limit_c: float | None = None
    pace_quota: int = 30
    pace_cap_s: float = 300.0
    pace_poll_s: float = 0.05
    # Amendment A6: the ramp ends this long after the paced warm-up ends (never after
    # ramp_s). None = the ramp runs its full ramp_s (A5).
    post_pace_s: float | None = None


class Pacer:
    """Amendment A5. Below `limit_c` the next job is dispatched only while the
    temperature is under a setpoint: the top edge of the lowest 2 C bin (from the
    ramp's start bin up) that has fewer than `quota` jobs. The bin's jobs are
    counted by the temperature before dispatch, as G2 bins them. Paced bins are
    those lying wholly at or below the limit (b + 2 <= limit_c). When all are
    full, setpoint() returns None and the jobs run back to back."""

    def __init__(self, limit_c, quota=30, bin_c=2.0):
        if quota < 1:
            raise ValueError("quota must be >= 1")
        self.limit, self.quota, self.bin_c = limit_c, quota, bin_c
        self.start_bin, self.counts = None, {}

    def _bin(self, t):
        return math.floor(t / self.bin_c) * self.bin_c

    def setpoint(self, t):
        if self.start_bin is None:
            if t is None:
                return None
            self.start_bin = self._bin(t)
        b = self.start_bin
        while b + self.bin_c <= self.limit:
            if self.counts.get(b, 0) < self.quota:
                return b + self.bin_c
            b += self.bin_c
        return None

    def count(self, t):
        if t is not None:
            b = self._bin(t)
            self.counts[b] = self.counts.get(b, 0) + 1

    def filled(self):
        """Paced bins (from the start bin) and their job counts."""
        if self.start_bin is None:
            return {}
        out, b = {}, self.start_bin
        while b + self.bin_c <= self.limit:
            out[b] = self.counts.get(b, 0)
            b += self.bin_c
        return out


class Exp2Guard(mg.MonitorGuard):
    """monitor_guard's barrier for Experiment 2 (as exp1_thermal.Exp1Guard), but
    the watchdog must be tools/exp2_monitor.sh: argv [..., exp2_monitor.sh, <pid>, <outdir>]."""

    def _watchdog_problem(self, mpid, mstart, check_exe=False):
        if mg.proc_start(mpid) != mstart:
            return f"watchdog pid {mpid} is not running with start time {mstart}"
        argv = mg.proc_argv(mpid)
        if (not argv or len(argv) < 3 or not argv[-3].endswith(EXP2_WATCHDOG)
                or argv[-2] != str(self.pid) or argv[-1] != self.root):
            return f"watchdog pid {mpid} argv {argv!r} does not match"
        if check_exe:
            exe = mg.proc_exe(mpid)
            if not exe or os.path.basename(exe) != "bash":
                return f"watchdog pid {mpid} executable {exe!r} is not bash"
        return None


# --------------------------------------------------------------------------
# Mock stand-ins (pipeline checks only; never a result)
# --------------------------------------------------------------------------
class MockClock:
    """The mock backend's frequency as the 'ARM clock'."""

    def __init__(self, backend):
        self.b = backend

    def read(self):
        return self.b.read().get("freq_mhz")

    def close(self):
        pass


class MockPool:
    """Simulated fixed-work jobs: latency = 196 ms x f_max / f (+ noise), or flat."""

    def __init__(self, backend, job_ms=10.0, flat=False, seed=0):
        self.b, self.job_s, self.flat = backend, job_ms / 1000.0, flat
        self.rng = np.random.default_rng(seed)
        self.unstopped = []

    def alive(self):
        return True

    def run_job(self, spec, record_units=False):
        time.sleep(self.job_s)
        f = self.b.read().get("freq_mhz") or self.b.f_max
        lat = MOCK_BASE_MS * (1.0 if self.flat else self.b.f_max / f)
        return {"class": spec.name, "P": spec.P, "units_planned": spec.planned_units,
                "units_done": spec.planned_units,
                "latency_ms": float(lat + self.rng.normal(0.0, 1.5)), "cpu_ms": None}

    def stop(self):
        return []


# --------------------------------------------------------------------------
# The ramps
# --------------------------------------------------------------------------
class Ramps:
    """Runs plan.ramps ramps of back-to-back HOT jobs. Every outside part is
    injected (backend, pool, clock, flag reader, watchdog check), so the
    hardware path is tested with fakes. Never raises for a run problem: the
    reason lands in abort_reason (and interrupted for SIGTERM, Ctrl+C or a lost
    watchdog), and what ran so far is kept."""

    def __init__(self, backend, pool, hot, clock, plan: Plan, check=None, flags=None,
                 quick=None, power=None, log=print, sleep=time.sleep):
        self.b, self.pool, self.hot, self.clock, self.plan = backend, pool, hot, clock, plan
        self.check = check
        self.flags = flags or (lambda: vw._flags(backend))
        self.quick = quick or (lambda: vw._quick_read(backend))
        self.power, self.log, self.sleep = power, log, sleep
        self.jobs, self.tele, self.clock_samples, self.ramp_info = [], [], [], []
        self.abort_reason = self.interrupted = None
        self.undervoltage, self.jobs_complete, self.ramps_completed = False, True, 0

    def _abort(self, why):
        self.abort_reason = self.abort_reason or why

    def _check(self):
        if self.check is not None:
            self.check()                  # raises mg.MonitorLost (a KeyboardInterrupt)

    def cooldown(self):
        """No load until the temperature is <= idle + margin. Returns (temp, waited_s)."""
        target = self.plan.idle_c + self.plan.margin_c
        t0 = time.monotonic()
        while True:
            self._check()
            t = self.quick().get("temp_c")
            if t is not None and t <= target:
                return t, time.monotonic() - t0
            if time.monotonic() - t0 > self.plan.cooldown_cap_s:
                raise vw.CooldownCap(f"did not cool to {target:.1f} C within "
                                     f"{self.plan.cooldown_cap_s:.0f} s (at {t} C)")
            self.sleep(self.plan.poll_s)

    def _flag_problem(self, th, where):
        if th is None:
            return f"throttle flags unreadable {where}"
        if th & analyze.UNDERVOLT_BITS:
            self.undervoltage = True
            return f"under-voltage {where} ({hex(th)})"
        return None

    def run(self):
        try:
            for r in range(self.plan.ramps):
                self._ramp(r)
                if self.abort_reason:
                    break
                self.ramps_completed += 1
        except KeyboardInterrupt as e:    # SIGTERM, Ctrl+C or monitor_guard.MonitorLost
            self.interrupted = f"interrupted ({e or 'KeyboardInterrupt'})"
            self._abort(self.interrupted)
        return self

    def _ramp(self, r):
        p = self.plan
        info = {"ramp": r}
        self.ramp_info.append(info)
        target = p.idle_c + p.margin_c
        self.log(f"[exp2] ramp {r}: cooling with no load to <= {target:.1f} C "
                 f"(idle {p.idle_c:.1f} + {p.margin_c:g}; cap {p.cooldown_cap_s:.0f} s)")
        try:
            info["start_temp_c"], waited = self.cooldown()
        except vw.CooldownCap as e:
            self._abort(f"INCOMPLETE: ramp {r} cooldown: {e}")
            return
        info["cooldown_s"] = round(waited, 1)
        th0 = self.flags()
        info["flags_before"] = hex(th0) if th0 is not None else None
        why = self._flag_problem(th0, f"before ramp {r}")
        if why is None and th0 & vw.LIVE_FLAG_MASK:
            why = f"live throttle flags before ramp {r} ({hex(th0)})"
        if why:
            self._abort(why)
            return
        logger = runner.TelemetryLogger(vw.SysfsReader(self.b), hz=p.hz)
        sampler = fwclock.ClockSampler(self.clock, hz=p.clock_hz, jitter=p.clock_jitter,
                                       seed=p.seed + r)
        n0, t_ramp = len(self.jobs), time.monotonic()
        pacer = Pacer(p.pace_limit_c, p.pace_quota) if p.pace_limit_c is not None else None
        pacing, wait_from, powered = pacer is not None, None, False
        pace = {"limit_c": p.pace_limit_c, "quota": p.pace_quota, "cap_s": p.pace_cap_s,
                "paced_jobs": 0, "wait_s": 0.0, "end_s": None,
                "end_reason": None if pacer else "off"}
        info["pacing"] = pace
        self.log(f"[exp2] ramp {r}: start at {info['start_temp_c']:.1f} C after "
                 f"{waited:.0f} s; HOT jobs for {p.ramp_s / 60:g} min"
                 + (f", paced below {p.pace_limit_c:.1f} C (A5)" if pacer else ", back to back"))
        end, next_note = t_ramp + p.ramp_s, t_ramp + p.progress_s
        try:
            logger.start()
            sampler.start()
            while time.monotonic() < end:
                self._check()
                q = self.quick()
                tb = q.get("temp_c")
                if p.max_temp is not None and tb is not None and tb >= p.max_temp:
                    self._abort(f"max_temp: {tb:.1f} C >= {p.max_temp} C")
                    break
                if pacing and tb is not None:
                    sp, now = pacer.setpoint(tb), time.monotonic()
                    if sp is None or now - t_ramp >= p.pace_cap_s:
                        pacing = False
                        pace.update(end_s=round(now - t_ramp, 1),
                                    end_reason="filled" if sp is None else "cap")
                        if p.post_pace_s is not None:     # A6: a fixed back-to-back phase
                            end = min(end, now + p.post_pace_s)
                        pace["ramp_end_s"] = round(end - t_ramp, 1)
                        self.log(f"[exp2] ramp {r}: paced warm-up ended at {now - t_ramp:.0f} s "
                                 f"({pace['end_reason']}): {pace['paced_jobs']} paced jobs, "
                                 f"{pace['wait_s']:.0f} s of waits; back to back until "
                                 f"{end - t_ramp:.0f} s")
                    elif tb >= sp:            # A5: no load until below the setpoint
                        if wait_from is None:
                            wait_from = now
                        if self.power and powered:
                            self.power(False)
                            powered = False
                        self.sleep(p.pace_poll_s)
                        continue
                if not self.pool.alive():
                    raise wl.WorkerFailure("pool lost a worker during the ramp")
                if self.power and not powered:
                    self.power(True)
                    powered = True
                ta = time.monotonic()
                rec = self.pool.run_job(self.hot)
                tz = time.monotonic()
                wait_ms = (ta - wait_from) * 1000.0 if wait_from is not None else 0.0
                rec.update(ramp=r, seq=len(self.jobs), ramp_seq=len(self.jobs) - n0,
                           t_start=ta, t_end=tz, temp_before_c=tb,
                           freq_before_mhz=q.get("freq_mhz"),
                           paced=pacing, wait_ms=round(wait_ms, 1))
                if pacing:
                    pacer.count(tb)
                    pace["paced_jobs"] += 1
                    pace["wait_s"] = round(pace["wait_s"] + wait_ms / 1000.0, 3)
                wait_from = None
                self.jobs.append(rec)
                if rec.get("units_done") != rec.get("units_planned"):
                    self.jobs_complete = False
                    self._abort(f"job {rec['seq']} did {rec.get('units_done')} of "
                                f"{rec.get('units_planned')} units")
                    break
                th = self.flags()             # P4c: between jobs, outside the latency
                rec["throttled_after"] = th
                why = self._flag_problem(th, "between jobs")
                if why:
                    self._abort(why)
                    break
                if tz >= next_note:
                    next_note += p.progress_s
                    self.log(f"[exp2] ramp {r}: {tz - t_ramp:4.0f} s, "
                             f"{len(self.jobs) - n0} jobs, T {tb} C, "
                             f"last {rec['latency_ms']:.1f} ms")
        except (wl.JobTimeout, wl.WorkerFailure) as e:
            self.jobs_complete = False
            self._abort(f"{type(e).__name__}: {e}")
        finally:
            if self.power:
                self.power(False)
            tele = logger.stop()
            samples = sampler.stop()
            self.tele.extend(dict(s, ramp=r) for s in tele)
            self.clock_samples.extend({"ramp": r, "t_start": a, "t_end": b, "arm_clock_mhz": v}
                                      for a, b, v in samples)
            temps = [s["temp_c"] for s in tele if s.get("temp_c") is not None]
            info.update(jobs=len(self.jobs) - n0, wall_s=round(time.monotonic() - t_ramp, 1),
                        peak_temp_c=max(temps) if temps else None, clock_reads=len(samples))
            if pacer is not None:
                if pacing:                    # still pacing when the ramp ended or stopped
                    pace.update(end_s=round(time.monotonic() - t_ramp, 1), end_reason="ramp ended")
                pace["bins"] = {f"{b:g}": n for b, n in pacer.filled().items()}
        th1 = self.flags()
        info["flags_after"] = hex(th1) if th1 is not None else None
        why = self._flag_problem(th1, f"after ramp {r}")
        if why:
            self._abort(why)
        self.log(f"[exp2] ramp {r}: {'done' if not self.abort_reason else 'STOPPED'}, "
                 f"{info['jobs']} jobs, peak {info['peak_temp_c']} C, flags "
                 f"{info['flags_before']} -> {info['flags_after']}")


# --------------------------------------------------------------------------
# Descriptive analysis (pure)
# --------------------------------------------------------------------------
def clock_summary(samples):
    """Readability (the >= 95 % rule) and read cost of the ARM-clock reads."""
    vals = [s["arm_clock_mhz"] for s in samples if s.get("arm_clock_mhz") is not None]
    walls = [(s["t_end"] - s["t_start"]) * 1000.0 for s in samples]
    return {"source": None, "reads": len(samples), "readable": len(vals),
            "readable_fraction": round(len(vals) / len(samples), 4) if samples else None,
            "min_mhz": min(vals) if vals else None, "max_mhz": max(vals) if vals else None,
            "read_wall_ms_median": round(float(np.median(walls)), 3) if walls else None,
            "read_wall_ms_max": round(max(walls), 3) if walls else None}


def attach_clock(jobs, samples):
    """Per job, the median ARM clock of readable reads whose midpoint lies inside
    the job ([t_start, t_end]); None if no read overlapped it. Descriptive only."""
    mids = sorted(((s["t_start"] + s["t_end"]) / 2.0, s["arm_clock_mhz"]) for s in samples
                  if s.get("arm_clock_mhz") is not None)
    keys = [m for m, _ in mids]
    for j in jobs:
        if j.get("t_start") is None:
            continue
        lo = bisect.bisect_left(keys, j["t_start"])
        hi = bisect.bisect_right(keys, j["t_end"])
        v = [mids[i][1] for i in range(lo, hi)]
        j["arm_clock_mhz"] = float(np.median(v)) if v else None


def mediation(jobs, step_mhz=50.0):
    """Latency against the ARM clock (A4: descriptive). Jobs with a clock read are
    grouped by clock (step_mhz levels); for each level the observed median latency
    and the latency predicted from the fastest level by f_ref / f."""
    pts = [(j["arm_clock_mhz"], j["latency_ms"]) for j in jobs
           if j.get("arm_clock_mhz") is not None and j.get("latency_ms") is not None]
    if not pts:
        return {"jobs_with_clock": 0, "levels": []}
    groups = {}
    for f, lat in pts:
        groups.setdefault(round(f / step_mhz) * step_mhz, []).append((f, lat))
    levels = []
    for lvl in sorted(groups, reverse=True):
        v = groups[lvl]
        levels.append({"clock_mhz": float(np.median([x[0] for x in v])), "n": len(v),
                       "latency_median_ms": float(np.median([x[1] for x in v]))})
    ref = levels[0]
    for lv in levels:
        lv["predicted_ms"] = ref["latency_median_ms"] * ref["clock_mhz"] / lv["clock_mhz"]
    return {"jobs_with_clock": len(pts), "step_mhz": step_mhz, "levels": levels}


def pacing_latency(jobs, knee_c):
    """A5, descriptive only: median latency of paced jobs (all, and those dispatched
    after an idle wait) against unpaced jobs still below the knee. Never used by G2."""
    def summ(v):
        v = sorted(v)
        return {"n": len(v), "latency_median_ms": round(v[len(v) // 2], 2) if v else None}
    paced = [j["latency_ms"] for j in jobs if j.get("paced")]
    waited = [j["latency_ms"] for j in jobs if j.get("paced") and (j.get("wait_ms") or 0) > 0]
    b2b = [j["latency_ms"] for j in jobs if not j.get("paced") and knee_c is not None
           and j.get("temp_before_c") is not None and j["temp_before_c"] < knee_c]
    return {"paced": summ(paced), "after_wait": summ(waited), "back_to_back_below_knee": summ(b2b)}


def support_knee(samples, tele, max_gap_s=0.5):
    """Exp2's own knee in the ARM clock (supporting evidence only): each readable
    clock read paired with the nearest telemetry temperature within max_gap_s."""
    tt = sorted((s["t_wall"], s["temp_c"]) for s in tele
                if s.get("temp_c") is not None and s.get("t_wall") is not None)
    keys = [t for t, _ in tt]
    T, F = [], []
    for s in samples:
        if s.get("arm_clock_mhz") is None or not keys:
            continue
        m = (s["t_start"] + s["t_end"]) / 2.0
        i = bisect.bisect_left(keys, m)
        best = min((k for k in (i - 1, i) if 0 <= k < len(keys)), key=lambda k: abs(keys[k] - m))
        if abs(keys[best] - m) <= max_gap_s:
            T.append(tt[best][1])
            F.append(s["arm_clock_mhz"])
    return e1t.find_knee(T, F) if T else None


def exp2_outcome(meta):
    """End-of-run outcome by the same rule analyze.py applies (classify_g2)."""
    state, sl, why = analyze.classify_g2(meta)
    lines = []
    if sl:
        lines.append(f"slope {sl['slope_ms_per_C']:+.3f} ms/C [95% CI {sl['ci_lo']:+.3f}, "
                     f"{sl['ci_hi']:+.3f}] over {sl['n_bins']} bins")
    if state == "PASS":
        return state, lines + ["G2 PASS: service time rises with temperature.",
                               "Proceed to Experiment 3."]
    if state == "FAIL":
        return state, lines + ["KILL SIGNAL:", "  - G2 FAIL: " + why]
    return "INCONCLUSIVE", lines + [f"INCONCLUSIVE: {why}",
                                    "  Neither a pass nor a G2 failure. Do not proceed on "
                                    "this run; fix the cause and rerun Experiment 2."]


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
    raise KeyboardInterrupt("SIGTERM")     # route through the same cleanup


def _workload_record(info):
    if info is None:
        return None
    out = {k: v for k, v in info.items() if k not in ("hot", "cool")}
    out["hot"] = asdict(info["hot"])
    out["report_sha256"] = _sha256_file(info.get("report"))
    return out


def preflight(args, b, is_mock):
    """Everything that must hold before any file or load. Returns (ctx, refusal)."""
    if is_mock:
        g1 = {"state": "MOCK", "knee_c": args.mock_knee, "reason": "mock --mock-knee",
              "exp1_meta": None, "exp1_meta_sha256": None}
    else:
        e1 = e3._load_json(args.exp1_meta)
        state, knee, depth, why = analyze.classify_g1(e1, None)
        if state != "PASS":
            return None, f"Experiment 1 does not pass G1 ({state}: {why}); --exp1-meta {args.exp1_meta!r}"
        g1 = {"state": state, "knee_c": knee, "depth": depth, "reason": why,
              "exp1_meta": args.exp1_meta, "exp1_meta_sha256": _sha256_file(args.exp1_meta)}
    info, refusal = e3.resolve_workload(args.workload_validation, P, is_mock, config=CONFIG)
    if refusal:
        return None, f"workload: {refusal}"
    if not is_mock and info.get("telemetry") != "sysfs":
        return None, f"workload telemetry is {info.get('telemetry')!r}, P4c needs 'sysfs'"
    if is_mock:
        clock, source = MockClock(b), "mock backend frequency"
    else:
        try:
            clock, source = fwclock.ArmClockMailbox(), "firmware mailbox (/dev/vcio)"
        except OSError as e:
            return None, f"ARM clock: cannot open {fwclock.VCIO} ({e})"
        if clock.read() is None:
            clock.close()
            return None, "ARM clock: the mailbox read returned no value"
    return {"g1": g1, "workload": info, "clock": clock, "clock_source": source}, None


def _save_early_stop(outdir, b, args, reason, guard, rc, ctx):
    meta = {"experiment": "exp2_curve", "amendment": AMENDMENT, "backend": b.name,
            "mock": b.name == "mock", "abort_reason": reason, "interrupted": None,
            "monitor": guard.describe() if guard is not None else None,
            "workload": _workload_record(ctx["workload"]), "g1": ctx["g1"],
            "g1_knee_c": ctx["g1"]["knee_c"], "ramps_planned": args.ramps,
            "ramps_completed": 0, "jobs_complete": None, "bins": [],
            "exp2_outcome": "INCONCLUSIVE"}
    runner.save_run(outdir, "exp2", meta, [], [])
    print(f"[exp2] stopped before load: {reason} (no load ran)")
    return rc


def build_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None)
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--exp1-meta", default=None,
                    help="exp1.meta.json of an Experiment 1 that passes G1 (real hardware)")
    ap.add_argument("--workload-validation", default=None,
                    help="report.json of an ACCEPTED P4c workload validation (real hardware)")
    ap.add_argument("--ramps", type=int, default=2)
    ap.add_argument("--ramp-minutes", type=float, default=12.0)
    ap.add_argument("--margin-c", type=float, default=3.0,
                    help="cooldown before each ramp to idle + this (C)")
    ap.add_argument("--cooldown-cap-s", type=float, default=1200.0)
    ap.add_argument("--idle-window-s", type=float, default=30.0)
    ap.add_argument("--hz", type=float, default=5.0, help="sysfs telemetry rate")
    ap.add_argument("--clock-hz", type=float, default=1.0, help="ARM-clock read rate")
    ap.add_argument("--clock-jitter", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=0, help="clock jitter seed (ramp r uses seed + r)")
    ap.add_argument("--max-temp", type=float, default=None,
                    help="stop the run (ABORTED) if the temperature before a job reaches this")
    ap.add_argument("--pace-quota", type=int, default=30,
                    help="A5: jobs per 2 C bin below G1 knee - 10 C before the setpoint moves up")
    ap.add_argument("--pace-cap-s", type=float, default=300.0,
                    help="A5: pacing ends at most this long after the ramp starts")
    ap.add_argument("--no-pace", action="store_true",
                    help="A4 procedure without the A5 paced warm-up (jobs back to back throughout)")
    ap.add_argument("--post-pace-s", type=float, default=240.0,
                    help="A6: each ramp ends this long after its paced warm-up ends (at most "
                         "--ramp-minutes after it starts)")
    ap.add_argument("--full-ramp", action="store_true",
                    help="A5 procedure: no A6 cut, every ramp runs --ramp-minutes")
    ap.add_argument("--require-monitor", type=float, default=None, metavar="SECONDS",
                    help="MANDATORY on real hardware (tools/launch_exp2.sh passes it): fresh "
                         "--outdir, no load until a verified tools/exp2_monitor.sh")
    ap.add_argument("--mock-knee", type=float, default=80.0)
    ap.add_argument("--mock-tau", type=float, default=6.0)
    ap.add_argument("--mock-power", type=float, default=1.4)
    ap.add_argument("--mock-job-ms", type=float, default=10.0)
    ap.add_argument("--mock-flat", action="store_true",
                    help="mock latency independent of the clock (exercises G2 FAIL)")
    args = ap.parse_args(argv)
    if args.ramps < 1:
        ap.error("--ramps must be >= 1")
    if not args.ramp_minutes > 0:
        ap.error("--ramp-minutes must be > 0")
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
              "with tools/launch_exp2.sh, which starts and verifies the watchdog.")
        return 2
    ctx, refusal = preflight(args, b, is_mock)
    if refusal:
        print(f"refused: {refusal} (nothing written, no load)")
        return 2
    hot, clock = ctx["workload"]["hot"], ctx["clock"]
    print(f"[exp2] backend={b.name} amendment {AMENDMENT}: G1 knee {ctx['g1']['knee_c']:.1f} C "
          f"({ctx['g1']['state']}); HOT job {hot}; clock {ctx['clock_source']}")

    root = args.outdir.rstrip("/") or args.outdir
    guard = check = None
    if args.require_monitor is not None:
        run_id = os.path.basename(root)
        try:
            os.makedirs(root)              # fresh directory only: never overwrite a run
        except FileExistsError:
            clock.close()
            print(f"refused: {root} exists; refusing to overwrite")
            return 2
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, "exp2.start.json"), "w") as f:
            json.dump({"pid": os.getpid(), "run_id": run_id, "argv": sys.argv,
                       "written": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "exp1_meta_sha256": ctx["g1"]["exp1_meta_sha256"],
                       "workload_report_sha256": _sha256_file(ctx["workload"].get("report")),
                       "sha256": {m: _sha256_file(os.path.join(here, m)) for m in EXP2_HASHED}},
                      f, indent=1)
    signal.signal(signal.SIGTERM, _sigterm)
    try:
        if args.require_monitor is not None:
            guard = Exp2Guard(root, run_id)
            print(f"[exp2] run {run_id}: waiting up to {args.require_monitor:.0f} s for a "
                  "verified watchdog (no load until then)", flush=True)
            try:
                why = guard.wait(args.require_monitor)
            except KeyboardInterrupt as e:
                return _save_early_stop(root, b, args, f"interrupted at the barrier ({e})",
                                        None, 130, ctx)
            if why:
                return _save_early_stop(root, b, args,
                                        f"refused: monitoring not established ({why})", None, 2, ctx)
            print(f"[exp2] monitoring verified: {guard.describe()}", flush=True)
            check = guard.raise_if_lost
        return _run(args, b, is_mock, ctx, root, guard, check)
    finally:
        clock.close()


def _run(args, b, is_mock, ctx, root, guard, check):
    hot, clock = ctx["workload"]["hot"], ctx["clock"]
    try:
        idle_stats = e1t.measure_idle_baseline(b, args.idle_window_s, check=check)
    except KeyboardInterrupt as e:
        return _save_early_stop(root, b, args, f"interrupted before load ({e or 'KeyboardInterrupt'})",
                                guard, 130, ctx)
    if not idle_stats:
        return _save_early_stop(root, b, args, "idle temperature unreadable", guard, 3, ctx)
    idle = idle_stats["baseline_c"]
    print(f"[exp2] idle baseline: {idle} C (median of {args.idle_window_s:g} s; "
          f"{idle_stats['min_c']}-{idle_stats['max_c']} C, stable={idle_stats['stable']})", flush=True)

    plan = Plan(ramps=args.ramps, ramp_s=args.ramp_minutes * 60.0, idle_c=idle,
                margin_c=args.margin_c, cooldown_cap_s=args.cooldown_cap_s, hz=args.hz,
                clock_hz=args.clock_hz, clock_jitter=args.clock_jitter, seed=args.seed,
                max_temp=args.max_temp, poll_s=0.5 if is_mock else 2.0,
                progress_s=10.0 if is_mock else 60.0,
                pace_limit_c=None if args.no_pace else ctx["g1"]["knee_c"] - analyze.G2_RULE["below_knee_c"],
                pace_quota=args.pace_quota, pace_cap_s=args.pace_cap_s,
                post_pace_s=None if args.full_ramp or args.no_pace else args.post_pace_s)
    pool = power = None
    try:
        if is_mock:
            pool = MockPool(b, job_ms=args.mock_job_ms, flat=args.mock_flat, seed=args.seed)
            power = lambda on: b.set_power(args.mock_power if on else 0.0)   # noqa: E731
        else:
            # Started before the first cooldown, so process start-up heat is never in a ramp.
            print(f"[exp2] starting {P} worker processes (P4c pool) ...", flush=True)
            pool = wl.FixedWorkPool(P, timeout_s=vw.PREREG["job_timeout_s"])
        ramps = Ramps(b, pool, hot, clock, plan, check=check, power=power,
                      log=lambda s: print(s, flush=True)).run()
    except KeyboardInterrupt as e:            # during the pool start
        if pool is not None:
            pool.stop()
        return _save_early_stop(root, b, args, f"interrupted before load ({e or 'KeyboardInterrupt'})",
                                guard, 130, ctx)
    except wl.WorkerFailure as e:
        return _save_early_stop(root, b, args, f"pool start failed: {e}", guard, 3, ctx)
    finally:
        # A second signal must not stop the cleanup, the analysis or the metadata write.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if pool is not None:
            pool.stop()                       # graceful, then at most one SIGTERM; never SIGKILL

    attach_clock(ramps.jobs, ramps.clock_samples)
    ck = clock_summary(ramps.clock_samples)
    ck.update(source=ctx["clock_source"], hz=args.clock_hz, jitter=args.clock_jitter,
              seed=args.seed)
    knee = support_knee(ramps.clock_samples, ramps.tele)
    meta = {
        "experiment": "exp2_curve", "amendment": AMENDMENT,
        "procedure": ("A4" if plan.pace_limit_c is None else "A4+A5 paced warm-up"
                      + (f"+A6 {plan.post_pace_s:g} s back to back" if plan.post_pace_s is not None
                         else "")),
        "backend": b.name, "mock": is_mock,
        "workload": _workload_record(ctx["workload"]),
        "g1": ctx["g1"], "g1_knee_c": ctx["g1"]["knee_c"],
        "idle_temp_c": idle, "idle_window": idle_stats,
        "plan": asdict(plan),
        "ramps_planned": plan.ramps, "ramps_completed": ramps.ramps_completed,
        "ramps": ramps.ramp_info,
        "n_jobs": len(ramps.jobs), "jobs_complete": ramps.jobs_complete,
        "undervoltage": ramps.undervoltage,
        "abort_reason": ramps.abort_reason, "interrupted": ramps.interrupted,
        "monitor": guard.describe() if guard is not None else None,
        "clock": ck,
        "bins": analyze.g2_bins(ramps.jobs),
        "mediation": mediation(ramps.jobs),            # descriptive only
        "pacing_latency": pacing_latency(ramps.jobs, ctx["g1"]["knee_c"]),   # descriptive (A5)
        "knee": knee,                                  # supporting evidence only
        "knee_c": knee.get("knee_c") if knee else None,
        "unstopped_workers": list(getattr(pool, "unstopped", None) or []),
    }
    state, sl, why = analyze.classify_g2(meta)
    meta["g2"] = {"state": state, "slope": sl, "reason": why}
    outcome, lines = exp2_outcome(meta)
    meta["exp2_outcome"] = outcome
    runner.save_run(root, "exp2", meta, ramps.jobs, ramps.tele)
    runner.write_jsonl(os.path.join(root, "exp2.clock.jsonl"), ramps.clock_samples)

    print("\n" + "=" * 62)
    print("EXPERIMENT 2 RESULT - s(T) (Amendment A4)" + (" - MOCK, NOT A RESULT" if is_mock else ""))
    print("=" * 62)
    print(f"ramps completed    : {ramps.ramps_completed} of {plan.ramps}")
    print(f"jobs               : {len(ramps.jobs)} (all complete: {ramps.jobs_complete})")
    for ri in ramps.ramp_info:
        print(f"  ramp {ri['ramp']}: start {ri.get('start_temp_c')} C, peak {ri.get('peak_temp_c')} C, "
              f"{ri.get('jobs')} jobs, flags {ri.get('flags_before')} -> {ri.get('flags_after')}")
        pc = ri.get("pacing")
        if pc and pc.get("end_reason") != "off":
            print(f"    paced warm-up (A5): {pc['paced_jobs']} jobs, {pc['wait_s']:.0f} s of waits, "
                  f"ended at {pc['end_s']} s ({pc['end_reason']}), ramp to {pc.get('ramp_end_s')} s; "
                  f"jobs per paced bin {pc.get('bins')}")
    pl = meta["pacing_latency"]
    if pl["paced"]["n"]:
        print(f"paced vs back-to-back jobs (descriptive): median {pl['paced']['latency_median_ms']:.1f} ms "
              f"(n {pl['paced']['n']}, after a wait: {pl['after_wait']['latency_median_ms']} ms, "
              f"n {pl['after_wait']['n']}) vs {pl['back_to_back_below_knee']['latency_median_ms']} ms "
              f"(back to back below the knee, n {pl['back_to_back_below_knee']['n']})")
    print(f"ARM clock          : {ck['readable']} of {ck['reads']} reads readable "
          f"({ck['source']})")
    print(f"kept bins          : {len(meta['bins'])} (2 C, >= 20 jobs, per ramp)")
    for bn in meta["bins"]:
        print(f"  ramp {bn['ramp']} {bn['bin_lo_c']:5.1f} C: n={bn['n']:4d}  "
              f"T~{bn['temp_median_c']:.1f}  latency~{bn['latency_median_ms']:.1f} ms")
    if meta["knee_c"] is not None:
        print(f"exp2 ARM-clock knee: {meta['knee_c']:.1f} C (supporting evidence only)")
    if ramps.abort_reason:
        print(f"RUN STOPPED EARLY  : {ramps.abort_reason}")
    print("\n--- GATE CHECK (G2 / P1) ---")
    for line in lines:
        print(line)
    print("=" * 62)
    if meta["unstopped_workers"]:
        print(f"INTERVENTION REQUIRED: worker pid(s) {meta['unstopped_workers']} still alive "
              "after one SIGTERM; no SIGKILL was sent")
    print(f"\nwrote {root}/exp2.*")
    if ramps.interrupted:
        return 130
    return 3 if ramps.abort_reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
