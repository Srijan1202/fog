#!/usr/bin/env python3
"""
EXPERIMENT 3 - identical-multiset ordering test. THE decisive experiment.

Every ordering executes the same multiset of FIXED-WORK jobs (Amendment A2):
the same jobs, the same planned compute units and the same planned sleep per
job. Achieved totals are recorded per run. Energy is NOT measured; equal
planned work does not imply equal energy. HOT and COOL cold latencies are
matched by an ACCEPTED workload validation (validate_workload.py), so
job-length/queueing explanations are excluded within its measured tolerance.

On real hardware this script refuses to run unless BOTH hold:
  - a valid Experiment 1 knee (G1 PASS)            --exp1-meta
  - an ACCEPTED workload validation for --parallel --workload-validation

Runs are INTERLEAVED and COUNTERBALANCED (not blocked by condition), because
ambient drifts over a multi-hour session and a blocked design confounds
condition with time-of-day.

    python3 exp3_ordering.py --reps 10 --k 60 --parallel 4 \
        --workload-validation results/workload_validation/<run>/report.json \
        --exp1-meta results/<exp1>/exp1.meta.json --outdir results
    python3 exp3_ordering.py --backend mock --reps 6 --k 40 --outdir results_mock

Outputs: results/exp3.{meta.json,jobs.jsonl,telemetry.jsonl}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time

import numpy as np

import analyze
import sensors
import runner
import workload as wl


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


def run_one_sequence(b, seq, knee_c, hz, is_mock, hot_power, cool_power,
                     pool=None):
    """Execute one ordering; return (jobs, telemetry, summary)."""
    log = runner.TelemetryLogger(b, hz=hz)
    log.start()
    t0 = time.monotonic()
    jobs = []
    for idx, job in enumerate(seq):
        before = b.read()
        work = {}
        if is_mock:
            b.set_power(hot_power if job.name == "HOT" else cool_power)
            r = b.read()
            base = 200.0
            lat = base * (1500.0 / r["freq_mhz"]) if r.get("freq_mhz") else base
            lat += np.random.normal(0, 2.0)
            time.sleep(0.02)
        else:
            rec = pool.run_job(job)
            lat = rec["latency_ms"]
            work = {k: rec[k] for k in (
                "units_planned", "units_done", "cpu_ms", "compute_ms",
                "sleep_req_ms", "sleep_actual_ms", "realized_duty",
                "overhead_ms", "imbalance", "skew_ms")}
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
            **work,
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
        "planned": wl.planned_totals(seq),
        "achieved": None if is_mock else {
            "units_done": sum(j["units_done"] for j in jobs),
            "cpu_ms": sum(j["cpu_ms"] for j in jobs),
            "sleep_actual_ms": sum(j["sleep_actual_ms"] for j in jobs)},
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
                    help="mock backend only: time-above-knee threshold in C. "
                         "Real runs take it from a valid Experiment 1")
    ap.add_argument("--exp1-meta", default=None,
                    help="Experiment 1 metadata whose knee sets the threshold "
                         "(default: <outdir>/exp1.meta.json)")
    ap.add_argument("--cool-target", type=float, default=None,
                    help="cool down to this temp between runs")
    ap.add_argument("--orderings", default="hot_first,cool_first,alternating,random")
    ap.add_argument("--parallel", type=int, choices=(1, 4), default=None,
                    help="fixed-work workers per job; must be an ACCEPTED "
                         "validated configuration (required on real hardware)")
    ap.add_argument("--workload-validation", default=None,
                    help="report.json from validate_workload.py")
    ap.add_argument("--workload-config", choices=("4b", "4c"), default=None,
                    help="4b = configuration P4b (with --parallel 4), validated by "
                         "validate_workload.py --config 4b")
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

    # ---- knee: only from a valid Experiment 1 (checked before any load) ----
    th = resolve_threshold(
        args.exp1_meta or os.path.join(args.outdir, "exp1.meta.json"),
        os.path.join(args.outdir, "exp2.meta.json"), is_mock, args.knee)
    knee_c = th["knee_c"]
    print(f"[exp3] G1 {th['g1_state']}: {th['g1_reason']}")
    if th["exp2_knee_support"]:
        print(f"[exp3] {th['exp2_knee_support']}; not used as the threshold")
    if knee_c is not None:
        print(f"[exp3] knee = {knee_c:.1f} C ({th['knee_source']})")
    elif not is_mock:
        print("[exp3] REFUSING TO RUN: no valid Experiment 1 knee.")
        print("       time_above_knee (the G4 mediator) would be unavailable, so")
        print("       this run could not test TDAS. Experiment 2's knee is")
        print("       supporting evidence only and is not substituted.")
        return 2
    else:
        print("[exp3] mock run without a knee: time_above_knee unavailable "
              "(pipeline check only)")

    # ---- fixed-work jobs: only from an ACCEPTED workload validation ----
    wk, refusal = resolve_workload(args.workload_validation, args.parallel, is_mock,
                                   args.workload_config)
    if refusal:
        print(f"[exp3] REFUSING TO RUN: {refusal}")
        print("       Run validate_workload.py (with approval) and pass its")
        print("       report.json via --workload-validation.")
        return 2
    if wk.get("telemetry", "full") != "full":
        # P4b jobs must run under P4b's telemetry scheme (sysfs-only during jobs) and
        # Experiment 3 must record the ARM clock during load (A3); that scheme is
        # specified in Amendment A4 and not implemented here yet.
        print(f"[exp3] REFUSING TO RUN: configuration {wk['config']} needs the "
              f"'{wk['telemetry']}' telemetry scheme of Amendment A4, which "
              "exp3_ordering.py does not implement yet (no load started).")
        return 2
    hot, cool = wk["hot"], wk["cool"]
    vm = wk["cold_latency_match"]
    if wk["accepted"]:
        print(f"[exp3] workload: {vm['source']}  u_h={hot.units_per_shard} "
              f"u_c={cool.units_per_shard} S={cool.sleep_per_shard_ms:.2f} ms "
              f"gap {vm['relative_gap']:+.4f} ({vm['gap_class']})")

    n_hot = int(round(args.k * args.hot_frac))
    multiset = wl.make_multiset(n_hot, args.k - n_hot, hot, cool)
    planned = wl.planned_totals(multiset)

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
    pool = None if is_mock else wl.FixedWorkPool(args.parallel)
    try:
        for i, (rep, mode) in enumerate(schedule):
            if cool_target is not None:
                cd = runner.cool_to(b, cool_target, verbose=False)
                if not cd["ok"]:
                    print(f"  [warn] cooldown incomplete before run {i} "
                          f"({cd.get('reason')}, at {cd.get('final_temp_c')}C)")
            seq = wl.order_sequence(multiset, mode, rng)
            jobs, tele, summ = run_one_sequence(
                b, seq, knee_c, args.hz, is_mock,
                hot_power=1.45, cool_power=0.55, pool=pool)
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
    finally:
        if pool is not None:
            pool.stop()

    meta = {
        "experiment": "exp3_ordering",
        "backend": b.name, "mock": is_mock,
        "knee_c": knee_c,
        "threshold": th,
        "time_above_knee_available": knee_c is not None,
        "k_jobs": args.k, "hot_frac": args.hot_frac,
        "reps": args.reps, "orderings": modes,
        "cold_latency_match": vm,
        "workload": {k: wk.get(k) for k in ("accepted", "mock", "frozen",
                                             "validation_run", "report")},
        "parallel": args.parallel,
        "multiset_planned": planned,
        "planned_identical_across_runs": all(r["planned"] == planned
                                             for r in results),
        "energy_measured": False,
        "idle_temp_c": idle, "cool_target_c": cool_target,
        "runs": results,
    }
    runner.save_run(args.outdir, "exp3", meta, all_jobs, all_tele)
    print(f"\nwrote {args.outdir}/exp3.*")
    print("Now run:  python3 analyze.py --outdir " + args.outdir)


if __name__ == "__main__":
    raise SystemExit(main())
