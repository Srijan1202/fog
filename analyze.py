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
import math
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


STRUCTURAL = ("G1_knee", "G2_sT", "G4_mediator")


UNDERVOLT_BITS = 0x1 | 0x10000   # Pi get_throttled: under-voltage now / since boot

# Amendment A3: the clock exp1's knee must be measured in, per backend. On the
# Pi 4, scaling_cur_freq (freq_mhz) does not show firmware throttling
# (instrument check clockprobe-20261016-01); the firmware ARM clock does.
KNEE_CLOCK = {"raspberrypi": "arm_clock_mhz"}
MAX_CLOCK_MISSING = 0.05         # A3: at most 5 % of samples without a clock reading


def knee_clock(backend):
    """Telemetry field exp1's knee is measured in for this backend."""
    return KNEE_CLOCK.get(backend, "freq_mhz")


def _num(x):
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x))


def _undervoltage(e1):
    """True if exp1's recorded throttle flags show under-voltage during heating.
    Runs without recorded flags (older metadata, non-Pi backends) are not
    penalized here; there is nothing to check."""
    th = (e1.get("heat_summary") or {}).get("throttled_or")
    try:
        return th is not None and bool(int(th, 16) & UNDERVOLT_BITS)
    except (TypeError, ValueError):
        return False


def exp2_knee_support(e2):
    """Experiment 2's knee as SUPPORTING evidence. Never establishes G1."""
    if e2 and _num(e2.get("knee_c")):
        return (f"exp2 observed a frequency drop from {e2['knee_c']:.1f} C "
                "(supporting evidence only; does not establish G1)")
    return None


def _exp1_invalid(e1, k1):
    """Why exp1 is not a completed, valid sustained-load test (None = valid).
    Applies to PASS and FAIL alike."""
    abort = e1.get("abort_reason") or ""
    status = e1.get("p4_status")
    if status == "SAFETY_STOPPED" or abort.startswith("max_temp"):
        return f"SAFETY-STOPPED: INCONCLUSIVE FOR KNEE DETECTION ({abort})"
    if status == "ABORTED" or abort:
        return f"exp1 heating aborted ({abort})"
    if not _num(k1.get("f_max")):
        return "exp1 recorded no CPU frequency; a knee could not be detected"
    if not e1.get("mock"):
        util = e1.get("cpu_util_heating")
        if not util or not util.get("per_core_busy"):
            return ("exp1 recorded no CPU load; sustained max load cannot be "
                    "confirmed")
        n = len(util["per_core_busy"])
        if util["avg_cores_busy"] < n - 0.5:   # same rule as exp1_thermal.py
            return (f"exp1 heating loaded only {util['avg_cores_busy']:.2f} "
                    f"of {n} cores")
    wall, minutes = e1.get("heat_wall_s"), e1.get("heat_minutes")
    if _num(wall) and _num(minutes) and wall < 0.95 * minutes * 60:
        return f"exp1 heating lasted {wall:.0f} s of the planned {minutes * 60:.0f} s"
    if _undervoltage(e1):
        return "under-voltage flagged during exp1 heating; run not valid"
    need = KNEE_CLOCK.get(e1.get("backend"))
    if need and not e1.get("mock"):                        # Amendment A3
        if k1.get("clock_source") != need:
            return (f"exp1 knee was not measured in {need} (run made before "
                    "Amendment A3); its recorded outcome is unchanged, but G1 "
                    "needs an Amendment A3 run")
        n, miss = k1.get("clock_samples"), k1.get("clock_missing")
        if not (_num(n) and _num(miss) and n > 0):
            return f"exp1 knee record lacks {need} sample counts"
        if miss / n > MAX_CLOCK_MISSING:
            return (f"exp1 {need} unreadable in {miss} of {n} samples "
                    f"(> {MAX_CLOCK_MISSING:.0%})")
    return None


