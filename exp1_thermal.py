#!/usr/bin/env python3
"""
EXPERIMENT 1 - thermal response, recovery timescale, throttle knee.

Answers P3 (is tau comparable to the reordering horizon?) and P4 (does the
device reach the knee under realistic sustained load?).

    python3 exp1_thermal.py --minutes 15 --outdir results
    python3 exp1_thermal.py --workers 4 --minutes 15 --outdir results_4core
    python3 exp1_thermal.py --backend mock --minutes 2 --outdir results_mock

--workers N runs N independent HOT worker processes during heating. Needed
because numpy's BLAS may be single-threaded (Debian's reference BLAS is), in
which case one process loads exactly one core. --workers 1 (the default) is
the original single-process behavior. Keep 1-core and N-core results in
separate --outdir directories; they are different characterizations.

Outputs: results/exp1.{meta.json,telemetry.jsonl}
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue
import signal
import time

import numpy as np

import analyze
import sensors
import runner
import workload as wl


def fit_exponential(t, y, cooling: bool):
    """
    Fit y(t) = y_inf + (y0 - y_inf) * exp(-t/tau) by grid search over tau
    with least-squares closed form for (y0, y_inf) at each tau.
    Robust and dependency-free; good enough to decide a go/no-go.
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    t = t - t[0]
    best = None
    for tau in np.geomspace(0.5, 2000.0, 400):
        e = np.exp(-t / tau)
        A = np.column_stack([np.ones_like(e), e])   # [y_inf, (y0-y_inf)]
        try:
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        except np.linalg.LinAlgError:
            continue
        resid = y - A @ coef
        ss = float(resid @ resid)
        if best is None or ss < best["ss"]:
            best = {"tau": float(tau), "y_inf": float(coef[0]),
                    "y0": float(coef[0] + coef[1]), "ss": ss,
                    "resid": resid}
    if best is None:
        return None
    ss_tot = float(((y - y.mean()) ** 2).sum())
    best["r2"] = 1.0 - best["ss"] / ss_tot if ss_tot > 0 else float("nan")
    # structured-residual check -> is the system second order?
    r = best["resid"]
    if len(r) > 20:
        half = len(r) // 2
        best["resid_first_half_mean"] = float(r[:half].mean())
        best["resid_second_half_mean"] = float(r[half:].mean())
        best["possible_second_order"] = bool(
            abs(r[:half].mean() - r[half:].mean()) > 0.5 * np.std(y) * 0.2
        )
    best.pop("resid", None)
    return best


def find_knee(temps, freqs):
    """
    Locate the temperature at which frequency first drops below its max.
    Returns (knee_c, f_max, f_min, depth) or None if frequency never drops.
    """
    temps = np.asarray([t for t in temps], float)
    freqs = np.asarray([f for f in freqs], float)
    m = ~(np.isnan(temps) | np.isnan(freqs))
    temps, freqs = temps[m], freqs[m]
    if len(freqs) == 0:
        return None
    f_max = float(np.max(freqs))
    below = freqs < f_max * 0.98
    if not below.any():
        return {"knee_c": None, "f_max": f_max, "f_min": f_max,
                "depth": 0.0, "throttled_samples": 0}
    knee = float(np.min(temps[below]))
    f_min = float(np.min(freqs))
    return {"knee_c": knee, "f_max": f_max, "f_min": f_min,
            "depth": 1.0 - f_min / f_max,
            "throttled_samples": int(below.sum())}


# --------------------------------------------------------------------------
# Multicore heating (--workers N > 1)
#
# Each worker is a separate spawned process that builds its OWN HOT Workload,
# so no mutable workload state is shared. The parent owns shutdown: workers
# ignore SIGINT (Ctrl+C reaches the whole process group), stop when the stop
# event is set, and exit by themselves if the parent dies (getppid changes),
# so no worker outlives the experiment.
# --------------------------------------------------------------------------
def _heat_worker(idx, n, target_ms, go, stop, parent_pid, q):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    hot = wl.Workload("HOT", n, duty=1.0, target_ms=target_ms)
    q.put(("ready", idx, os.getpid()))
    while not go.wait(0.5):
        if stop.is_set() or os.getppid() != parent_pid:
            return
    jobs = 0
    t0, c0 = time.monotonic(), time.process_time()
    while not stop.is_set() and os.getppid() == parent_pid:
        hot.run_once()
        jobs += 1
    q.put(("done", idx, {"pid": os.getpid(), "jobs": jobs,
                         "cpu_s": round(time.process_time() - c0, 3),
                         "wall_s": round(time.monotonic() - t0, 3)}))


