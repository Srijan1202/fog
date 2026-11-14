#!/usr/bin/env python3
"""
Figures for the paper, generated only from the raw run archives (no synthetic data).

    python make_figures.py <evidence_root> <figures_dir>

Sources (inside <evidence_root>):
  fig1  a3/run_exp1-4core-a3-20261016-01.tar :: exp1.telemetry.jsonl   (ARM clock vs temperature)
  fig2  a4/run_exp2-a4-20261018-03.tar      :: exp2.meta.json (bins, g2) (s(T), G2)
  fig3  a7/run_exp3-a7-20261018-01.tar      :: exp3.telemetry.jsonl   (temperature trajectories)
  fig4  a7/...                              :: exp3.meta.json runs     (time above knee, G4)
  fig5  a7/...                              :: exp3.meta.json runs     (P99, mean, throughput; G5/G5b)
  fig6  a7/...                              :: exp3.jobs.jsonl         (latency tail; EXPLORATORY)
"""
import json
import os
import sys
import tarfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

EV, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.labelsize": 8, "legend.fontsize": 7,
                     "xtick.labelsize": 7, "ytick.labelsize": 7, "axes.linewidth": 0.6,
                     "savefig.bbox": "tight", "savefig.dpi": 300})
W1 = 3.45          # IEEE single column, inches
ORD = ["hot_first", "cool_first", "alternating", "random"]
COL = {"hot_first": "#c0392b", "cool_first": "#2471a3", "alternating": "#239b56", "random": "#7d3c98"}
MARK = {"hot_first": "o", "cool_first": "s", "alternating": "^", "random": "D"}


def jl(tar, name):
    return [json.loads(l) for l in tar.extractfile(name) if l.strip()]


def save(fig, name):
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT, f"{name}.{ext}"))
    plt.close(fig)
    print("wrote", name)


# ---------------- fig1: Experiment 1, ARM clock vs temperature ----------------
t1 = tarfile.open(f"{EV}/a3/run_exp1-4core-a3-20261016-01.tar")
m1 = json.load(t1.extractfile("results/exp1-4core-a3-20261016-01/exp1.meta.json"))
tel1 = [x for x in jl(t1, "results/exp1-4core-a3-20261016-01/exp1.telemetry.jsonl")
        if x.get("temp_c") is not None and x.get("arm_clock_mhz") is not None]
knee = m1["knee"]["knee_c"]
fmax = m1["knee"]["f_max"]
T = np.array([x["temp_c"] for x in tel1])
F = np.array([x["arm_clock_mhz"] for x in tel1])
rng = np.random.default_rng(0)
fig, ax = plt.subplots(figsize=(W1, 2.2))
ax.scatter(T + rng.uniform(-0.15, 0.15, len(T)), F, s=2, alpha=0.25, color="0.3", linewidths=0,
           label=f"5 Hz samples (n={len(T)})")
ax.axhline(0.98 * fmax, color="0.5", ls=":", lw=0.8, label="98 % of max clock")
ax.axvline(knee, color="#c0392b", ls="--", lw=0.8, label=f"knee {knee:.1f} °C")
ax.set_xlabel("SoC temperature (°C)")
ax.set_ylabel("Firmware ARM clock (MHz)")
ax.legend(loc="lower left", frameon=False)
save(fig, "fig1_exp1_knee")

# ---------------- fig2: Experiment 2 run -03, s(T) ----------------
t2 = tarfile.open(f"{EV}/a4/run_exp2-a4-20261018-03.tar")
m2 = json.load(t2.extractfile("results/exp2-a4-20261018-03/exp2.meta.json"))
bins = m2["bins"]
sl = m2["g2"]["slope"]
fig, ax = plt.subplots(figsize=(W1, 2.2))
for r, mk in ((0, "o"), (1, "s")):
    b = [x for x in bins if x["ramp"] == r]
    ax.plot([x["temp_median_c"] for x in b], [x["latency_median_ms"] for x in b], mk, ms=3.5,
            mfc="none", mew=0.8, label=f"ramp {r} (bin medians)")
xs = np.array([x["temp_median_c"] for x in bins])
ys = np.array([x["latency_median_ms"] for x in bins])
c = np.polyfit(xs, ys, 1)
xx = np.linspace(xs.min(), xs.max(), 50)
ax.plot(xx, np.polyval(c, xx), color="0.4", lw=0.8,
        label=f"OLS {sl['slope_ms_per_C']:+.3f} ms/°C [{sl['ci_lo']:+.3f}, {sl['ci_hi']:+.3f}]")
ax.axvline(m2["g1_knee_c"], color="#c0392b", ls="--", lw=0.8, label="G1 knee")
ax.set_xlabel("Bin-median temperature before dispatch (°C)")
ax.set_ylabel("Bin-median HOT job latency (ms)")
ax.legend(loc="upper left", frameon=False)
save(fig, "fig2_exp2_sT")

# ---------------- Experiment 3 data ----------------
t3 = tarfile.open(f"{EV}/a7/run_exp3-a7-20261018-01.tar")
P3 = "results/exp3-a7-20261018-01/"
m3 = json.load(t3.extractfile(P3 + "exp3.meta.json"))
runs = [r for r in m3["runs"] if r["valid"]]
tel3 = jl(t3, P3 + "exp3.telemetry.jsonl")
jobs3 = jl(t3, P3 + "exp3.jobs.jsonl")
K3 = m3["g1_knee_c"]

