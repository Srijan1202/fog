#!/usr/bin/env python3
"""
Independent reproduction of the TDAS pilot statistics from the raw run archives.

    python reproduce_stats.py <evidence_root> <repo_root> <out_json>

<evidence_root> is the folder that holds a3/, a4/ and a7/ (the run archives and their
SHA256SUMS). Nothing is written to it; archives are read in place.

Part A recomputes every gate statistic from raw records with code written here and
compares it with the value the official pipeline recorded (exp*.meta.json) and with the
committed analysis code (analyze.py). Part B is EXPLORATORY: post-hoc checks that were
not preregistered and cannot change the verdict.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tarfile
from itertools import combinations
from statistics import NormalDist

import numpy as np

EV, REPO, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, REPO)
sys.dont_write_bytecode = True
import analyze  # noqa: E402  (the committed, preregistered analysis code)

R = {}            # everything that ends up in the JSON
LOG = []


def log(msg=""):
    LOG.append(msg)
    print(msg)


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tar_json(tar, name):
    return json.load(tar.extractfile(name))


def tar_jsonl(tar, name):
    return [json.loads(l) for l in tar.extractfile(name) if l.strip()]


def check(label, ours, recorded, tol=1e-9):
    ok = (ours is None and recorded is None) or (
        ours is not None and recorded is not None and abs(ours - recorded) <= tol)
    return ok


# ------------------------------------------------------------------ archives
ARCH = {
    "exp1": f"{EV}/a3/run_exp1-4core-a3-20261016-01.tar",
    "exp2_01": f"{EV}/a4/run_exp2-a4-20261018-01.tar",
    "exp2_02": f"{EV}/a4/run_exp2-a4-20261018-02.tar",
    "exp2_03": f"{EV}/a4/run_exp2-a4-20261018-03.tar",
    "exp3": f"{EV}/a7/run_exp3-a7-20261018-01.tar",
}
P4C_REPORT = f"{EV}/a3/diag/p4c/run_extract/results/workload_validation/wv-p4c-20261017-01/report.json"
R["archives"] = {k: sha256(v) for k, v in ARCH.items()}
R["archives"]["p4c_report"] = sha256(P4C_REPORT)
log("== archives (sha256)")
for k, v in R["archives"].items():
    log(f"  {k:10s} {v}")

# ================================================================== PART A
log("\n================ PART A: reproduction of the recorded statistics ================")

# ---------------- G1: Experiment 1 knee (A1/A3 rule: ARM clock < 98 % of max) ----------
t1 = tarfile.open(ARCH["exp1"])
P1 = "results/exp1-4core-a3-20261016-01/"
m1 = tar_json(t1, P1 + "exp1.meta.json")
tele1 = tar_jsonl(t1, P1 + "exp1.telemetry.jsonl")
s1 = [x for x in tele1 if x.get("temp_c") is not None]
clk = [x.get("arm_clock_mhz") for x in s1]
fmax = max(c for c in clk if c is not None)
below = [(x["temp_c"], x["arm_clock_mhz"]) for x in s1
         if x.get("arm_clock_mhz") is not None and x["arm_clock_mhz"] < 0.98 * fmax]
knee = min(t for t, _ in below)
fmin = min(c for c in clk if c is not None)
g1 = {"samples_with_temp": len(s1), "clock_missing": sum(1 for c in clk if c is None),
      "f_max_mhz": fmax, "f_min_mhz": fmin, "knee_c": knee, "depth": 1 - fmin / fmax,
      "throttled_samples": len(below), "peak_temp_c": max(x["temp_c"] for x in s1),
      "recorded_knee": m1["knee"], "classify_g1": list(analyze.classify_g1(m1, None)),
      "workers": m1.get("workers"), "heat_minutes": m1.get("heat_minutes"),
      "cpu_util_heating": m1.get("cpu_util_heating"), "fit_cooling_tau_s": (m1.get("fit_cooling") or {}).get("tau")}
g1["matches_record"] = (check("knee", knee, m1["knee"]["knee_c"]) and check("fmax", fmax, m1["knee"]["f_max"])
                        and g1["throttled_samples"] == m1["knee"]["throttled_samples"])
levels = {}
for c in clk:
    if c is not None:
        levels[round(c)] = levels.get(round(c), 0) + 1
g1["clock_levels_mhz"] = dict(sorted(levels.items(), reverse=True))
R["G1"] = g1
log(f"\nG1: knee {knee:.3f} C (record {m1['knee']['knee_c']}), f {fmax:.1f} -> {fmin:.1f} MHz, depth "
    f"{g1['depth']*100:.2f} %, {len(below)} throttled of {len(s1)} samples, clock missing "
    f"{g1['clock_missing']}; classify_g1 = {g1['classify_g1'][0]}; matches record: {g1['matches_record']}")

# ---------------- G2: Experiment 2 run -03 (A4 rule) -----------------------------------
t2 = tarfile.open(ARCH["exp2_03"])
P2 = "results/exp2-a4-20261018-03/"
m2 = tar_json(t2, P2 + "exp2.meta.json")
jobs2 = tar_jsonl(t2, P2 + "exp2.jobs.jsonl")
groups = {}
for j in jobs2:
    if j.get("temp_before_c") is None or j.get("latency_ms") is None:
        continue
    lo = math.floor(j["temp_before_c"] / 2.0) * 2.0
    groups.setdefault((j["ramp"], lo), []).append((j["temp_before_c"], j["latency_ms"]))
bins = [{"ramp": r, "bin_lo_c": lo, "n": len(v), "temp_median_c": float(np.median([a for a, _ in v])),
         "latency_median_ms": float(np.median([b for _, b in v]))}
        for (r, lo), v in sorted(groups.items()) if len(v) >= 20]
x = np.array([b["temp_median_c"] for b in bins])
y = np.array([b["latency_median_ms"] for b in bins])
slope = float(np.polyfit(x, y, 1)[0])
rng = np.random.default_rng(0)
boots = []
for _ in range(2000):
    idx = rng.integers(0, len(bins), len(bins))
    if np.ptp(x[idx]) == 0:
        continue
    boots.append(np.polyfit(x[idx], y[idx], 1)[0])
lo2, hi2 = np.percentile(boots, [2.5, 97.5])
rec = m2["g2"]["slope"]
g2 = {"jobs": len(jobs2), "kept_bins": len(bins), "slope_ms_per_c": slope, "ci": [float(lo2), float(hi2)],
      "recorded": rec, "classify_g2": analyze.classify_g2(m2)[0],
      "bins_match_record": bins == m2["bins"],
      "matches_record": check("slope", slope, rec["slope_ms_per_C"], 1e-9) and check("lo", lo2, rec["ci_lo"], 1e-9)
      and check("hi", hi2, rec["ci_hi"], 1e-9),
      "bins": bins, "procedure": m2.get("procedure"), "ramps": m2.get("ramps"),
      "clock": {k: m2["clock"][k] for k in ("reads", "readable")}, "mediation_levels": m2["mediation"]["levels"]}
below_knee = [b["latency_median_ms"] for b in bins if b["temp_median_c"] < 78.0]
above = [b for b in bins if b["temp_median_c"] >= 82.0]
g2["latency_bins_below_78c_range"] = [min(below_knee), max(below_knee)]
g2["latency_bins_ge_82c"] = [(b["ramp"], b["temp_median_c"], b["latency_median_ms"]) for b in above]
R["G2"] = g2
log(f"\nG2: {len(jobs2)} jobs, {len(bins)} kept bins; slope {slope:+.4f} ms/C, 95% CI [{lo2:+.4f}, {hi2:+.4f}] "
    f"(record {rec['slope_ms_per_C']:+.4f} [{rec['ci_lo']:+.4f}, {rec['ci_hi']:+.4f}]); "
    f"bins identical to record: {g2['bins_match_record']}; classify_g2 = {g2['classify_g2']}")

# ---------------- G3: cold-latency match (P4c validation) ----------------------------
rep = json.load(open(P4C_REPORT))
cfg = rep["configs"]["4c"]
gap = cfg["evaluation"]["gap"]
vals = cfg["evaluation"]["values"]
R["G3"] = {"status": cfg["status"], "gap": gap, "values": {k: vals[k] for k in vals if not isinstance(vals[k], (list, dict))},
           "criteria": cfg["evaluation"]["criteria"], "frozen": {k: cfg["frozen"][k] for k in ("P", "n_b", "u_h", "u_c", "S_ms", "gemms_per_unit", "params_id")},
           "run_id": rep.get("run_id")}
log(f"\nG3: P4c {cfg['status']}; gap g_hat {gap['g_hat']*100:+.4f} % CI [{gap['ci_lo']*100:+.4f}, {gap['ci_hi']*100:+.4f}] %, "
    f"class {gap['class']}, pairs {gap['n_pairs']}; HOT median {vals.get('hot_median_ms')} ms, COOL median {vals.get('cool_median_ms')} ms")

# ---------------- Experiment 3: sequences, validity, per-sequence metrics ------------
t3 = tarfile.open(ARCH["exp3"])
P3 = "results/exp3-a7-20261018-01/"
m3 = tar_json(t3, P3 + "exp3.meta.json")
jobs3 = tar_jsonl(t3, P3 + "exp3.jobs.jsonl")
tele3 = tar_jsonl(t3, P3 + "exp3.telemetry.jsonl")
KNEE = m3["g1_knee_c"]
runs = m3["runs"]
by_run_jobs, by_run_tele = {}, {}
for j in jobs3:
    by_run_jobs.setdefault(j["run_index"], []).append(j)
for s in tele3:
    by_run_tele.setdefault(s["run_index"], []).append(s)
recomputed, mismatches = [], []
for r in runs:
    J = sorted(by_run_jobs[r["run_index"]], key=lambda j: j["idx"])
    T = by_run_tele.get(r["run_index"], [])
    lat = np.array([j["latency_ms"] for j in J])
    temps = [s["temp_c"] for s in T if s.get("temp_c") is not None]
    ours = {"n_jobs": len(J), "p99_ms": float(np.percentile(lat, 99)), "p95_ms": float(np.percentile(lat, 95)),
            "p50_ms": float(np.percentile(lat, 50)), "mean_ms": float(lat.mean()),
            "time_above_knee_s": sum(1 for t in temps if t >= KNEE) * 0.2,
            "throughput_jobs_s": len(J) / r["wall_s"], "peak_temp_c": max(temps), "telemetry_samples": len(temps)}
    seq_classes = [j["class"] for j in J]
    ours["n_hot"] = seq_classes.count("HOT")
    ours["units_ok"] = all(j["units_done"] == j["units_planned"] for j in J)
    ours["flags_ok"] = all(not ((j.get("throttled_after") or 0) & analyze.UNDERVOLT_BITS) for j in J)
    for k in ("n_jobs", "p99_ms", "p95_ms", "p50_ms", "mean_ms", "time_above_knee_s", "throughput_jobs_s",
              "peak_temp_c", "telemetry_samples"):
        if not check(k, float(ours[k]), float(r[k]), 1e-6):
            mismatches.append((r["run_index"], k, ours[k], r[k]))
    recomputed.append({**ours, "run_index": r["run_index"], "rep": r["rep"], "mode": r["mode"], "valid": r["valid"],
                       "start_temp_c": r["start_temp_c"], "preheat_s": r["preheat_s"], "cooldown_s": r["cooldown_s"],
                       "wall_s": r["wall_s"], "mean_temp_c": r["mean_temp_c"],
                       "jobs_with_live_throttle_flags": r["jobs_with_live_throttle_flags"]})
modes = m3["orderings"]
order_ok = {}
for r in recomputed:
    J = sorted(by_run_jobs[r["run_index"]], key=lambda j: j["idx"])
    c = [j["class"] for j in J]
    if r["mode"] == "hot_first":
        order_ok[r["run_index"]] = c == ["HOT"] * 300 + ["COOL"] * 300
    elif r["mode"] == "cool_first":
        order_ok[r["run_index"]] = c == ["COOL"] * 300 + ["HOT"] * 300
    elif r["mode"] == "alternating":
        order_ok[r["run_index"]] = all(c[i] != c[i + 1] for i in range(len(c) - 1))
    else:
        order_ok[r["run_index"]] = c.count("HOT") == 300
R["exp3_session"] = {
    "n_sequences": len(runs), "valid": sum(r["valid"] for r in runs), "n_valid": m3["n_valid"],
    "reruns": sum(1 for r in runs if r["rerun_of"] is not None), "session_complete": m3["session_complete"],
    "abort_reason": m3["abort_reason"], "interrupted": m3["interrupted"], "undervoltage": m3["undervoltage"],
    "a7_validity": m3["a7_validity"], "e3_invalid": analyze.e3_invalid(m3), "jobs_total": len(jobs3),
    "telemetry_samples": len(tele3), "per_sequence_mismatches_vs_record": mismatches,
    "ordering_structure_ok": all(order_ok.values()), "all_units_ok": all(r["units_ok"] for r in recomputed),
    "no_undervoltage_between_jobs": all(r["flags_ok"] for r in recomputed),
    "start_temp_range": [min(r["start_temp_c"] for r in recomputed), max(r["start_temp_c"] for r in recomputed)],
    "preheat_s_range": [min(r["preheat_s"] for r in recomputed), max(r["preheat_s"] for r in recomputed)],
    "idle_temp_c": m3["idle_temp_c"], "knee_c": KNEE, "plan": m3["plan"], "schedule": m3["schedule"],
}
log(f"\nExp3: {len(runs)} sequences ({R['exp3_session']['valid']} valid, {R['exp3_session']['reruns']} reruns), "
    f"n_valid {m3['n_valid']}; {len(jobs3)} jobs, {len(tele3)} telemetry samples; e3_invalid = "
    f"{R['exp3_session']['e3_invalid']}; per-sequence metrics recomputed from raw jobs/telemetry: "
    f"{'all match the record' if not mismatches else mismatches}; ordering structure ok: "
    f"{R['exp3_session']['ordering_structure_ok']}; units ok: {R['exp3_session']['all_units_ok']}")


def by_mode(field):
    return {m: [r[field] for r in recomputed if r["mode"] == m and r["valid"]] for m in modes}


def boot_diff(a, b, iters=10000, seed=0):
    """Same procedure as the preregistered analyze.boot_diff, written independently."""
    g = np.random.default_rng(seed)
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = np.empty(iters)
    for i in range(iters):
        d[i] = np.median(g.choice(a, len(a), replace=True)) - np.median(g.choice(b, len(b), replace=True))
    lo, hi = np.percentile(d, [2.5, 97.5])
    p = min(1.0, 2.0 * min(np.mean(d <= 0), np.mean(d >= 0)))
    return {"diff": float(np.median(a) - np.median(b)), "ci_lo": float(lo), "ci_hi": float(hi), "p_value": float(p)}


def holm(p, alpha=0.05):
    order = sorted(range(len(p)), key=lambda i: p[i])
    out, m = [False] * len(p), len(p)
    for rank, i in enumerate(order):
        if p[i] <= alpha / (m - rank):
            out[i] = True
        else:
            break
    return out


def cliffs(a, b):
    gt = sum(1 for u in a for v in b if u > v)
    lt = sum(1 for u in a for v in b if u < v)
    return (gt - lt) / (len(a) * len(b))


summ = {}
for f in ("time_above_knee_s", "p99_ms", "p95_ms", "p50_ms", "mean_ms", "throughput_jobs_s", "peak_temp_c",
          "mean_temp_c", "start_temp_c", "preheat_s", "jobs_with_live_throttle_flags"):
    g = by_mode(f)
    summ[f] = {m: {"median": float(np.median(v)), "min": float(min(v)), "max": float(max(v)),
                   "q1": float(np.percentile(v, 25)), "q3": float(np.percentile(v, 75)), "n": len(v)} for m, v in g.items()}
R["exp3_per_ordering"] = summ

# ---------------- G4 ----------------
tak = by_mode("time_above_knee_s")
best = max(tak, key=lambda m: np.median(tak[m]))
worst = min(tak, key=lambda m: np.median(tak[m]))
d4 = boot_diff(tak[best], tak[worst])
off4 = analyze.boot_diff(tak[best], tak[worst])
R["G4"] = {"best": best, "worst": worst, "ours": d4, "official": off4,
           "pass": d4["ci_lo"] > 0 or d4["ci_hi"] < 0,
           "matches_official": all(check(k, d4[k], off4[k], 1e-12) for k in ("diff", "ci_lo", "ci_hi", "p_value")),
           "ties_note": f"worst-median orderings tied at {np.median(tak[worst])}: "
                        + ", ".join(m for m in modes if np.median(tak[m]) == np.median(tak[worst]))}
log(f"\nG4: {best} - {worst}: {d4['diff']:+.2f} s, 95% CI [{d4['ci_lo']:+.2f}, {d4['ci_hi']:+.2f}] "
    f"-> PASS={R['G4']['pass']}; identical to analyze.boot_diff: {R['G4']['matches_official']}; {R['G4']['ties_note']}")

# ---------------- G5 / G5b ----------------
g5 = {}
for field in ("p99_ms", "p95_ms", "mean_ms", "throughput_jobs_s"):
    g = by_mode(field)
    pairs = []
    for a, b in combinations(sorted(g), 2):
        d = boot_diff(g[a], g[b])
        off = analyze.boot_diff(g[a], g[b])
        pairs.append({"a": a, "b": b, **d, "cliffs_delta": cliffs(g[a], g[b]),
                      "uncorrected_ci_excludes_0": d["ci_lo"] > 0 or d["ci_hi"] < 0,
                      "matches_official": all(check(k, d[k], off[k], 1e-12) for k in ("diff", "ci_lo", "ci_hi", "p_value"))})
    keep = holm([p["p_value"] for p in pairs])
    for p, k in zip(pairs, keep):
        p["survives_holm"] = k
    g5[field] = pairs
R["G5"] = g5
R["G5_pass"] = any(p["survives_holm"] for p in g5["p99_ms"])
R["G5b_pass"] = any(p["survives_holm"] for p in g5["mean_ms"]) or any(p["survives_holm"] for p in g5["throughput_jobs_s"])
log("\nG5 (P99) pairs: diff [95% CI] p (Holm) cliffs_d")
for p in g5["p99_ms"]:
    log(f"  {p['a']:12s} - {p['b']:12s} {p['diff']:+6.2f} [{p['ci_lo']:+6.2f}, {p['ci_hi']:+6.2f}] p={p['p_value']:.4f} "
        f"holm={p['survives_holm']} d={p['cliffs_delta']:+.2f} official-match={p['matches_official']}")
log(f"G5 pass: {R['G5_pass']}; G5b pass: {R['G5b_pass']}")

# official end-to-end verdict on the same three records
gates_dir = os.path.join(os.path.dirname(os.path.abspath(OUT)), "_gates_input")
os.makedirs(gates_dir, exist_ok=True)
for name, (tar, member) in {"exp1": (t1, P1 + "exp1.meta.json"), "exp2": (t2, P2 + "exp2.meta.json"),
                            "exp3": (t3, P3 + "exp3.meta.json")}.items():
    with open(os.path.join(gates_dir, f"{name}.meta.json"), "wb") as f:
        f.write(tar.extractfile(member).read())
import contextlib, io  # noqa: E401,E402
buf = io.StringIO()
argv = sys.argv
sys.argv = ["analyze.py", "--outdir", gates_dir]
with contextlib.redirect_stdout(buf):
    res = analyze.main()
sys.argv = argv
R["official_verdict"] = {"verdict": res["verdict"], "gates": res["gates"]}
log(f"\nanalyze.main() on exp1 A3 + exp2 run -03 + exp3: verdict {res['verdict']}, gates {res['gates']}")

# ================================================================== PART B (EXPLORATORY)
log("\n================ PART B: EXPLORATORY (post hoc; cannot change the verdict) ================")
X = {}
# B1: what numpy's P99 is for n = 600
n = 600
pos = (n - 1) * 0.99
X["p99_estimator"] = {"method": "numpy.percentile default (linear, Hyndman-Fan type 7)", "n": n,
                      "zero_based_position": pos,
                      "meaning": "ascending sort x[0..599]: P99 = x[593] + 0.01*(x[594]-x[593]), i.e. the 7th "
                                 "largest value interpolated 1% toward the 6th largest",
                      "lower_order_stat_from_top": n - math.floor(pos), "upper_order_stat_from_top": n - math.ceil(pos),
                      "weight_on_upper": pos - math.floor(pos)}
log(f"B1 P99 with n=600 (linear): position {pos:.2f} -> {n - math.floor(pos)}th largest, interpolated "
    f"{(pos - math.floor(pos))*100:.0f}% toward the {n - math.ceil(pos)}th largest")

# B2: slow jobs per sequence; jobs started at or above the knee
for thr in (200.0, 205.0, 210.0):
    X[f"jobs_over_{int(thr)}ms"] = {m: [sum(1 for j in by_run_jobs[r["run_index"]] if j["latency_ms"] > thr)
                                        for r in recomputed if r["mode"] == m] for m in modes}
X["jobs_started_at_or_above_knee"] = {m: [sum(1 for j in by_run_jobs[r["run_index"]]
                                              if (j.get("temp_before_c") or 0) >= KNEE)
                                          for r in recomputed if r["mode"] == m] for m in modes}
for k in ("jobs_over_200ms", "jobs_over_205ms", "jobs_over_210ms", "jobs_started_at_or_above_knee"):
    log(f"B2 {k}: " + "; ".join(f"{m} median {np.median(v):.1f} [{min(v)}, {max(v)}]" for m, v in X[k].items()))

# B3: latency of HOT jobs by whether they started at/above the knee (exp3, pooled, all orderings)
hot_lat = {"below_knee": [], "at_or_above_knee": []}
for j in jobs3:
    if j["class"] == "HOT" and j.get("temp_before_c") is not None:
        hot_lat["at_or_above_knee" if j["temp_before_c"] >= KNEE else "below_knee"].append(j["latency_ms"])
X["hot_latency_by_knee"] = {k: {"n": len(v), "median": float(np.median(v)), "p90": float(np.percentile(v, 90))}
                            for k, v in hot_lat.items()}
log(f"B3 HOT job latency: " + "; ".join(f"{k} n={v['n']} median {v['median']:.2f} ms p90 {v['p90']:.2f}"
                                         for k, v in X["hot_latency_by_knee"].items()))

# B4: within-sequence P99 uncertainty (job-level bootstrap; assumes exchangeable jobs, ignores autocorrelation)
w = []
g = np.random.default_rng(1)
for r in recomputed:
    lat = np.array([j["latency_ms"] for j in by_run_jobs[r["run_index"]]])
    bs = [np.percentile(g.choice(lat, len(lat), replace=True), 99) for _ in range(1000)]
    lo, hi = np.percentile(bs, [2.5, 97.5])
    w.append(hi - lo)
X["within_sequence_p99_ci_width_ms"] = {"median": float(np.median(w)), "min": float(min(w)), "max": float(max(w))}
log(f"B4 within-sequence P99 95% bootstrap CI width: median {np.median(w):.2f} ms [{min(w):.2f}, {max(w):.2f}]")

# B5: rough sensitivity (normal approximation on per-sequence P99; Holm's first step alpha = 0.05/6)
sd = {m: float(np.std(by_mode("p99_ms")[m], ddof=1)) for m in modes}
mad = {m: float(np.median(np.abs(np.array(by_mode("p99_ms")[m]) - np.median(by_mode("p99_ms")[m])))) * 1.4826 for m in modes}
za, zb = NormalDist().inv_cdf(1 - 0.05 / 12), NormalDist().inv_cdf(0.8)
mde = {}
for a, b in combinations(sorted(modes), 2):
    mde[f"{a}-{b}"] = {"sd_based": (za + zb) * math.sqrt(sd[a] ** 2 / 10 + sd[b] ** 2 / 10),
                       "robust_sd_based": (za + zb) * math.sqrt(mad[a] ** 2 / 10 + mad[b] ** 2 / 10)}
X["p99_between_sequence_sd_ms"] = sd
X["p99_between_sequence_robust_sd_ms"] = mad
X["approx_mde_ms_80pct_power_holm_first_step"] = mde
log("B5 per-sequence P99 SD (ms): " + ", ".join(f"{m} {sd[m]:.2f} (robust {mad[m]:.2f})" for m in modes))
log("B5 approx. minimum detectable P99 difference (80% power, alpha 0.05/6): " +
    ", ".join(f"{k} {v['sd_based']:.2f} (robust {v['robust_sd_based']:.2f})" for k, v in mde.items()))

# B6: blocked (paired-by-repetition) view of P99, hot_first - alternating
pd_ = []
for rep in range(10):
    a = [r["p99_ms"] for r in recomputed if r["rep"] == rep and r["mode"] == "hot_first"]
    b = [r["p99_ms"] for r in recomputed if r["rep"] == rep and r["mode"] == "alternating"]
    pd_.append(a[0] - b[0])
X["paired_p99_hot_first_minus_alternating_by_rep"] = {"diffs": pd_, "median": float(np.median(pd_)),
                                                       "n_positive": sum(1 for d in pd_ if d > 0)}
log(f"B6 paired-by-rep P99 hot_first - alternating: median {np.median(pd_):+.2f} ms, {sum(1 for d in pd_ if d > 0)}/10 positive")


# B7: drift across the session (Spearman rank correlation of P99 and of start-of-sequence cooldown with run order)
def spearman(u, v):
    ru = np.argsort(np.argsort(u)).astype(float)
    rv = np.argsort(np.argsort(v)).astype(float)
    ru -= ru.mean()
    rv -= rv.mean()
    return float(ru @ rv / math.sqrt((ru @ ru) * (rv @ rv)))


idx = [r["run_index"] for r in recomputed]
X["drift_spearman_vs_run_order"] = {f: spearman(idx, [r[f] for r in recomputed])
                                    for f in ("p99_ms", "mean_ms", "time_above_knee_s", "peak_temp_c", "preheat_s")}
log("B7 Spearman vs run order: " + ", ".join(f"{k} {v:+.2f}" for k, v in X["drift_spearman_vs_run_order"].items()))

# B8: correlation of time above knee and P99 across sequences (G6 statistic, NOT a gate here)
tk = [r["time_above_knee_s"] for r in recomputed]
p9 = [r["p99_ms"] for r in recomputed]
X["corr_time_above_knee_p99_pearson"] = float(np.corrcoef(tk, p9)[0, 1])
X["corr_time_above_knee_p99_spearman"] = spearman(tk, p9)
log(f"B8 across 40 sequences: Pearson r(time above knee, P99) = {X['corr_time_above_knee_p99_pearson']:+.3f}, "
    f"Spearman {X['corr_time_above_knee_p99_spearman']:+.3f} (descriptive; G6 was not testable)")
# B9: the sequences with the most time above the knee (heterogeneity within an ordering)
hot = sorted(recomputed, key=lambda r: -r["time_above_knee_s"])[:8]
X["hottest_sequences"] = [{k: r[k] for k in ("run_index", "rep", "mode", "time_above_knee_s", "p99_ms", "mean_ms",
                                              "peak_temp_c", "start_temp_c", "preheat_s", "cooldown_s", "wall_s")}
                          for r in hot]
log("B9 sequences with the most time above the knee (run_index, rep, mode, s above knee, P99, preheat s, cooldown s):")
for r in X["hottest_sequences"]:
    log(f"  #{r['run_index']:2d} rep {r['rep']} {r['mode']:11s} {r['time_above_knee_s']:5.1f} s  P99 {r['p99_ms']:6.2f}  "
        f"preheat {r['preheat_s']:5.1f}  cooldown {r['cooldown_s']:6.1f}")
# B10: pooled-job P99 per ordering (all 6000 jobs of an ordering; NOT the preregistered statistic)
X["pooled_p99_ms"] = {m: float(np.percentile([j["latency_ms"] for j in jobs3 if j["mode"] == m], 99)) for m in modes}
log("B10 pooled-job P99 (6000 jobs per ordering, not preregistered): " +
    ", ".join(f"{m} {v:.2f}" for m, v in X["pooled_p99_ms"].items()))
# B11: jobs slower than 215 ms by ordering (extreme tail; thermal or not)
X["jobs_over_215ms_total"] = {m: sum(1 for j in jobs3 if j["mode"] == m and j["latency_ms"] > 215) for m in modes}
X["jobs_over_215ms_started_below_knee"] = {m: sum(1 for j in jobs3 if j["mode"] == m and j["latency_ms"] > 215
                                                  and (j.get("temp_before_c") or 0) < KNEE) for m in modes}
log(f"B11 jobs > 215 ms per ordering (of 6000): {X['jobs_over_215ms_total']}; of these started below the knee: "
    f"{X['jobs_over_215ms_started_below_knee']}")
# B12: per-repetition view (session order): P99 and time above knee for each ordering, and
# the cooldown / pre-heat durations, which index the board's thermal state between sequences
X["by_rep"] = []
log("B12 per repetition: P99 ms (time above knee s) by ordering | median cooldown s, median preheat s")
for rep in range(10):
    row = {"rep": rep}
    cells = []
    for m in modes:
        r = [x for x in recomputed if x["rep"] == rep and x["mode"] == m][0]
        row[m] = {"p99_ms": r["p99_ms"], "time_above_knee_s": r["time_above_knee_s"]}
        cells.append(f"{m[:4]} {r['p99_ms']:6.2f} ({r['time_above_knee_s']:4.1f})")
    rr = [x for x in recomputed if x["rep"] == rep]
    row["cooldown_s_median"] = float(np.median([x["cooldown_s"] for x in rr]))
    row["preheat_s_median"] = float(np.median([x["preheat_s"] for x in rr]))
    X["by_rep"].append(row)
    log(f"  rep {rep}: " + " | ".join(cells) + f" | cooldown {row['cooldown_s_median']:6.1f}, preheat {row['preheat_s_median']:4.1f}")
R["exploratory"] = X
R["per_sequence"] = recomputed
with open(OUT, "w") as f:
    json.dump(R, f, indent=1, default=float)
with open(OUT.replace(".json", ".log"), "w") as f:
    f.write("\n".join(LOG) + "\n")
print(f"\nwrote {OUT}")
