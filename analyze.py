#!/usr/bin/env python3
"""
analyze.py - statistics and PRE-REGISTERED kill gates for the TDAS pilot.

The gates below are stated in terms of measurable effects and confidence
intervals, NOT invented percentage thresholds. The one structural rule:

    An ordering effect on tail latency counts as evidence for TDAS ONLY if
    the mediating variable (time above the throttle knee) also moves.
    Ordering -> thermal trajectory -> throttle exposure -> service time ->
    tail latency. If the middle links are flat, a latency difference is
    noise or confounding, not thermal causation.

    python3 analyze.py --outdir results
"""

from __future__ import annotations

import argparse
import json
import os
from itertools import combinations

import numpy as np


def load(outdir, name):
    p = os.path.join(outdir, f"{name}.meta.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def boot_diff(a, b, iters=10000, seed=0):
    """Bootstrap CI for median(a) - median(b) (unpaired, robust)."""
    rng = np.random.default_rng(seed)
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return None
    d = []
    for _ in range(iters):
        d.append(np.median(rng.choice(a, len(a), replace=True)) -
                 np.median(rng.choice(b, len(b), replace=True)))
    d = np.array(d)
    lo, hi = np.percentile(d, [2.5, 97.5])
    return {
        "diff": float(np.median(a) - np.median(b)),
        "ci_lo": float(lo), "ci_hi": float(hi),
        "excludes_zero": bool(lo > 0 or hi < 0),
        "rel_pct": float((np.median(a) - np.median(b)) / np.median(b) * 100)
        if np.median(b) else None,
    }


def cliffs_delta(a, b):
    """Non-parametric effect size in [-1, 1]. |d|<0.147 negligible."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    gt = sum((x > y) for x in a for y in b)
    lt = sum((x < y) for x in a for y in b)
    n = len(a) * len(b)
    return float((gt - lt) / n) if n else float("nan")


def by_mode(runs, field):
    out = {}
    for r in runs:
        if r.get(field) is not None:
            out.setdefault(r["mode"], []).append(r[field])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="results")
    args = ap.parse_args()

    e1 = load(args.outdir, "exp1")
    e2 = load(args.outdir, "exp2")
    e3 = load(args.outdir, "exp3")

    print("=" * 70)
    print("TDAS PILOT - ANALYSIS AND PRE-REGISTERED KILL GATES")
    print("=" * 70)

    gates = {}
    mock = any(x and x.get("mock") for x in (e1, e2, e3))
    if mock:
        print("\n*** MOCK DATA - SIMULATED. NOT A RESULT. PIPELINE CHECK ONLY. ***")

    # ---------------- GATE 1: knee exists (P4) ----------------
    print("\n--- GATE 1 (P4): is there a throttle knee? ---")
    knee = None
    depth = None
    if e1 and e1.get("knee"):
        knee = e1["knee"].get("knee_c")
        depth = e1["knee"].get("depth")
    if knee is None and e2:
        knee = e2.get("knee_c")
    if knee is None:
        print("  FAIL: no throttle knee observed under sustained load.")
        gates["G1_knee"] = False
    else:
        print(f"  knee = {knee:.1f} C" + (f", depth = {depth*100:.1f}%"
                                          if depth else ""))
        gates["G1_knee"] = True
        if depth is not None and depth < 0.05:
            print("  WARNING: throttle depth <5% - effect likely immaterial.")

    # ---------------- GATE 2: s(T) not flat (P1) ----------------
    print("\n--- GATE 2 (P1): does service time depend on temperature? ---")
    if not e2:
        print("  no exp2 data")
        gates["G2_sT"] = None
    else:
        sl = e2.get("slope")
        rho = e2.get("spearman_temp_latency")
        if sl:
            print(f"  slope {sl['slope_ms_per_C']:+.3f} ms/C "
                  f"[{sl['ci_lo']:+.3f}, {sl['ci_hi']:+.3f}]  rho={rho:+.3f}")
            gates["G2_sT"] = bool(sl["excludes_zero"])
            print("  PASS" if sl["excludes_zero"] else
                  "  FAIL: CI includes zero -> s(T) flat -> TDAS DEAD")
        else:
            gates["G2_sT"] = None

    # ---------------- GATE 3: cold-latency match (attack #7) -------
    print("\n--- GATE 3: are HOT and COOL indistinguishable when cold? ---")
    if e3 and e3.get("cold_latency_match"):
        vm = e3["cold_latency_match"]
        if vm.get("note") == "mock":
            print("  (mock - not applicable)")
            gates["G3_match"] = None
        else:
            print(f"  hot={vm['hot_median_ms']:.1f}ms cool={vm['cool_median_ms']:.1f}ms "
                  f"gap={vm['relative_gap']*100:.1f}%")
            gates["G3_match"] = bool(vm["matched"])
            print("  PASS - job-length explanations ruled out by construction"
                  if vm["matched"] else
                  "  FAIL - cold latencies differ; attack #7 is LIVE")
    else:
        gates["G3_match"] = None

    if not e3:
        print("\nno exp3 data - stopping.")
        return

    runs = e3["runs"]

    # ---------------- GATE 4: mediator moves ----------------
    print("\n--- GATE 4: does ORDERING change TIME ABOVE KNEE? (mediator) ---")
    tak = by_mode(runs, "time_above_knee_s")
    if len(tak) < 2:
        print("  cannot test (knee unknown or single ordering)")
        gates["G4_mediator"] = None
    else:
        for m, v in sorted(tak.items()):
            print(f"  {m:12s} median {np.median(v):8.1f}s  (n={len(v)})")
        best = max(tak, key=lambda m: np.median(tak[m]))
        worst = min(tak, key=lambda m: np.median(tak[m]))
        d = boot_diff(tak[best], tak[worst])
        print(f"  max-min: {best} vs {worst}: "
              f"{d['diff']:+.1f}s [{d['ci_lo']:+.1f}, {d['ci_hi']:+.1f}] "
              f"excludes_zero={d['excludes_zero']}")
        gates["G4_mediator"] = bool(d["excludes_zero"])
        if not d["excludes_zero"]:
            print("  FAIL: orderings produce indistinguishable thermal")
            print("        trajectories. No causal pathway. TDAS DEAD.")

    # ---------------- GATE 5: outcome moves ----------------
    print("\n--- GATE 5: does ORDERING change TAIL LATENCY? (outcome) ---")
    res = {}
    for field, label in (("p99_ms", "P99"), ("p95_ms", "P95"),
                         ("mean_ms", "mean"),
                         ("throughput_jobs_s", "throughput")):
        g = by_mode(runs, field)
        if len(g) < 2:
            continue
        print(f"\n  [{label}]")
        for m, v in sorted(g.items()):
            print(f"    {m:12s} median {np.median(v):9.2f}  (n={len(v)})")
        pairs = []
        for a, b_ in combinations(sorted(g), 2):
            d = boot_diff(g[a], g[b_])
            cd = cliffs_delta(g[a], g[b_])
            pairs.append((a, b_, d, cd))
            flag = "*" if d["excludes_zero"] else " "
            print(f"    {flag} {a:12s} - {b_:12s}: {d['diff']:+8.2f} "
                  f"[{d['ci_lo']:+.2f}, {d['ci_hi']:+.2f}] "
                  f"cliffs_d={cd:+.2f}")
        res[field] = pairs

    p99 = res.get("p99_ms", [])
    any_sig = any(d["excludes_zero"] for _, _, d, _ in p99)
    gates["G5_outcome"] = bool(any_sig)
    if not any_sig:
        print("\n  No P99 difference between orderings survives its CI.")

    # secondary outcomes: did anything else respond?
    sec = {}
    for f in ("mean_ms", "throughput_jobs_s"):
        sec[f] = any(d["excludes_zero"] for _, _, d, _ in res.get(f, []))
    gates["G5b_secondary"] = bool(any(sec.values()))
    if not any_sig and gates["G5b_secondary"]:
        moved = [k for k, v in sec.items() if v]
        print(f"  BUT these DID respond: {', '.join(moved)}")
        print("  -> the thermal effect is real but does not land on the tail.")
        print("     Consider reframing the claim around the metric that moved,")
        print("     rather than abandoning or overclaiming on P99.")

    # ---------------- GATE 6: mediation consistency ----------------
    print("\n--- GATE 6: is the effect MEDIATED (not just correlated)? ---")
    if gates.get("G4_mediator") and gates.get("G5_outcome"):
        xs, ys = [], []
        for r in runs:
            if r.get("time_above_knee_s") is not None and r.get("p99_ms"):
                xs.append(r["time_above_knee_s"])
                ys.append(r["p99_ms"])
        if len(xs) > 4 and np.std(xs) > 0:
            c = float(np.corrcoef(xs, ys)[0, 1])
            print(f"  corr(time_above_knee, p99) = {c:+.3f} over {len(xs)} runs")
            gates["G6_mediation"] = bool(c > 0.3)
            print("  PASS - mediator tracks outcome" if c > 0.3 else
                  "  FAIL - mediator and outcome do not co-vary; the latency")
        else:
            gates["G6_mediation"] = None
    else:
        print("  not testable (need both G4 and G5)")
        gates["G6_mediation"] = None

    # ---------------- VERDICT ----------------
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    for k, v in gates.items():
        sym = {True: "PASS", False: "FAIL", None: "n/a "}[v]
        print(f"  {sym}  {k}")

    hard_fail = [k for k, v in gates.items()
                 if v is False and k in ("G1_knee", "G2_sT", "G4_mediator")]
    if hard_fail:
        print("\n  >>> KILL TDAS <<<")
        print(f"  Failed structural gate(s): {', '.join(hard_fail)}")
        print("  The physical precondition does not hold on this hardware.")
        print("  Write it up as a negative result. Do NOT rescue by renaming,")
        print("  adding a neural network, or complicating the scheduler.")
    elif gates.get("G5_outcome") is False and gates.get("G5b_secondary"):
        print("\n  >>> REFORMULATE <<<")
        print("  Thermal trajectory moves AND a performance metric responds,")
        print("  but not the tail. The physics is real; the P99 framing is")
        print("  wrong. Re-scope the claim to the metric that actually moved")
        print("  (mean latency / throughput) and re-check that the mediation")
        print("  chain holds for THAT metric before writing anything.")
    elif gates.get("G5_outcome") is False:
        print("\n  >>> LIKELY KILL <<<")
        print("  Thermal trajectory moves but no performance metric responds.")
        print("  The effect exists physically but is immaterial to anything")
        print("  the paper would claim to improve.")
    elif gates.get("G3_match") is False:
        print("\n  >>> REFORMULATE <<<")
        print("  Effect present but HOT/COOL cold latencies differ, so a")
        print("  job-length explanation is not excluded. Re-tune and rerun")
        print("  Experiment 3 before claiming anything.")
    elif all(gates.get(k) for k in ("G1_knee", "G2_sT", "G4_mediator",
                                    "G5_outcome")):
        print("\n  >>> PROCEED (conditionally) <<<")
        print("  Full causal chain intact: ordering -> thermal trajectory ->")
        print("  throttle exposure -> tail latency, with job-length")
        print("  explanations excluded by construction.")
        print("  Next: implement baselines B4 (alternation) and B5 (tuned")
        print("  threshold). TDAS must beat BOTH, not just FCFS.")
    else:
        print("\n  >>> INCONCLUSIVE - gates incomplete, see above ---")

    if mock:
        print("\n  (mock data - this verdict is a pipeline check, not science)")
    print("=" * 70)


if __name__ == "__main__":
    main()
