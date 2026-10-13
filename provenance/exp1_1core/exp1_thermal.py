#!/usr/bin/env python3
"""
EXPERIMENT 1 - thermal response, recovery timescale, throttle knee.

Answers P3 (is tau comparable to the reordering horizon?) and P4 (does the
device reach the knee under realistic sustained load?).

    python3 exp1_thermal.py --minutes 15 --outdir results
    python3 exp1_thermal.py --backend mock --minutes 2 --outdir results_mock

Outputs: results/exp1.{meta.json,telemetry.jsonl}
"""

from __future__ import annotations

import argparse
import json
import os
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
    args = ap.parse_args()

    b = sensors.get_backend(args.backend)
    is_mock = b.name == "mock"
    print(f"[exp1] backend={b.name}")

    idle = b.read().get("temp_c")
    print(f"[exp1] idle temperature: {idle} C")

    log = runner.TelemetryLogger(b, hz=args.hz)
    hot = wl.Workload("HOT", args.n, duty=1.0, target_ms=200.0)

    # ---------------- 1a: heating step response ----------------
    print(f"[exp1] 1a heating: sustained load for {args.minutes} min ...")
    log.start()
    t_start = time.monotonic()
    if is_mock:
        b.set_power(1.0)
    heat_end = t_start + args.minutes * 60
    n_jobs = 0
    while time.monotonic() < heat_end:
        if is_mock:
            time.sleep(0.2)
        else:
            hot.run_once()
        n_jobs += 1
    t_heat_done = time.monotonic()

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
    }
    runner.save_run(args.outdir, "exp1", meta, [], tele)

    # ---------------- report ----------------
    print("\n" + "=" * 62)
    print("EXPERIMENT 1 RESULT")
    print("=" * 62)
    print(f"peak temperature   : {meta['t_peak_c']:.1f} C" if T else "no temps")
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
    if kill:
        print("\nKILL SIGNALS:")
        for x in kill:
            print(f"  - {x}")
        print("\nIf P4 failed, TDAS is dead on this hardware. Stop here.")
    else:
        print("\nNo kill signal from Experiment 1. Proceed to Experiment 2.")
    print("=" * 62)
    print(f"\nwrote {args.outdir}/exp1.*")


if __name__ == "__main__":
    main()