class HeatWorkers:
    """N independent HOT worker processes, started idle and released together."""

    def __init__(self, workers, n, target_ms, ready_timeout_s=120.0):
        ctx = mp.get_context("spawn")      # clean interpreters, no forked threads
        self.go, self.stop_evt, self.q = ctx.Event(), ctx.Event(), ctx.Queue()
        self.procs = [
            ctx.Process(target=_heat_worker, name=f"heat-{i}", daemon=True,
                        args=(i, n, target_ms, self.go, self.stop_evt,
                              os.getpid(), self.q))
            for i in range(workers)]
        self.reports, self.forced, self._stopped = {}, [], False
        for p in self.procs:
            p.start()
        try:
            ready, deadline = 0, time.monotonic() + ready_timeout_s
            while ready < workers:
                if self.dead():
                    raise RuntimeError(f"worker(s) died during startup: {self.dead()}")
                if time.monotonic() > deadline:
                    raise RuntimeError("workers did not become ready in time")
                try:
                    if self.q.get(timeout=0.5)[0] == "ready":
                        ready += 1
                except queue.Empty:
                    pass
        except BaseException:
            self.stop()
            raise

    def start(self):
        self.go.set()

    def dead(self):
        return [p.name for p in self.procs if not p.is_alive()]

    def stop(self, timeout_s=5.0):
        """Idempotent. Graceful stop, then terminate, then kill. Never orphans."""
        if self._stopped:
            return self.report()
        self._stopped = True
        self.stop_evt.set()
        deadline = time.monotonic() + timeout_s
        while len(self.reports) < len(self.procs) and time.monotonic() < deadline:
            try:
                msg = self.q.get(timeout=0.2)
            except queue.Empty:
                if not any(p.is_alive() for p in self.procs):
                    break
                continue
            if msg[0] == "done":
                self.reports[msg[1]] = msg[2]
        for p in self.procs:
            p.join(max(0.0, deadline - time.monotonic()))
            if p.is_alive():
                self.forced.append(p.name)
                p.terminate()
                p.join(2.0)
            if p.is_alive():
                p.kill()
                p.join(2.0)
        return self.report()

    def report(self):
        out = []
        for i, p in enumerate(self.procs):
            r = {"pid": p.pid, "jobs": None, "cpu_s": None, "wall_s": None}
            r.update(self.reports.get(i, {}))
            r.update({"worker": i, "exitcode": p.exitcode,
                      "forced_stop": p.name in self.forced})
            out.append(r)
        return out


def _sigterm(signum, frame):
    raise KeyboardInterrupt("SIGTERM")     # route through the same cleanup


def _latest_temp(log):
    for s in reversed(log.samples[-5:]):
        if s.get("temp_c") is not None:
            return s["temp_c"]
    return None


def _cpu_times():
    """Per-core (busy, total) jiffies from /proc/stat; None off Linux."""
    try:
        out = []
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu") and line[3].isdigit():
                    v = [int(x) for x in line.split()[1:9]]
                    idle = v[3] + v[4]                  # idle + iowait
                    out.append((sum(v) - idle, sum(v)))
        return out or None
    except (OSError, IndexError, ValueError):
        return None


def _cpu_util(before, after):
    if not before or not after or len(before) != len(after):
        return None
    busy = [round((a[0] - b[0]) / (a[1] - b[1]), 3) if a[1] > b[1] else None
            for b, a in zip(before, after)]
    return {"per_core_busy": busy,
            "avg_cores_busy": round(sum(x for x in busy if x is not None), 3)}


def _blas_libs():
    """BLAS/LAPACK shared libraries actually mapped into this process."""
    try:
        libs = set()
        with open("/proc/self/maps") as f:
            for ln in f:
                path = ln.split()[-1]
                if ".so" in path and any(k in path.lower() for k in
                                         ("blas", "lapack", "blis", "mkl")):
                    libs.add(path)
        return sorted(libs)
    except (OSError, IndexError):
        return None


