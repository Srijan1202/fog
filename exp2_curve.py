#!/usr/bin/env python3
"""
EXPERIMENT 2 - service time vs temperature, s(T).

THE decisive cheap measurement. If s(T) is flat across the accessible
temperature range, thermal state cannot affect latency, and TDAS is dead
regardless of anything else. This costs a couple of hours.

Method: heat the device in stages; at each stage immediately run a fixed
probe batch and record latency against the temperature at that moment.

    python3 exp2_curve.py --stages 8 --outdir results
    python3 exp2_curve.py --backend mock --stages 6 --outdir results_mock
"""

from __future__ import annotations

import argparse
import time

import numpy as np

import sensors
import runner
import workload as wl


def spearman(x, y):
    """Rank correlation without scipy."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean()
    ry -= ry.mean()
    d = np.sqrt((rx @ rx) * (ry @ ry))
    return float(rx @ ry / d) if d > 0 else float("nan")


def bootstrap_slope(x, y, iters=2000, seed=0):
    """Bootstrap CI for OLS slope of y on x (ms per degree C)."""
    rng = np.random.default_rng(seed)
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = len(x)
    slopes = []
    for _ in range(iters):
        idx = rng.integers(0, n, n)
        xs, ys = x[idx], y[idx]
        if xs.std() == 0:
            continue
        slopes.append(np.polyfit(xs, ys, 1)[0])
    if not slopes:
        return None
    s = np.array(slopes)
    return {"slope_ms_per_C": float(np.median(s)),
            "ci_lo": float(np.percentile(s, 2.5)),
            "ci_hi": float(np.percentile(s, 97.5)),
            "excludes_zero": bool(np.percentile(s, 2.5) > 0
                                  or np.percentile(s, 97.5) < 0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None)
    ap.add_argument("--stages", type=int, default=8,
                    help="number of temperature stages to probe")
    ap.add_argument("--stage-heat-s", type=float, default=90.0,
                    help="heating time between probes")
    ap.add_argument("--probes", type=int, default=25,
                    help="probe jobs per stage")
    ap.add_argument("--n", type=int, default=512)
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--hz", type=float, default=5.0)
    args = ap.parse_args()

    b = sensors.get_backend(args.backend)
    is_mock = b.name == "mock"
    print(f"[exp2] backend={b.name}")

    probe = wl.Workload("PROBE", args.n, duty=1.0, target_ms=150.0)
    heater = wl.Workload("HEAT", args.n, duty=1.0, target_ms=200.0)

    log = runner.TelemetryLogger(b, hz=args.hz)
    log.start()
    if is_mock:
        b.set_power(1.4)   # enough to cross the mock knee

    rows = []
    for stage in range(args.stages):
        # heat
        end = time.monotonic() + args.stage_heat_s
        while time.monotonic() < end:
            if is_mock:
                time.sleep(0.2)
            else:
                heater.run_once()
        # probe immediately
        for i in range(args.probes):
            snap_before = b.read()
            lat = 150.0 if is_mock else probe.run_once()
            if is_mock:
                # simulate throttled service time on the mock
                r = b.read()
                lat = 150.0 * (r["freq_mhz"] and
                               (1500.0 / r["freq_mhz"]) or 1.0)
                lat += np.random.normal(0, 1.5)
                time.sleep(0.02)
            snap_after = b.read()
            rows.append({
                "stage": stage,
                "probe": i,
                "temp_c": snap_before.get("temp_c"),
                "freq_mhz": snap_before.get("freq_mhz"),
                "throttled": snap_before.get("throttled"),
                "latency_ms": lat,
                "temp_after_c": snap_after.get("temp_c"),
            })
        t_now = b.read().get("temp_c")
        print(f"  stage {stage}: T={t_now:.1f}C  "
              f"median_probe={np.median([r['latency_ms'] for r in rows if r['stage']==stage]):.1f}ms")

    tele = log.stop()

    # ---------------- analysis ----------------
    valid = [r for r in rows if r["temp_c"] is not None]
    T = [r["temp_c"] for r in valid]
    L = [r["latency_ms"] for r in valid]
    rho = spearman(T, L)
    slope = bootstrap_slope(T, L)

    # split at the observed knee if frequency dropped
    F = [r["freq_mhz"] for r in valid if r["freq_mhz"] is not None]
    knee_c = None
    if F:
        fmax = max(F)
        thr = [r for r in valid if r["freq_mhz"] is not None
               and r["freq_mhz"] < fmax * 0.98]
        if thr:
            knee_c = min(r["temp_c"] for r in thr)

    lo = [r["latency_ms"] for r in valid
          if knee_c is None or r["temp_c"] < knee_c]
    hi = [r["latency_ms"] for r in valid
          if knee_c is not None and r["temp_c"] >= knee_c]

    meta = {
        "experiment": "exp2_curve",
        "backend": b.name, "mock": is_mock,
        "n_probes": len(valid),
        "temp_range_c": [min(T), max(T)] if T else None,
        "spearman_temp_latency": rho,
        "slope": slope,
        "knee_c": knee_c,
        "median_latency_below_knee_ms": float(np.median(lo)) if lo else None,
        "median_latency_above_knee_ms": float(np.median(hi)) if hi else None,
    }
    runner.save_run(args.outdir, "exp2", meta, rows, tele)

    print("\n" + "=" * 62)
    print("EXPERIMENT 2 RESULT - s(T)")
    print("=" * 62)
    print(f"temperature range probed : {min(T):.1f} - {max(T):.1f} C")
    print(f"Spearman rho(T, latency) : {rho:+.3f}")
    if slope:
        print(f"slope                    : {slope['slope_ms_per_C']:+.3f} ms/C "
              f"[95% CI {slope['ci_lo']:+.3f}, {slope['ci_hi']:+.3f}]")
        print(f"CI excludes zero         : {slope['excludes_zero']}")
    if knee_c is not None:
        print(f"knee                     : {knee_c:.1f} C")
        print(f"median latency below     : {meta['median_latency_below_knee_ms']:.1f} ms")
        print(f"median latency above     : {meta['median_latency_above_knee_ms']:.1f} ms")
        jump = (meta["median_latency_above_knee_ms"] /
                meta["median_latency_below_knee_ms"] - 1) * 100
        print(f"jump across knee         : {jump:+.1f}%")

    print("\n--- GATE CHECK (P1) ---")
    dead = False
    if slope and not slope["excludes_zero"]:
        print("  KILL: slope CI includes zero - latency does not depend on")
        print("        temperature over the probed range. P1 FAILS.")
        dead = True
    if knee_c is None:
        print("  WARNING: no frequency drop observed; if the range probed")
        print("           never reached the knee, extend --stages.")
    if abs(rho) < 0.2 and not dead:
        print("  WARNING: weak rank correlation; effect may be too small.")
    if not dead:
        print("  P1 holds so far. Proceed to Experiment 3.")
    print("=" * 62)
    print(f"\nwrote {args.outdir}/exp2.*")


if __name__ == "__main__":
    main()