def classify_g1(e1, e2):
    """G1 (P4): did a completed, valid Experiment 1 establish a throttle knee?

    Returns (state, knee_c, depth, reason). Explicit rule:
      NOT_TESTED    no Experiment 1 metadata.
      INCONCLUSIVE  Experiment 1 is not a completed, valid sustained-load test
                    (safety-stopped, aborted, partial or unrecorded load,
                    short heating, no frequency record, under-voltage,
                    incomplete metadata; on the Pi, a knee not measured in the
                    firmware ARM clock or with > 5 % of clock readings missing,
                    Amendment A3), even if it recorded a knee; or a
                    knee record is incomplete; or a valid no-knee Experiment 1
                    is contradicted by an Experiment 2 knee.
      PASS          valid Experiment 1 with a complete knee record
                    (knee_c, f_max, f_min, depth numeric).
      FAIL          valid Experiment 1 with no knee, not contradicted by an
                    Experiment 2 knee.
    Experiment 2 is supporting evidence only: its knee can never PASS G1 and
    never overrides an inconclusive or missing Experiment 1.
    """
    if e1 is None:
        return ("NOT_TESTED", None, None, "no exp1 data"
                + ("; an exp2 knee alone does not establish G1"
                   if exp2_knee_support(e2) else ""))
    k1 = e1.get("knee")
    if not isinstance(k1, dict):
        return ("INCONCLUSIVE", None, None,
                "exp1 metadata incomplete: no knee measurement recorded")
    invalid = _exp1_invalid(e1, k1)
    if invalid:
        if _num(k1.get("knee_c")):
            invalid += (f"; a knee recorded at {k1['knee_c']:.1f} C does not "
                        "establish G1 from an invalid run")
        return "INCONCLUSIVE", None, None, invalid

    if k1.get("knee_c") is not None:                       # a knee was seen
        if not all(_num(k1.get(f)) for f in ("knee_c", "f_max", "f_min", "depth")):
            return ("INCONCLUSIVE", None, None,
                    "exp1 knee record incomplete (knee_c/f_max/f_min/depth)")
        return "PASS", k1["knee_c"], k1["depth"], "knee measured in a valid exp1"

    if exp2_knee_support(e2):
        return ("INCONCLUSIVE", None, None,
                f"conflicting evidence: completed exp1 found no knee but exp2 "
                f"observed a frequency drop from {e2['knee_c']:.1f} C")
    return ("FAIL", None, None,
            "completed exp1 at full load observed no throttle knee")


# ---------------------------------------------------------------------------
# G2 (P1) - Amendment A4 (approved 2026-10-18, AMENDMENT_A4_exp2.md): s(T) is
# measured under the job stream's own four-core load (P4c HOT jobs, two ramps).
# ---------------------------------------------------------------------------
G2_RULE = {"bin_c": 2.0, "min_jobs": 20, "n_boot": 2000, "seed": 0, "cover_bins": 2,
           "below_knee_c": 10.0, "min_clock_readable": 0.95, "config": "4c"}


def g2_bins(jobs, rule=G2_RULE):
    """2 C bins per ramp from completed jobs ({"ramp", "temp_before_c",
    "latency_ms"}); bins with fewer than min_jobs jobs are dropped."""
    groups = {}
    for j in jobs:
        t, lat = j.get("temp_before_c"), j.get("latency_ms")
        if not (_num(t) and _num(lat)):
            continue
        lo = math.floor(t / rule["bin_c"]) * rule["bin_c"]
        groups.setdefault((j["ramp"], lo), []).append((t, lat))
    out = []
    for (ramp, lo), v in sorted(groups.items()):
        if len(v) < rule["min_jobs"]:
            continue
        out.append({"ramp": ramp, "bin_lo_c": lo, "n": len(v),
                    "temp_median_c": float(np.median([x[0] for x in v])),
                    "latency_median_ms": float(np.median([x[1] for x in v]))})
    return out


def g2_slope(bins, rule=G2_RULE):
    """OLS slope of bin-median latency on bin-median temperature, with a
    bootstrap over (ramp, bin) clusters (each bin resampled whole). None when
    fewer than 3 bins or no temperature spread."""
    if len(bins) < 3:
        return None
    x = np.array([b["temp_median_c"] for b in bins], float)
    y = np.array([b["latency_median_ms"] for b in bins], float)
    if np.ptp(x) == 0:
        return None
    slope = float(np.polyfit(x, y, 1)[0])
    rng = np.random.default_rng(rule["seed"])
    boots = []
    for _ in range(rule["n_boot"]):
        idx = rng.integers(0, len(bins), len(bins))
        if np.ptp(x[idx]) == 0:
            continue
        boots.append(np.polyfit(x[idx], y[idx], 1)[0])
    if not boots:
        return None
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"slope_ms_per_C": slope, "ci_lo": float(lo), "ci_hi": float(hi),
            "n_bins": len(bins), "n_boot_used": len(boots)}


