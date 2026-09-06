#!/usr/bin/env python3
"""
EXPERIMENT 3 - identical-multiset ordering test. THE decisive experiment.

Every ordering executes exactly the same multiset of jobs, so total work and
total energy are identical BY CONSTRUCTION. HOT and COOL are calibrated to
the same cold latency, so job-length/queueing explanations are ruled out too.
The only remaining channel from ordering to tail latency is thermal.

Runs are INTERLEAVED and COUNTERBALANCED (not blocked by condition), because
ambient drifts over a multi-hour session and a blocked design confounds
condition with time-of-day.

    python3 exp3_ordering.py --reps 10 --k 60 --outdir results
    python3 exp3_ordering.py --backend mock --reps 6 --k 40 --outdir results_mock

Outputs: results/exp3.{meta.json,jobs.jsonl,telemetry.jsonl}
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


def run_one_sequence(b, seq, knee_c, hz, is_mock, hot_power, cool_power):
    """Execute one ordering; return (jobs, telemetry, summary)."""
    log = runner.TelemetryLogger(b, hz=hz)
    log.start()
    t0 = time.monotonic()
    jobs = []
    for idx, job in enumerate(seq):
        before = b.read()
        if is_mock:
            b.set_power(hot_power if job.name == "HOT" else cool_power)
            r = b.read()
            base = 200.0
            lat = base * (1500.0 / r["freq_mhz"]) if r.get("freq_mhz") else base
            lat += np.random.normal(0, 2.0)
            time.sleep(0.02)
        else:
            lat = job.run_once()
        after = b.read()
        jobs.append({
            "idx": idx,
            "class": job.name,
            "latency_ms": lat,
            "t_start": before["t_wall"] - t0,
            "temp_before_c": before.get("temp_c"),
            "temp_after_c": after.get("temp_c"),
            "freq_mhz": after.get("freq_mhz"),
            "throttled": after.get("throttled"),
        })
    wall = time.monotonic() - t0
    tele = log.stop()
    if is_mock:
        b.set_power(0.0)

    temps = [s["temp_c"] for s in tele if s.get("temp_c") is not None]
    freqs = [s.get("freq_mhz") for s in tele if s.get("temp_c") is not None]
    dt = 1.0 / hz

    time_above = None
    if knee_c is not None and temps:
        time_above = float(sum(1 for t in temps if t >= knee_c) * dt)

    fmax = max([f for f in freqs if f is not None], default=None)
    time_throttled = None
    if fmax:
        time_throttled = float(
            sum(1 for f in freqs if f is not None and f < fmax * 0.98) * dt)

    lats = [j["latency_ms"] for j in jobs]
    summary = {
        "wall_s": wall,
        "n_jobs": len(jobs),
        "mean_ms": float(np.mean(lats)),
        "p50_ms": float(np.percentile(lats, 50)),
        "p95_ms": float(np.percentile(lats, 95)),
        "p99_ms": float(np.percentile(lats, 99)),
        "throughput_jobs_s": len(jobs) / wall if wall > 0 else None,
        "peak_temp_c": max(temps) if temps else None,
        "mean_temp_c": float(np.mean(temps)) if temps else None,
        "time_above_knee_s": time_above,
        "time_throttled_s": time_throttled,
    }
    return jobs, tele, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None)
    ap.add_argument("--reps", type=int, default=10,
                    help="repetitions per ordering")
    ap.add_argument("--k", type=int, default=60,
                    help="jobs per sequence (the reordering horizon)")
    ap.add_argument("--hot-frac", type=float, default=0.5)
    ap.add_argument("--knee", type=float, default=None,
                    help="throttle knee in C (from Experiment 1/2)")
    ap.add_argument("--cool-target", type=float, default=None,
                    help="cool down to this temp between runs")
    ap.add_argument("--orderings", default="hot_first,cool_first,alternating,random")
    ap.add_argument("--n", type=int, default=512)
    ap.add_argument("--target-ms", type=float, default=200.0)
    ap.add_argument("--cool-duty", type=float, default=0.35)
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mock-tau", type=float, default=45.0,
                    help="mock only: thermal time constant (s)")
    ap.add_argument("--mock-knee", type=float, default=80.0,
                    help="mock only: throttle knee (C)")
    args = ap.parse_args()

    b = sensors.get_backend(args.backend, tau=args.mock_tau,
                            knee=args.mock_knee)
    is_mock = b.name == "mock"
    rng = np.random.default_rng(args.seed)
    modes = [m.strip() for m in args.orderings.split(",")]

    print(f"[exp3] backend={b.name} orderings={modes} reps={args.reps} k={args.k}")

    # ---- knee ----
    knee_c = args.knee
    if knee_c is None:
        p = os.path.join(args.outdir, "exp1.meta.json")
        if os.path.exists(p):
            with open(p) as f:
                m = json.load(f)
            knee_c = (m.get("knee") or {}).get("knee_c")
        if knee_c is None:
            p = os.path.join(args.outdir, "exp2.meta.json")
            if os.path.exists(p):
                with open(p) as f:
                    knee_c = json.load(f).get("knee_c")
    if knee_c is None:
        print("[exp3] WARNING: no knee known. time_above_knee will be null and")
        print("       the mediation test cannot run. Supply --knee.")
    else:
        print(f"[exp3] knee = {knee_c:.1f} C")

    # ---- workloads with matched cold latency ----
    hot, cool = wl.calibrate(n_hot=args.n, target_ms=args.target_ms,
                             cool_duty=args.cool_duty)
    if not is_mock:
        print("[exp3] verifying HOT/COOL cold-latency match ...")
        vm = wl.verify_match(hot, cool, reps=12)
        print(f"  hot={vm['hot_median_ms']:.1f}ms cool={vm['cool_median_ms']:.1f}ms "
              f"gap={vm['relative_gap']*100:.1f}% matched={vm['matched']}")
        if not vm["matched"]:
            print("  ACTION: cold latencies differ >5%. Re-tune --target-ms /")
            print("          --cool-duty before trusting Experiment 3, or")
            print("          reviewer attack #7 is live.")
    else:
        vm = {"matched": True, "note": "mock"}

    n_hot = int(round(args.k * args.hot_frac))
    multiset = wl.make_multiset(n_hot, args.k - n_hot, hot, cool)

    idle = b.read().get("temp_c")
    cool_target = args.cool_target if args.cool_target is not None else \
        (idle + 3.0 if idle is not None else None)
    print(f"[exp3] idle={idle} cooldown target={cool_target}")

    # ---- counterbalanced schedule ----
    schedule = []
    for r in range(args.reps):
        block = list(modes)
        if r % 2 == 1:
            block = block[::-1]           # reverse alternate blocks
        rng.shuffle(block)                 # plus randomization within block
        for m in block:
            schedule.append((r, m))

    all_jobs, all_tele, results = [], [], []
    t_session = time.monotonic()
    for i, (rep, mode) in enumerate(schedule):
        if cool_target is not None:
            cd = runner.cool_to(b, cool_target, verbose=False)
            if not cd["ok"]:
                print(f"  [warn] cooldown incomplete before run {i} "
                      f"({cd.get('reason')}, at {cd.get('final_temp_c')}C)")
        seq = wl.order_sequence(multiset, mode, rng)
        jobs, tele, summ = run_one_sequence(
            b, seq, knee_c, args.hz, is_mock,
            hot_power=1.45, cool_power=0.55)
        summ.update({"rep": rep, "mode": mode, "run_index": i,
                     "start_temp_c": cd.get("final_temp_c") if cool_target else None,
                     "session_t": time.monotonic() - t_session})
        results.append(summ)
        for j in jobs:
            j.update({"rep": rep, "mode": mode, "run_index": i})
        all_jobs.extend(jobs)
        for s in tele:
            s.update({"run_index": i, "mode": mode})
        all_tele.extend(tele)
        print(f"  run {i+1}/{len(schedule)} rep={rep} {mode:12s} "
              f"p99={summ['p99_ms']:7.1f}ms  peak={summ['peak_temp_c']}C  "
              f"above_knee={summ['time_above_knee_s']}s")

    meta = {
        "experiment": "exp3_ordering",
        "backend": b.name, "mock": is_mock,
        "knee_c": knee_c,
        "k_jobs": args.k, "hot_frac": args.hot_frac,
        "reps": args.reps, "orderings": modes,
        "cold_latency_match": vm,
        "idle_temp_c": idle, "cool_target_c": cool_target,
        "runs": results,
    }
    runner.save_run(args.outdir, "exp3", meta, all_jobs, all_tele)
    print(f"\nwrote {args.outdir}/exp3.*")
    print("Now run:  python3 analyze.py --outdir " + args.outdir)


if __name__ == "__main__":
    main()