def _heat_summary(samples, t0, t1):
    s = [x for x in samples
         if x.get("temp_c") is not None and t0 <= x.get("t_wall", -1) <= t1]
    if not s:
        return None
    fr = [x["freq_mhz"] for x in s if x.get("freq_mhz") is not None]
    th = [x["throttled"] for x in s if x.get("throttled") is not None]
    th_or = 0
    for v in th:
        th_or |= v
    return {"samples": len(s),
            "temp_min_c": min(x["temp_c"] for x in s),
            "temp_max_c": max(x["temp_c"] for x in s),
            "freq_min_mhz": min(fr) if fr else None,
            "freq_max_mhz": max(fr) if fr else None,
            "throttled_or": hex(th_or) if th else None,
            "throttled_nonzero_samples": sum(1 for v in th if v)}


# --------------------------------------------------------------------------
# Idle baseline, cooling termination, and the P4 verdict.
# Kept as small pure pieces so test_exp1_thermal.py can pin their behavior.
# --------------------------------------------------------------------------
SAFETY_STOP_LABEL = "SAFETY-STOPPED: INCONCLUSIVE FOR KNEE DETECTION"
COOL_TOL_C = 1.0            # "back at idle" = within this of the baseline
COOL_CONSECUTIVE = 10       # readings in a row (0.5 s apart) to end cooling
IDLE_STABLE_RANGE_C = 1.5   # max-min over the idle window to call it stable


def measure_idle_baseline(backend, window_s, period_s=0.5):
    """Median of idle readings over window_s, with the spread recorded.

    A single read at launch is one noisy ~0.5 C-quantized sample; the cooling
    phase compares against this baseline, so it must be a stable level.
    """
    vals, end = [], time.monotonic() + window_s
    while True:
        t = backend.read().get("temp_c")
        if t is not None:
            vals.append(t)
        if time.monotonic() >= end:
            break
        time.sleep(period_s)
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    return {"baseline_c": round(float(np.median(vals)), 3), "min_c": lo,
            "max_c": hi, "n": len(vals), "window_s": window_s,
            "stable": (hi - lo) <= IDLE_STABLE_RANGE_C}


class CoolingTerminator:
    """End cooling only after `needed` CONSECUTIVE readings within tol of the
    idle baseline. One qualifying (possibly transient) reading never ends it;
    a non-qualifying or missing reading resets the streak."""

    def __init__(self, baseline, tol=COOL_TOL_C, needed=COOL_CONSECUTIVE):
        if needed < 2:
            raise ValueError("needed must be >= 2 (never end on one sample)")
        self.baseline, self.tol, self.needed, self.streak = \
            baseline, tol, needed, 0

    def update(self, temp) -> bool:
        if (self.baseline is None or temp is None
                or abs(temp - self.baseline) > self.tol):
            self.streak = 0
        else:
            self.streak += 1
        return self.streak >= self.needed


def p4_verdict(knee, abort_reason, util, is_mock):
    """Classify the P4 gate. Returns (status, report_lines).

    A run whose heating was cut short (safety stop, worker death) can never
    yield P4 FAIL: the knee may lie above the point where heating stopped.
    """
    knee_seen = bool(knee) and knee.get("knee_c") is not None
    if abort_reason:
        if abort_reason.startswith("max_temp"):
            status, head = "SAFETY_STOPPED", SAFETY_STOP_LABEL
        else:
            status = "ABORTED"
            head = f"ABORTED: INCONCLUSIVE FOR KNEE DETECTION ({abort_reason})"
        lines = [head, f"  Heating ended early: {abort_reason}."]
        if knee_seen:
            lines.append(f"  A knee WAS observed at {knee['knee_c']:.1f} C before "
                         "the stop; throttle depth may be underestimated.")
        else:
            lines.append("  No knee observed before the stop. The knee may lie "
                         "above the stop point:")
            lines.append("  this is neither a pass nor a P4 failure.")
        return status, lines
    n_cores = len(util["per_core_busy"]) if util else None
    if (not is_mock and util
            and util["avg_cores_busy"] < n_cores - 0.5):
        return "PARTIAL_LOAD", [
            f"INCONCLUSIVE: heating loaded only {util['avg_cores_busy']:.2f} "
            f"of {n_cores} cores.",
            "  A knee recorded under partial load does not establish P4." if knee_seen
            else "  A missing knee is NOT a P4 verdict for sustained max load.",
            f"  Rerun with --workers {n_cores} into a separate --outdir."]
    if not knee_seen:
        return "P4_FAIL", [
            "KILL SIGNALS:",
            "  - P4 FAIL: no throttle knee reached under sustained load", "",
            "If P4 failed, TDAS is dead on this hardware. Stop here."]
    if knee["depth"] < 0.05:
        return "P4_WEAK", [
            "KILL SIGNALS:",
            f"  - P1/P4 WEAK: throttle depth only {knee['depth']*100:.1f}%", "",
            "If P4 failed, TDAS is dead on this hardware. Stop here."]
    return "NO_KILL", ["No kill signal from Experiment 1. Proceed to Experiment 2."]