def g2_invalid(e2, rule=G2_RULE):
    """Why exp2 cannot decide G2 under A4 (None = valid)."""
    if e2.get("amendment") != "A4":
        return "exp2 record made before Amendment A4; it cannot decide G2"
    if e2.get("abort_reason") or e2.get("interrupted"):
        return f"exp2 aborted ({e2.get('abort_reason') or e2.get('interrupted')})"
    if not e2.get("mock"):
        wk = e2.get("workload") or {}
        if not wk.get("accepted") or wk.get("config") != rule["config"]:
            return "exp2 jobs are not from an ACCEPTED P4c workload validation"
        if not e2.get("monitor"):
            return "exp2 ran without verified supervision"
        ck = e2.get("clock") or {}
        n, ok = ck.get("reads"), ck.get("readable")
        if not (_num(n) and n > 0 and _num(ok)) or ok / n < rule["min_clock_readable"]:
            return (f"ARM clock readable in {ok} of {n} reads "
                    f"(need >= {rule['min_clock_readable']:.0%})")
    if e2.get("undervoltage"):
        return "under-voltage during exp2; run not valid"
    if not e2.get("jobs_complete"):
        return "an exp2 job did not complete its planned units"
    planned, done = e2.get("ramps_planned"), e2.get("ramps_completed")
    if not (_num(planned) and planned >= 1 and done == planned):
        return f"exp2 completed {done} of {planned} ramps"
    knee = e2.get("g1_knee_c")
    if not _num(knee):
        return "exp2 recorded no G1 knee"
    bins = e2.get("bins") or []
    for r in range(int(planned)):
        rb = [b for b in bins if b["ramp"] == r]
        above = sum(1 for b in rb if b["temp_median_c"] >= knee)
        below = sum(1 for b in rb if b["temp_median_c"] <= knee - rule["below_knee_c"])
        if above < rule["cover_bins"] or below < rule["cover_bins"]:
            return (f"ramp {r}: {above} kept bins at/above the knee ({knee:.1f} C) and "
                    f"{below} at/below knee - {rule['below_knee_c']:.0f} C "
                    f"(need {rule['cover_bins']} each)")
    return None


def classify_g2(e2, rule=G2_RULE):
    """G2 (P1) under A4. Returns (state, slope_dict, reason):
      NOT_TESTED    no exp2 data
      INCONCLUSIVE  exp2 not a completed, valid A4 run (incl. pre-A4 records), too
                    few bins, or the CI lies entirely below 0 (anomalous)
      PASS          the 95 % CI of the slope lies above 0
      FAIL          the CI includes 0"""
    if e2 is None:
        return "NOT_TESTED", None, "no exp2 data"
    why = g2_invalid(e2, rule)
    if why:
        return "INCONCLUSIVE", None, why
    sl = g2_slope(e2["bins"], rule)
    if sl is None:
        return "INCONCLUSIVE", None, "too few kept bins (or no temperature spread) for a slope"
    if sl["ci_lo"] > 0:
        return "PASS", sl, "service time rises with temperature (CI above 0)"
    if sl["ci_hi"] < 0:
        return ("INCONCLUSIVE", sl, "latency falls with temperature (CI below 0): "
                "physically unexpected, treated as an artifact")
    return "FAIL", sl, "CI includes 0: s(T) flat over the probed range"


