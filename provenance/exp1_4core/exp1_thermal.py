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
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("--workers must be >= 1")

    b = sensors.get_backend(args.backend)
    is_mock = b.name == "mock"
    if is_mock and args.workers > 1:
        ap.error("--workers > 1 is meaningless with the mock backend")
    print(f"[exp1] backend={b.name} workers={args.workers}")
    signal.signal(signal.SIGTERM, _sigterm)

    idle = b.read().get("temp_c")
    print(f"[exp1] idle temperature: {idle} C")

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
    cool_end = time.monotonic() + args.cool_minutes * 60
    while time.monotonic() < cool_end:
        time.sleep(0.5)
        cur = b.read().get("temp_c")
        if idle is not None and cur is not None and cur <= idle + 1.0:
            print("  [exp1] returned to idle+1C, stopping cooling phase early")
            break
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

    meta = {
        "experiment": "exp1_thermal",
        "backend": b.name,
        "mock": is_mock,
        "idle_temp_c": idle,
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
        "cpu_util_heating": _cpu_util(cpu_before, cpu_after),
        "blas_libs": blas,
        "heat_snapshot_start": snap_start,
        "heat_snapshot_end": snap_end,
        "heat_summary": _heat_summary(tele, t_start, t_heat_done),
    }
    runner.save_run(args.outdir, "exp1", meta, [], tele)

    # ---------------- report ----------------
    print("\n" + "=" * 62)
    print("EXPERIMENT 1 RESULT")
    print("=" * 62)
    print(f"peak temperature   : {meta['t_peak_c']:.1f} C" if T else "no temps")
    util = meta["cpu_util_heating"]
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

    print("\n--- GATE CHECK (P3 / P4) ---")
    kill = []
    if not knee or knee.get("knee_c") is None:
        kill.append("P4 FAIL: no throttle knee reached under sustained load")
    elif knee["depth"] < 0.05:
        kill.append(f"P1/P4 WEAK: throttle depth only {knee['depth']*100:.1f}%")
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
    n_cores = len(util["per_core_busy"]) if util else None
    partial = (not is_mock) and util and util["avg_cores_busy"] < n_cores - 0.5
    if kill:
        print("\nKILL SIGNALS:")
        for x in kill:
            print(f"  - {x}")
        if partial:
            print(f"\nWARNING: heating loaded only {util['avg_cores_busy']:.2f} "
                  f"of {n_cores} cores. A missing knee is NOT a P4 verdict for")
            print("         sustained max load. Rerun with --workers "
                  f"{n_cores} into a separate --outdir.")
        else:
            print("\nIf P4 failed, TDAS is dead on this hardware. Stop here.")
    else:
        print("\nNo kill signal from Experiment 1. Proceed to Experiment 2.")
    print("=" * 62)
    print(f"\nwrote {args.outdir}/exp1.*")


if __name__ == "__main__":
    main()