def exp1_outcome(meta):
    """End-of-run outcome. Returns (outcome, report_lines).

    Decided by the same Experiment 1 validity rule analyze.py applies
    (analyze.classify_g1), so the run can only recommend proceeding when the
    analysis would accept its knee. Three outcomes:
      VALID_KNEE / VALID_KNEE_WEAK  valid run, knee measured
      VALID_NO_KNEE                 valid completed run, no knee (P4 FAIL)
      INCONCLUSIVE                  invalid, aborted or safety-stopped run
    """
    state, knee, depth, why = analyze.classify_g1(meta, None)
    if state == "PASS":
        head = f"VALID RUN, KNEE MEASURED at {knee:.1f} C (depth {depth*100:.1f}%)"
        if depth < 0.05:
            return "VALID_KNEE_WEAK", [
                head, "KILL SIGNALS:",
                f"  - P1/P4 WEAK: throttle depth only {depth*100:.1f}%", "",
                "If P4 failed, TDAS is dead on this hardware. Stop here."]
        return "VALID_KNEE", [
            head, "No kill signal from Experiment 1. Proceed to Experiment 2."]
    if state == "FAIL":
        return "VALID_NO_KNEE", [
            "VALID COMPLETED RUN, NO KNEE (full load, full duration)",
            "KILL SIGNALS:",
            "  - P4 FAIL: no throttle knee reached under sustained load", "",
            "If P4 failed, TDAS is dead on this hardware. Stop here."]
    status = meta.get("p4_status")
    head = (SAFETY_STOP_LABEL if status == "SAFETY_STOPPED" else
            "ABORTED: INCONCLUSIVE FOR KNEE DETECTION" if status == "ABORTED"
            else "INCONCLUSIVE: THIS RUN CANNOT DECIDE P4")
    return "INCONCLUSIVE", [
        head, f"  Reason: {why}",
        "  Neither a pass nor a P4 failure. Do not proceed on this run;",
        "  fix the cause and rerun Experiment 1."]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None)
    ap.add_argument("--minutes", type=float, default=15.0,
                    help="sustained load duration (1a)")
    ap.add_argument("--cool-minutes", type=float, default=10.0,
                    help="max cooling observation (1b)")
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--n", type=int, default=512, help="GEMM size")
    ap.add_argument("--workers", type=int, default=1,
                    help="independent HOT worker processes during heating "
                         "(1 = original single-process behavior)")
    ap.add_argument("--max-temp", type=float, default=None,
                    help="safety: stop heating early if temperature reaches "
                         "this (C); the stop is recorded in the metadata")
    ap.add_argument("--idle-window-s", type=float, default=30.0,
                    help="seconds of idle readings whose median is the "
                         "baseline the cooling phase returns to")
    ap.add_argument("--idle-baseline", type=float, default=None,
                    help="use this recorded pre-run idle temperature (C) as "
                         "the baseline instead of the measured median")
    ap.add_argument("--cool-consecutive", type=int, default=COOL_CONSECUTIVE,
                    help="consecutive readings (0.5 s apart) within "
                         f"{COOL_TOL_C} C of baseline needed to end cooling early")
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("--workers must be >= 1")
    if args.cool_consecutive < 2:
        ap.error("--cool-consecutive must be >= 2 (never end on one sample)")

    b = sensors.get_backend(args.backend)
    is_mock = b.name == "mock"
    if is_mock and args.workers > 1:
        ap.error("--workers > 1 is meaningless with the mock backend")
    print(f"[exp1] backend={b.name} workers={args.workers}")
    signal.signal(signal.SIGTERM, _sigterm)

    idle_stats = measure_idle_baseline(b, args.idle_window_s)
    if args.idle_baseline is not None:
        idle, idle_source = args.idle_baseline, "recorded (--idle-baseline)"
    else:
        idle = idle_stats["baseline_c"] if idle_stats else None
        idle_source = f"median of {args.idle_window_s:g} s idle window"
    print(f"[exp1] idle baseline: {idle} C ({idle_source})")
    if idle_stats:
        print(f"[exp1] idle window: {idle_stats['min_c']}-{idle_stats['max_c']} C "
              f"over {idle_stats['n']} readings, stable={idle_stats['stable']}")
        if not idle_stats["stable"]:
            print("[exp1] WARNING: idle not stable; cooling baseline is "
                  "uncertain. Consider waiting and rerunning.")

    log = runner.TelemetryLogger(b, hz=args.hz)
    hot = wl.Workload("HOT", args.n, duty=1.0, target_ms=200.0)
    blas = _blas_libs()

    pool = None
    if args.workers > 1:
        print(f"[exp1] starting {args.workers} HOT worker processes ...")
        pool = HeatWorkers(args.workers, args.n, 200.0)

    # ---------------- 1a: heating step response ----------------
    print(f"[exp1] 1a heating: sustained load for {args.minutes} min ...")
    abort_reason = None
    worker_report = None
    try:
        log.start()
        t_start = time.monotonic()
        snap_start = b.read()
        cpu_before, pcpu_before = _cpu_times(), time.process_time()
        if is_mock:
            b.set_power(1.0)
        if pool:
            pool.start()
        heat_end = t_start + args.minutes * 60
        n_jobs = 0
        while time.monotonic() < heat_end:
            if args.max_temp is not None:
                cur = _latest_temp(log)
                if cur is not None and cur >= args.max_temp:
                    abort_reason = f"max_temp: {cur:.1f} C >= {args.max_temp} C"
                    break
            if pool:
                if pool.dead():
                    abort_reason = f"worker(s) exited early: {pool.dead()}"
                    break
                time.sleep(0.25)
            elif is_mock:
                time.sleep(0.2)
                n_jobs += 1
            else:
                hot.run_once()
                n_jobs += 1
    finally:
        if pool:   # always: normal end, safety stop, Ctrl+C, SIGTERM
            worker_report = pool.stop()
    t_heat_done = time.monotonic()
    cpu_after, pcpu_after = _cpu_times(), time.process_time()
    snap_end = b.read()
    if pool:
        n_jobs = sum(w["jobs"] or 0 for w in worker_report)
    if abort_reason:
        print(f"[exp1] HEATING STOPPED EARLY: {abort_reason}")

    # ---------------- 1b: cooling / recovery -------------------
    print(f"[exp1] 1b cooling: idle for up to {args.cool_minutes} min ...")
    if is_mock:
        b.set_power(0.0)
    t_cool_start = time.monotonic()
    cool_end = t_cool_start + args.cool_minutes * 60
    term = CoolingTerminator(idle, needed=args.cool_consecutive)
    cool_end_reason = "cool_minutes_elapsed"
    while time.monotonic() < cool_end:
        time.sleep(0.5)
        if term.update(b.read().get("temp_c")):
            cool_end_reason = "baseline_reached"
            print(f"  [exp1] {term.needed} consecutive readings within "
                  f"{term.tol} C of idle baseline {idle} C; "
                  "stopping cooling phase early")
            break
    cool_wall = time.monotonic() - t_cool_start
    tele = log.stop()

    # ---------------- analysis ----------------
    t = [s["t_wall"] for s in tele if s.get("temp_c") is not None]
    T = [s["temp_c"] for s in tele if s.get("temp_c") is not None]
    F = [s.get("freq_mhz") for s in tele if s.get("temp_c") is not None]
    F = [np.nan if f is None else f for f in F]

    heat_idx = [i for i, tt in enumerate(t) if tt <= t_heat_done]
    cool_idx = [i for i, tt in enumerate(t) if tt > t_heat_done]

    fit_h = fit_exponential([t[i] for i in heat_idx],
                            [T[i] for i in heat_idx], cooling=False) \
        if len(heat_idx) > 10 else None
    fit_c = fit_exponential([t[i] for i in cool_idx],
                            [T[i] for i in cool_idx], cooling=True) \
        if len(cool_idx) > 10 else None
    knee = find_knee(T, F)
    util = _cpu_util(cpu_before, cpu_after)
    status, _ = p4_verdict(knee, abort_reason, util, is_mock)

    meta = {
        "experiment": "exp1_thermal",
        "backend": b.name,
        "mock": is_mock,
        "idle_temp_c": idle,
        "idle_baseline_source": idle_source,
        "idle_window": idle_stats,
        "cool_end_reason": cool_end_reason,
        "cool_wall_s": round(cool_wall, 3),
        "cool_consecutive": args.cool_consecutive,
        "p4_status": status,
        "heat_minutes": args.minutes,
        "jobs_executed": n_jobs,
        "samples": len(tele),
        "t_peak_c": max(T) if T else None,
        "fit_heating": fit_h,
        "fit_cooling": fit_c,
        "knee": knee,
        # --- load characterization (added with --workers) ---
        "workers": args.workers,
        "max_temp_c": args.max_temp,
        "abort_reason": abort_reason,
        "heat_wall_s": round(t_heat_done - t_start, 3),
        "parent_cpu_s": round(pcpu_after - pcpu_before, 3),
        "worker_report": worker_report,
        "cpu_util_heating": util,
        "blas_libs": blas,
        "heat_snapshot_start": snap_start,
        "heat_snapshot_end": snap_end,
        "heat_summary": _heat_summary(tele, t_start, t_heat_done),
    }
    outcome, outcome_lines = exp1_outcome(meta)
    meta["exp1_outcome"] = outcome
    runner.save_run(args.outdir, "exp1", meta, [], tele)

    # ---------------- report ----------------
    print("\n" + "=" * 62)
    print("EXPERIMENT 1 RESULT")
    print("=" * 62)
    print(f"peak temperature   : {meta['t_peak_c']:.1f} C" if T else "no temps")
    print(f"workers            : {args.workers}")
    if util:
        print(f"cores busy (heat)  : {util['avg_cores_busy']:.2f} avg, "
              f"per-core {util['per_core_busy']}")
    print(f"BLAS libraries     : {blas}")
    if abort_reason:
        print(f"HEATING STOPPED EARLY: {abort_reason}")
    if fit_h:
        print(f"tau_heat           : {fit_h['tau']:.1f} s  (R^2={fit_h['r2']:.3f})")
        print(f"T_steady_state est : {fit_h['y_inf']:.1f} C")
    if fit_c:
        print(f"tau_cool           : {fit_c['tau']:.1f} s  (R^2={fit_c['r2']:.3f})")
        if fit_c.get("possible_second_order"):
            print("  NOTE: structured residuals -> possibly second-order "
                  "thermal system.")
            print("        If confirmed, a history term carries information "
                  "beyond instantaneous T.")
    if knee and knee.get("knee_c") is not None:
        print(f"throttle knee      : {knee['knee_c']:.1f} C")
        print(f"throttle depth     : {knee['depth']*100:.1f}% "
              f"(f {knee['f_max']:.0f} -> {knee['f_min']:.0f} MHz)")
    else:
        print("throttle knee      : NOT OBSERVED")

    print(f"cooling ended      : {cool_end_reason} after {cool_wall:.0f} s")

    print("\n--- GATE CHECK (P3 / P4) ---")
    if fit_c:
        tau = fit_c["tau"]
        print(f"tau_cool = {tau:.0f}s")
        print("P3 requires tau comparable to the REORDERING HORIZON "
              "(k * service_time),")
        print("not to a single request. With 200 ms jobs:")
        for k in (10, 50, 200, 1000):
            horizon = k * 0.2
            ratio = tau / horizon
            verdict = ("usable" if 0.2 <= ratio <= 20
                       else "too slow" if ratio > 20 else "too fast")
            print(f"  k={k:5d} -> horizon {horizon:7.1f}s  tau/horizon="
                  f"{ratio:8.2f}  [{verdict}]")
        print("Pick k for Experiment 3 so tau/horizon lands near 1.")
    print()
    for line in outcome_lines:
        print(line)
    print("=" * 62)
    print(f"\nwrote {args.outdir}/exp1.*")


if __name__ == "__main__":
    main()