def overall_verdict(gates):
    """Map gate results to a verdict. Returns (verdict, failed_structural)."""
    hard_fail = [k for k in STRUCTURAL if gates.get(k) is False]
    if gates.get("G1_knee") is None:
        return "FURTHER_EVIDENCE", hard_fail
    if hard_fail:
        return "KILL", hard_fail
    if gates.get("G5_outcome") is False and gates.get("G5b_secondary"):
        return "REFORMULATE", []
    if gates.get("G5_outcome") is False:
        return "LIKELY_KILL", []
    if gates.get("G3_match") is False:
        return "REFORMULATE_MATCH", []
    if all(gates.get(k) for k in ("G1_knee", "G2_sT", "G4_mediator",
                                  "G5_outcome")):
        return "PROCEED", []
    return "INCONCLUSIVE", []


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
    notes = {}
    mock = any(x and x.get("mock") for x in (e1, e2, e3))
    if mock:
        print("\n*** MOCK DATA - SIMULATED. NOT A RESULT. PIPELINE CHECK ONLY. ***")

    # ---------------- GATE 1: knee exists (P4) ----------------
    print("\n--- GATE 1 (P4): is there a throttle knee? ---")
    g1, knee, depth, why = classify_g1(e1, e2)
    if g1 == "FAIL":
        print("  FAIL: no throttle knee observed under sustained load.")
        gates["G1_knee"] = False
    elif g1 == "PASS":
        print(f"  knee = {knee:.1f} C" + (f", depth = {depth*100:.1f}%"
                                          if depth else ""))
        gates["G1_knee"] = True
        if depth is not None and depth < 0.05:
            print("  WARNING: throttle depth <5% - effect likely immaterial.")
    else:
        print(f"  {g1.replace('_', ' ')}: {why}")
        print("  A missing or truncated measurement is not a failed hypothesis.")
        gates["G1_knee"] = None
        notes["G1_knee"] = f"{g1.replace('_', ' ')}: {why}"
    support = exp2_knee_support(e2)
    if support:
        print(f"  supporting: {support}")
        notes["G1_support"] = support
    no_knee = gates["G1_knee"] is None

    # ---------------- GATE 2: s(T) not flat (P1) ----------------
    print("\n--- GATE 2 (P1): does service time depend on temperature? ---")
    g2, sl, why2 = classify_g2(e2)                     # Amendment A4
    if sl:
        print(f"  slope {sl['slope_ms_per_C']:+.3f} ms/C "
              f"[{sl['ci_lo']:+.3f}, {sl['ci_hi']:+.3f}] over {sl['n_bins']} bins")
    if g2 == "PASS":
        print("  PASS")
        gates["G2_sT"] = True
    elif g2 == "FAIL":
        gates["G2_sT"] = False
        if no_knee:
            print("  FAIL: CI includes zero -> s(T) flat over the probed "
                  "range (knee not established; see verdict)")
        else:
            print("  FAIL: CI includes zero -> s(T) flat -> TDAS DEAD")
    else:
        print(f"  {g2}: {why2}")
        gates["G2_sT"] = None
        notes["G2_sT"] = f"{g2}: {why2}"

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
        if no_knee:
            print(f"G1 {notes['G1_knee']} -> further evidence required.")
        return {"gates": gates, "notes": notes, "verdict": None}

    runs = e3["runs"]

    # ---------------- GATE 4: mediator moves ----------------
    print("\n--- GATE 4: does ORDERING change TIME ABOVE KNEE? (mediator) ---")
    tak = by_mode(runs, "time_above_knee_s")
    if len(tak) < 2:
        print("  cannot test (knee unknown or single ordering)")
        gates["G4_mediator"] = None
        notes["G4_mediator"] = "NOT TESTED: knee unknown or single ordering"
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
            print("        trajectories. No causal pathway." +
                  (" (knee not established; see verdict)" if no_knee
                   else " TDAS DEAD."))

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
        if v is None and k in STRUCTURAL:
            print(f"  NOT TESTED/INCONCLUSIVE  {k}"
                  + (f"  ({notes[k]})" if k in notes else ""))
            continue
        sym = {True: "PASS", False: "FAIL", None: "n/a "}[v]
        print(f"  {sym}  {k}")

    verdict, hard_fail = overall_verdict(gates)
    if verdict == "FURTHER_EVIDENCE":
        print("\n  >>> FURTHER EVIDENCE REQUIRED - NOT A KILL, NOT A PASS <<<")
        print(f"  G1 (throttle knee) is not established: {notes['G1_knee']}")
        if "G1_support" in notes:
            print(f"  Supporting: {notes['G1_support']}")
        print("  Without a measured knee, G2 and G4 cannot test TDAS: s(T) is")
        print("  expected to be flat below the knee, and time-above-knee needs")
        print("  a knee to exist.")
        if hard_fail:
            print(f"  Also FAILED: {', '.join(hard_fail)}. Reported, but not")
            print("  acted on until G1 is established.")
    elif verdict == "KILL":
        print("\n  >>> KILL TDAS <<<")
        print(f"  Failed structural gate(s): {', '.join(hard_fail)}")
        print("  The physical precondition does not hold on this hardware.")
        print("  Write it up as a negative result. Do NOT rescue by renaming,")
        print("  adding a neural network, or complicating the scheduler.")
    elif verdict == "REFORMULATE":
        print("\n  >>> REFORMULATE <<<")
        print("  Thermal trajectory moves AND a performance metric responds,")
        print("  but not the tail. The physics is real; the P99 framing is")
        print("  wrong. Re-scope the claim to the metric that actually moved")
        print("  (mean latency / throughput) and re-check that the mediation")
        print("  chain holds for THAT metric before writing anything.")
    elif verdict == "LIKELY_KILL":
        print("\n  >>> LIKELY KILL <<<")
        print("  Thermal trajectory moves but no performance metric responds.")
        print("  The effect exists physically but is immaterial to anything")
        print("  the paper would claim to improve.")
    elif verdict == "REFORMULATE_MATCH":
        print("\n  >>> REFORMULATE <<<")
        print("  Effect present but HOT/COOL cold latencies differ, so a")
        print("  job-length explanation is not excluded. Re-tune and rerun")
        print("  Experiment 3 before claiming anything.")
    elif verdict == "PROCEED":
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
    return {"gates": gates, "notes": notes, "verdict": verdict}


if __name__ == "__main__":
    main()