# ---------------- fig3: temperature trajectories ----------------
by = {}
for s in tel3:
    if s.get("temp_c") is not None:
        by.setdefault(s["run_index"], []).append((s["t_wall"], s["temp_c"]))
grid = np.arange(0, 119, 1.0)        # every sequence lasts >= 119.6 s
fig, ax = plt.subplots(figsize=(W1, 2.3))
for m in ORD:
    curves = []
    for r in runs:
        if r["mode"] != m:
            continue
        pts = sorted(by[r["run_index"]])
        t0 = pts[0][0]
        tt = np.array([p[0] - t0 for p in pts])
        vv = np.array([p[1] for p in pts])
        curves.append(np.interp(grid, tt, vv, right=np.nan))
    C = np.array(curves)
    med = np.nanmedian(C, axis=0)
    q1, q3 = np.nanpercentile(C, 25, axis=0), np.nanpercentile(C, 75, axis=0)
    ax.plot(grid, med, color=COL[m], lw=1.0, label=f"{m} (n={len(curves)})")
    ax.fill_between(grid, q1, q3, color=COL[m], alpha=0.15, linewidth=0)
ax.axhline(K3, color="k", ls="--", lw=0.7, label=f"knee {K3:.1f} °C")
ax.set_xlabel("Time since sequence start (s)")
ax.set_ylabel("SoC temperature (°C)")
ax.set_ylim(58, 86)
ax.legend(loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 1.22), columnspacing=0.8)
save(fig, "fig3_exp3_trajectories")


# ---------------- fig4 / fig5: per-sequence strips ----------------
def strip(ax, field, ylabel, fmt=None):
    for i, m in enumerate(ORD):
        v = np.array([r[field] for r in runs if r["mode"] == m])
        x = i + np.random.default_rng(i).uniform(-0.12, 0.12, len(v))
        ax.plot(x, v, MARK[m], color=COL[m], ms=3.2, mfc="none", mew=0.8)
        ax.plot([i - 0.25, i + 0.25], [np.median(v)] * 2, color="k", lw=1.2)
    ax.set_xticks(range(len(ORD)))
    ax.set_xticklabels(["hot\nfirst", "cool\nfirst", "alter-\nnating", "random"])
    ax.set_ylabel(ylabel)


fig, ax = plt.subplots(figsize=(W1, 2.2))
strip(ax, "time_above_knee_s", "Time above knee per sequence (s)")
save(fig, "fig4_exp3_time_above_knee")

fig, axs = plt.subplots(1, 3, figsize=(7.16, 2.2))
strip(axs[0], "p99_ms", "P99 latency per sequence (ms)")
strip(axs[1], "mean_ms", "Mean latency per sequence (ms)")
strip(axs[2], "throughput_jobs_s", "Throughput per sequence (jobs/s)")
fig.tight_layout()
save(fig, "fig5_exp3_outcomes")

# ---------------- fig6: EXPLORATORY latency tail (pooled jobs per ordering) ----------------
fig, ax = plt.subplots(figsize=(W1, 2.3))
for m in ORD:
    v = np.sort(np.array([j["latency_ms"] for j in jobs3 if j["mode"] == m]))
    ccdf = 1.0 - np.arange(len(v)) / len(v)
    ax.step(v, ccdf, where="post", color=COL[m], lw=0.9, label=f"{m} (n={len(v)} jobs)")
ax.axhline(0.01, color="0.5", ls=":", lw=0.7, label="P99 level (1 %)")
ax.set_yscale("log")
ax.set_xlim(195, 230)
ax.set_xlabel("Job latency (ms)")
ax.set_ylabel("Fraction of jobs slower (CCDF)")
ax.legend(loc="upper right", frameon=False)
save(fig, "fig6_exp3_tail_ccdf_exploratory")

# ---------------- fig7: EXPLORATORY session order: thermal state and P99 per sequence ----------------
start = {}
for s in tel3:
    start[s["run_index"]] = min(start.get(s["run_index"], s["t_wall"]), s["t_wall"])
t_first = min(start.values())
fig, axs = plt.subplots(3, 1, figsize=(W1, 4.2), sharex=True)
for m in ORD:
    rr = [r for r in runs if r["mode"] == m]
    tm = [(start[r["run_index"]] - t_first) / 60.0 for r in rr]
    axs[0].plot(tm, [r["cooldown_s"] for r in rr], MARK[m], color=COL[m], ms=3, mfc="none", mew=0.8, label=m)
    axs[1].plot(tm, [r["time_above_knee_s"] for r in rr], MARK[m], color=COL[m], ms=3, mfc="none", mew=0.8)
    axs[2].plot(tm, [r["p99_ms"] for r in rr], MARK[m], color=COL[m], ms=3, mfc="none", mew=0.8)
axs[0].set_ylabel("Cooldown\nbefore seq. (s)")
axs[1].set_ylabel("Time above\nknee (s)")
axs[2].set_ylabel("P99 (ms)")
axs[2].set_xlabel("Sequence start, minutes after the first sequence")
axs[0].legend(loc="lower center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 1.0), columnspacing=0.6,
              handletextpad=0.2)
fig.tight_layout()
save(fig, "fig7_exp3_session_order_exploratory")
print("rep 2 -> 3 boundary (min):",
      round((start[[r for r in runs if r["rep"] == 3][0]["run_index"]] - t_first) / 60.0, 1),
      "session span (min):", round((max(start.values()) - t_first) / 60.0, 1))
