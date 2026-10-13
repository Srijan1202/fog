#!/usr/bin/env python3
"""
validate_workload.py - non-thermal validation of the fixed-work HOT/COOL
workload: V0 (unit + overhead), V1 (P=1), V2 (P=4), V4 (telemetry, descriptive).
V3 (frequency caps) is excluded. Pre-registration: PREREG_workload_validation.md.

    python3 validate_workload.py --plan                  # print the plan; no load
    python3 validate_workload.py --outdir results/workload_validation --run-id <id>

Runs only on real hardware (the mock backend is refused). Run it only with
explicit approval, detached (setsid nohup), together with the external
watchdog tools/validation_monitor.sh, and only after the Pi has been idle
for at least 15 minutes (the idle baseline is measured at start; a warm start
raises every block's start threshold). See README "Launching the workload
validation". On SIGTERM or Ctrl+C the run stops, the worker pools are shut
down, and report.json is written with the interruption recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import sys
import time
from dataclasses import dataclass, field

import numpy as np

import runner
import sensors
import workload as wl

# ---------------------------------------------------------------------------
# PRE-REGISTERED CONSTANTS - identical to PREREG_workload_validation.md.
# Do not change after validation data exists; test_validate_workload.py pins them.
# ---------------------------------------------------------------------------
PREREG = {
    "d_cool": 0.35,
    "l_target_ms": 200.0,
    "unit_sizes": [64, 96, 128],
    "v0a_jobs": 4, "v0a_units": 50,
    "v0b_jobs": 10, "v0b_units": 50,
    "v0c_jobs": 50,
    "unit_min_ms": 2.0, "unit_cv_max_v0": 0.02,
    "calib_pairs": 10,
    "warmup_pairs": 5,
    "accept_blocks": {"1": [20, 20, 15], "4": [15, 15, 15, 15]},
    "accepted_pairs": 40,
    "reruns_per_config": 2, "v0_reruns": 2, "v4_reruns": 1,
    "v4_pairs": 20,
    "guard_max_temp_c": 65.0, "guard_min_freq_mhz": 1800.0,
    "idle_margin_c": 2.0, "cooldown_cap_s": 600.0, "idle_window_s": 30.0,
    "external_monitor_c": 67.0,
    "job_timeout_s": 5.0, "telemetry_hz": 5.0,
    "n_boot": 10000, "seed": 0, "gap_target": 0.02, "gap_hard": 0.05,
    "A2_hot_duty_min": {"1": 0.95, "4": 0.90},
    "A3_cool_duty_tol": 0.05,
    "A4_contrast_lo_min": 2.0,
    "A6_unit_cv_max": {"1": 0.02, "4": 0.03},
    "A6_hot_lat_cv_max": {"1": 0.03, "4": 0.04},
    "A8_overhead_frac_max": 0.05,
    "A9_imbalance_max": {"4": 0.05},
}

LIVE_FLAG_MASK = 0xF          # get_throttled bits 0-3: happening now
STICKY_SHIFT = 16             # bits 16-19: has happened since boot

TARGET, WITHIN, FAIL = "TARGET MET", "WITHIN HARD LIMIT", "FAIL"
ACCEPTED, REJECTED, INCOMPLETE, NOT_RUN = "ACCEPTED", "REJECTED", "INCOMPLETE", "NOT_RUN"

LIMITATION = ("The interval treats the 40 accepted pairs as interchangeable. "
              "Dependence within and between blocks (drift, cache or idle state, "
              "ambient) is not modelled, so the interval may be too narrow. "
              "Per-block gaps are diagnostics only; they do not remove this "
              "limitation and cannot change the classification.")
V4_LABEL = ("DESCRIPTIVE ONLY. Not used in any acceptance decision. The "
            "telemetry-off and telemetry-on blocks run one after the other, so "
            "any difference is confounded with block order and time drift.")


class RerunsExhausted(RuntimeError):
    pass


class CooldownCap(RuntimeError):
    pass


class SampleCountError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Pure pieces: pair plans, guards, statistics, criteria, V0 analysis
# ---------------------------------------------------------------------------
def pair_plan(n_pairs: int, hot: wl.JobSpec, cool: wl.JobSpec) -> list:
    """Pair k runs HOT then COOL when k is even, COOL then HOT when k is odd."""
    plan = []
    for k in range(n_pairs):
        order = (hot, cool) if k % 2 == 0 else (cool, hot)
        for pos, spec in enumerate(order):
            plan.append({"spec": spec, "pair": k, "pos": pos})
    return plan


def accepted_pairs(jobs: list, warmup: int = PREREG["warmup_pairs"]) -> list:
    """(k, hot_job, cool_job) for every pair k >= warmup. Position only;
    no other exclusion."""
    by_pair = {}
    for j in jobs:
        if j.get("pair") is not None:
            by_pair.setdefault(j["pair"], {})[j["class"]] = j
    return [(k, p["HOT"], p["COOL"]) for k, p in sorted(by_pair.items())
            if k >= warmup and "HOT" in p and "COOL" in p]


def sample_violation(s: dict, prereg: dict = PREREG):
    """Reason a telemetry sample breaks the guard, or None."""
    t, f, th = s.get("temp_c"), s.get("freq_mhz"), s.get("throttled")
    if t is None:
        return "temperature unreadable"
    if t >= prereg["guard_max_temp_c"]:
        return f"temperature {t:.1f} C >= {prereg['guard_max_temp_c']} C"
    if f is None:
        return "frequency unreadable"
    if f < prereg["guard_min_freq_mhz"]:
        return f"frequency {f:.0f} MHz < {prereg['guard_min_freq_mhz']:.0f} MHz"
    if th is not None and th & LIVE_FLAG_MASK:
        return f"live throttle/under-voltage flags {hex(th)}"
    return None


def sticky_violation(before, after):
    """Reason if a sticky flag (bits 16-19) appeared between two reads."""
    if before is None or after is None:
        return "throttle flags unreadable for the before/after audit"
    new = ((after >> STICKY_SHIFT) & ~(before >> STICKY_SHIFT)) & LIVE_FLAG_MASK
    if new:
        return f"sticky throttle/under-voltage flag appeared during block ({hex(after)})"
    if after & LIVE_FLAG_MASK:
        return f"live throttle/under-voltage flags at end of block ({hex(after)})"
    return None


def classify_gap(lo: float, hi: float, prereg: dict = PREREG) -> str:
    """Inclusive bounds on unrounded values."""
    t, h = prereg["gap_target"], prereg["gap_hard"]
    if -t <= lo and hi <= t:
        return TARGET
    if -h <= lo and hi <= h:
        return WITHIN
    return FAIL


def paired_bootstrap(h, c, cpu_h, cpu_c, prereg: dict = PREREG) -> dict:
    """Pre-registered paired bootstrap over whole HOT/COOL pairs.
    Refuses unless there are exactly `accepted_pairs` pairs."""
    n = prereg["accepted_pairs"]
    h, c = np.asarray(h, float), np.asarray(c, float)
    cpu_h, cpu_c = np.asarray(cpu_h, float), np.asarray(cpu_c, float)
    if not (len(h) == len(c) == len(cpu_h) == len(cpu_c) == n):
        raise SampleCountError(f"paired bootstrap needs exactly {n} pairs, "
                               f"got {len(h)}")
    r = (c - h) / h
    rng = np.random.default_rng(prereg["seed"])
    nb = prereg["n_boot"]
    g, ratio, rmed = np.empty(nb), np.empty(nb), np.empty(nb)
    for b in range(nb):
        idx = rng.integers(0, n, size=n)
        g[b] = np.median(c[idx]) / np.median(h[idx]) - 1.0
        ratio[b] = np.median(cpu_h[idx]) / np.median(cpu_c[idx])
        rmed[b] = np.median(r[idx])
    lo, hi = np.percentile(g, [2.5, 97.5])
    return {
        "n_pairs": n, "n_boot": nb, "seed": prereg["seed"],
        "g_hat": float(np.median(c) / np.median(h) - 1.0),
        "ci_lo": float(lo), "ci_hi": float(hi),
        "class": classify_gap(float(lo), float(hi), prereg),
        "cpu_ratio_hat": float(np.median(cpu_h) / np.median(cpu_c)),
        "cpu_ratio_lo": float(np.percentile(ratio, 2.5)),
        "pair_r_median": float(np.median(r)),
        "pair_r_ci": [float(x) for x in np.percentile(rmed, [2.5, 97.5])],
        "limitation": LIMITATION,
    }


def cliffs_delta(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    gt = sum(int((x > b).sum()) for x in a)
    lt = sum(int((x < b).sum()) for x in a)
    return (gt - lt) / (len(a) * len(b))


def _cv(x) -> float:
    x = np.asarray(x, float)
    return float(np.std(x, ddof=1) / np.mean(x)) if len(x) > 1 else float("nan")


def evaluate_config(P: int, blocks: list, prereg: dict = PREREG) -> dict:
    """Criteria A1-A9 on the valid acceptance blocks. Raises SampleCountError
    unless exactly `accepted_pairs` pairs result (never classifies fewer)."""
    key = str(P)
    pairs = [(b["block_id"], k, hj, cj) for b in blocks
             for (k, hj, cj) in accepted_pairs(b["jobs"], prereg["warmup_pairs"])]
    if len(pairs) != prereg["accepted_pairs"]:
        raise SampleCountError(f"{len(pairs)} accepted pairs, need "
                               f"{prereg['accepted_pairs']}")
    hot = [p[2] for p in pairs]
    cool = [p[3] for p in pairs]
    acc_jobs = hot + cool
    all_jobs = [j for b in blocks for j in b["jobs"]]
    boot = paired_bootstrap([j["latency_ms"] for j in hot],
                            [j["latency_ms"] for j in cool],
                            [j["cpu_ms"] for j in hot],
                            [j["cpu_ms"] for j in cool], prereg)
    hot_duty = float(np.median([j["realized_duty"] for j in hot]))
    cool_duty = float(np.median([j["realized_duty"] for j in cool]))
    unit_times = [u for j in hot for u in (j.get("unit_ms") or [])]
    unit_cv = _cv(unit_times) if unit_times else float("nan")
    hot_lat_cv = _cv([j["latency_ms"] for j in hot])
    overhead = float(np.median([j["overhead_ms"] / j["latency_ms"] for j in acc_jobs]))
    imbalance = float(np.median([j["imbalance"] for j in acc_jobs]))
    d = prereg["d_cool"]
    crit = {
        "A1": all(j["units_done"] == j["units_planned"] for j in all_jobs),
        "A2": hot_duty >= prereg["A2_hot_duty_min"][key],
        "A3": abs(cool_duty - d * hot_duty) <= prereg["A3_cool_duty_tol"],
        "A4": boot["cpu_ratio_lo"] >= prereg["A4_contrast_lo_min"],
        "A5": boot["class"] in (TARGET, WITHIN),
        "A6": (unit_cv <= prereg["A6_unit_cv_max"][key]
               and hot_lat_cv <= prereg["A6_hot_lat_cv_max"][key]),
        "A7": all(b.get("valid", True) for b in blocks),
        "A8": overhead <= prereg["A8_overhead_frac_max"],
    }
    if key in prereg["A9_imbalance_max"]:
        crit["A9"] = imbalance <= prereg["A9_imbalance_max"][key]
    per_block = {}
    for b in blocks:
        bp = accepted_pairs(b["jobs"], prereg["warmup_pairs"])
        per_block[b["block_id"]] = float(
            np.median([p[2]["latency_ms"] for p in bp])
            / np.median([p[1]["latency_ms"] for p in bp]) - 1.0)
    h_lat = [j["latency_ms"] for j in hot]
    c_lat = [j["latency_ms"] for j in cool]
    return {
        "status": ACCEPTED if all(crit.values()) else REJECTED,
        "criteria": crit,
        "values": {"hot_duty_median": hot_duty, "cool_duty_median": cool_duty,
                   "unit_cv": unit_cv, "hot_latency_cv": hot_lat_cv,
                   "overhead_frac_median": overhead,
                   "imbalance_median": imbalance,
                   "hot_median_ms": float(np.median(h_lat)),
                   "cool_median_ms": float(np.median(c_lat))},
        "gap": boot,
        "diagnostics": {"per_block_gap": per_block,
                        "cliffs_delta": cliffs_delta(c_lat, h_lat),
                        "p95_gap": float(np.percentile(c_lat, 95)
                                         / np.percentile(h_lat, 95) - 1.0),
                        "note": "diagnostics only; not used for the decision"},
    }


def analyze_unit_sizes(jobs: list, prereg: dict = PREREG) -> dict:
    """V0a: per n_b, median unit time, CV and median overhead fraction."""
    out = {}
    for n_b in prereg["unit_sizes"]:
        js = [j for j in jobs if j["n_b"] == n_b]
        units = [u for j in js for u in (j.get("unit_ms") or [])]
        if not units:
            continue
        ofrac = [1.0 - s["compute_ms"] / s["wall_ms"]
                 for j in js for s in j["shards"] if s["wall_ms"] > 0]
        med = float(np.median(units))
        cv = _cv(units)
        out[str(n_b)] = {"t_u_ms": med, "cv": cv,
                         "overhead_frac": float(np.median(ofrac)) if ofrac else None,
                         "qualifies": med >= prereg["unit_min_ms"]
                         and cv <= prereg["unit_cv_max_v0"]}
    return out


def choose_unit_size(stats: dict):
    """Smallest median overhead fraction among qualifying sizes; None if none."""
    q = [(v["overhead_frac"], int(k)) for k, v in stats.items() if v["qualifies"]]
    return min(q)[1] if q else None


def describe_v4(off_jobs: list, on_jobs: list, prereg: dict = PREREG) -> dict:
    """Descriptive telemetry comparison; independent bootstrap. Never gates."""
    out = {"label": V4_LABEL}
    rng = np.random.default_rng(prereg["seed"])
    for cls in ("HOT", "COOL"):
        a = np.array([j["latency_ms"] for (_, h, c) in accepted_pairs(off_jobs)
                      for j in (h, c) if j["class"] == cls])
        b = np.array([j["latency_ms"] for (_, h, c) in accepted_pairs(on_jobs)
                      for j in (h, c) if j["class"] == cls])
        if len(a) == 0 or len(b) == 0:
            out[cls] = None
            continue
        draws = np.empty(prereg["n_boot"])
        for i in range(prereg["n_boot"]):
            draws[i] = (np.median(rng.choice(b, len(b))) /
                        np.median(rng.choice(a, len(a))) - 1.0)
        out[cls] = {"off_median_ms": float(np.median(a)),
                    "on_median_ms": float(np.median(b)),
                    "shift": float(np.median(b) / np.median(a) - 1.0),
                    "ci": [float(x) for x in np.percentile(draws, [2.5, 97.5])],
                    "n_off": len(a), "n_on": len(b)}
    return out


# ---------------------------------------------------------------------------
# Block results and the per-configuration state machine
# ---------------------------------------------------------------------------
@dataclass
class BlockResult:
    block_id: str
    phase: str
    position: str
    valid: bool
    reason: str | None
    jobs: list
    telemetry: list = field(default_factory=list)
    sticky_before: int | None = None
    sticky_after: int | None = None
    params_id: str | None = None
    telemetry_on: bool = True

    def as_dict(self) -> dict:
        return {"block_id": self.block_id, "phase": self.phase,
                "position": self.position, "valid": self.valid,
                "reason": self.reason, "params_id": self.params_id,
                "telemetry_on": self.telemetry_on,
                "sticky_before": self.sticky_before,
                "sticky_after": self.sticky_after,
                "jobs": self.jobs, "telemetry": self.telemetry}


class Store:
    """Writes blocks and parameters. Valid blocks go to blocks/, invalid ones
    to discarded/, stale ones to discarded/stale/. Nothing is deleted."""

    def __init__(self, root):
        self.root = root

    def _write(self, rel, obj):
        """Atomic: write a temporary file, then rename over the target, so an
        interruption never leaves a half-written JSON file."""
        if self.root is None:
            return
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=1, default=_json_default)
        os.replace(tmp, path)

    def block(self, res: BlockResult):
        sub = "blocks" if res.valid else "discarded"
        self._write(f"{sub}/{res.block_id}.json", res.as_dict())

    def mark_stale(self, res: BlockResult):
        """Move a counted block to discarded/stale/ (it no longer counts)."""
        if self.root is None:
            return
        src = os.path.join(self.root, "blocks", f"{res.block_id}.json")
        dst = os.path.join(self.root, "discarded", "stale", f"{res.block_id}.json")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(src):
            os.replace(src, dst)
        else:
            self._write(f"discarded/stale/{res.block_id}.json", res.as_dict())

    def json(self, rel, obj):
        self._write(rel, obj)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, wl.JobSpec):
        return o.__dict__
    raise TypeError(type(o))


class Budget:
    """Re-execution accounting: running a (phase, position) that has run
    before costs one rerun. Raises RerunsExhausted instead of exceeding."""

    def __init__(self, limit: int):
        self.limit, self.used, self.executed = limit, 0, {}

    def claim(self, phase: str, position: str) -> int:
        key = (phase, position)
        n = self.executed.get(key, 0)
        if n > 0:
            if self.used >= self.limit:
                raise RerunsExhausted(f"rerun limit {self.limit} reached "
                                      f"before re-running {phase} {position}")
            self.used += 1
        self.executed[key] = n + 1
        return n + 1


class ConfigRun:
    """Calibrate (C1, C2) -> freeze -> acceptance -> evaluate, for one P.
    `execute(block_id, phase, position, plan, params_id, telemetry_on,
    record_units)` returns a BlockResult. `calibration_check(frozen)` may
    report a later finding that invalidates frozen parameters."""

    def __init__(self, P: int, v0: dict, execute, store: Store | None = None,
                 prereg: dict = PREREG, calibration_check=None, log=print):
        self.P, self.v0, self.execute, self.prereg = P, v0, execute, prereg
        self.store = store or Store(None)
        self.calibration_check = calibration_check
        self.log = log
        self.budget = Budget(prereg["reruns_per_config"])
        self.history = []          # every executed block (dict summary)
        self.frozen_versions = []  # every frozen parameter set, with stale flag
        self.current = None        # the frozen set in use (never stale)
        self.stale_blocks = []     # acceptance blocks invalidated with stale params

    # -- helpers ------------------------------------------------------------
    def _exec(self, phase, position, plan, params_id, record_units):
        n = self.budget.claim(phase, position)
        bid = f"P{self.P}-{position}-e{n}"
        res = self.execute(bid, phase, position, plan, params_id, True, record_units)
        self.store.block(res)
        self.history.append({"block_id": bid, "phase": phase, "position": position,
                             "valid": res.valid, "reason": res.reason,
                             "params_id": params_id})
        if not res.valid:
            self.log(f"  [P={self.P}] {bid} INVALID: {res.reason}")
        return res

    def _run_valid(self, phase, position, plan, params_id, record_units=False):
        while True:
            res = self._exec(phase, position, plan, params_id, record_units)
            if res.valid:
                return res

    # -- calibration ---------------------------------------------------------
    def _calibrate(self, base: dict) -> dict:
        S = base["S0_ms"]
        rounds = []
        for r, pos in enumerate(("C1", "C2"), 1):
            hot, cool = wl.make_job_specs({**base, "S_ms": S})
            res = self._run_valid("calibration", pos,
                                  pair_plan(self.prereg["calib_pairs"], hot, cool),
                                  params_id=f"calib-{pos}")
            acc = accepted_pairs(res.jobs, self.prereg["warmup_pairs"])
            S_new, clamped = wl.adjust_sleep_ms(
                S, [p[1]["latency_ms"] for p in acc], [p[2]["latency_ms"] for p in acc])
            rounds.append({"round": r, "block_id": res.block_id, "S_in_ms": S,
                           "S_out_ms": S_new, "clamped": clamped,
                           "pairs_used": [p[0] for p in acc]})
            S = S_new
        frozen = {**{k: base[k] for k in ("P", "n_b", "u_h", "u_c")}, "S_ms": S,
                  "params_id": f"P{self.P}-F{len(self.frozen_versions) + 1}",
                  "calibration": rounds, "stale": False,
                  "frozen_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.frozen_versions.append(frozen)
        self.store.json(f"P{self.P}/frozen_{frozen['params_id']}.json", frozen)
        self.log(f"  [P={self.P}] frozen {frozen['params_id']}: u_h={frozen['u_h']} "
                 f"u_c={frozen['u_c']} S={S:.2f} ms")
        return frozen

    def _mark_stale(self, frozen, reason, accepted):
        frozen["stale"] = True
        frozen["stale_reason"] = reason
        self.store.json(f"P{self.P}/frozen_{frozen['params_id']}.json", frozen)
        for res in accepted:
            self.store.mark_stale(res)
            self.stale_blocks.append(res.block_id)
        self.log(f"  [P={self.P}] {frozen['params_id']} STALE: {reason}")

    def _check_calibration(self, frozen):
        return self.calibration_check(frozen) if self.calibration_check else None

    # -- acceptance ----------------------------------------------------------
    def _accept(self, frozen):
        hot, cool = wl.make_job_specs(frozen)
        accepted = []
        for i, n in enumerate(self.prereg["accept_blocks"][str(self.P)]):
            reason = self._check_calibration(frozen)
            if reason:
                self._mark_stale(frozen, reason, accepted)
                return None
            accepted.append(self._run_valid("acceptance", f"A{i + 1}",
                                            pair_plan(n, hot, cool),
                                            frozen["params_id"], record_units=True))
        reason = self._check_calibration(frozen)
        if reason:
            self._mark_stale(frozen, reason, accepted)
            return None
        return accepted

    # -- run -----------------------------------------------------------------
    def run(self) -> dict:
        p, key = self.prereg, str(self.P)
        t_u, o = self.v0["t_u_ms"][key], self.v0["overhead_ms"][key]
        u_h = wl.hot_units(p["l_target_ms"], o, t_u)
        u_c = wl.cool_units(u_h, p["d_cool"])
        base = {"P": self.P, "n_b": self.v0["n_b"], "u_h": u_h, "u_c": u_c,
                "S0_ms": wl.initial_sleep_ms(u_h, u_c, t_u), "t_u_ms": t_u,
                "overhead_ms": o}
        out = {"P": self.P, "base": base}
        try:
            while True:
                frozen = self._calibrate(base)
                self.current = frozen
                blocks = self._accept(frozen)
                if blocks is not None:
                    break
                self.current = None
        except (RerunsExhausted, CooldownCap) as e:
            out.update(status=INCOMPLETE, reason=str(e))
            return self._finish(out)
        try:
            ev = evaluate_config(self.P, [b.as_dict() for b in blocks], p)
        except SampleCountError as e:
            out.update(status=INCOMPLETE, reason=str(e))
            return self._finish(out)
        out.update(status=ev["status"], frozen=self.current, evaluation=ev,
                   acceptance_blocks=[b.block_id for b in blocks])
        return self._finish(out)

    def _finish(self, out):
        out.update(reruns_used=self.budget.used, history=self.history,
                   frozen_versions=self.frozen_versions,
                   stale_blocks=self.stale_blocks)
        if out["status"] == INCOMPLETE:
            out["note"] = "INCOMPLETE: not classified; no decision statistics"
        return out

    def partial(self, reason: str) -> dict:
        """State so far, for a run interrupted in this configuration."""
        return self._finish({"P": self.P, "status": INCOMPLETE, "reason": reason})


# ---------------------------------------------------------------------------
# Hardware executor
# ---------------------------------------------------------------------------
def _quick_read(backend):
    """Temperature and frequency from sysfs (no vcgencmd), for blocks with
    the 5 Hz logger off."""
    try:
        with open(backend.thermal) as f:
            t = int(f.read().strip()) / 1000.0
        with open(backend.freq) as f:
            fr = int(f.read().strip()) / 1000.0
        return {"temp_c": t, "freq_mhz": fr, "throttled": 0}
    except (AttributeError, OSError, ValueError):
        r = backend.read()
        return {"temp_c": r.get("temp_c"), "freq_mhz": r.get("freq_mhz"),
                "throttled": 0}


class HardwareExecutor:
    def __init__(self, backend, idle_baseline_c, prereg: dict = PREREG,
                 kernel: str = "gemm", log=print, poll_s: float = 2.0):
        self.b, self.prereg, self.kernel, self.log = backend, prereg, kernel, log
        self.idle = idle_baseline_c
        self.poll_s = poll_s
        self.pools = {}

    def pool(self, P):
        p = self.pools.get(P)
        if p is None or not p.alive():
            if p is not None:
                p.stop()
            p = self.pools[P] = wl.FixedWorkPool(
                P, kernel=self.kernel, timeout_s=self.prereg["job_timeout_s"])
        return p

    def reset_pool(self, P):
        p = self.pools.pop(P, None)
        if p is not None:
            p.stop()

    def close(self):
        for P in list(self.pools):
            self.reset_pool(P)

    def cooldown(self):
        target = self.idle + self.prereg["idle_margin_c"]
        t0 = time.monotonic()
        while True:
            t = self.b.read().get("temp_c")
            if t is not None and t <= target:
                return t
            if time.monotonic() - t0 > self.prereg["cooldown_cap_s"]:
                raise CooldownCap(f"did not cool to {target:.1f} C within "
                                  f"{self.prereg['cooldown_cap_s']:.0f} s (at {t} C)")
            time.sleep(self.poll_s)

    def execute(self, block_id, phase, position, plan, params_id,
                telemetry_on=True, record_units=False) -> BlockResult:
        # Start the needed pools BEFORE the cooldown, so process start-up
        # heat is never inside a block that counts as a cold start.
        try:
            for P in sorted({i["spec"].P for i in plan}):
                self.pool(P)
        except (wl.WorkerFailure, OSError) as e:
            reason = f"pool start failed: {type(e).__name__}: {e}"
            self.log(f"  {block_id}: INVALID - {reason}")
            return BlockResult(block_id, phase, position, False, reason, [],
                               [], None, None, params_id, telemetry_on)
        start_temp = self.cooldown()
        th0 = self.b.read().get("throttled")
        jobs, reason, tele = [], None, []
        if th0 is None:
            reason = "throttle flags unreadable"
        elif th0 & LIVE_FLAG_MASK:
            reason = f"live flags set before block ({hex(th0)})"
        log = runner.TelemetryLogger(self.b, hz=self.prereg["telemetry_hz"]) \
            if telemetry_on else None
        seen = 0
        if reason is None:
            if log:
                log.start()
            try:
                for item in plan:
                    spec = item["spec"]
                    rec = self.pool(spec.P).run_job(spec, record_units=record_units)
                    rec.update(pair=item.get("pair"), pos=item.get("pos"),
                               block=block_id, seq=len(jobs))
                    jobs.append(rec)
                    if log:
                        new = log.samples[seen:]
                        seen += len(new)
                        checks = new
                    else:
                        checks = [_quick_read(self.b)]
                    for s in checks:
                        reason = sample_violation(s, self.prereg)
                        if reason:
                            break
                    if reason:
                        break
            except (wl.JobTimeout, wl.WorkerFailure) as e:
                reason = f"{type(e).__name__}: {e}"
                for P in {i["spec"].P for i in plan}:
                    self.reset_pool(P)
            finally:
                if log:
                    tele = log.stop()
            if reason is None:
                for s in tele:
                    reason = sample_violation(s, self.prereg)
                    if reason:
                        break
        th1 = self.b.read().get("throttled")
        if reason is None:
            reason = sticky_violation(th0, th1)
        if reason is None and len(jobs) != len(plan):
            reason = f"only {len(jobs)} of {len(plan)} jobs ran"
        res = BlockResult(block_id, phase, position, reason is None, reason, jobs,
                          tele, th0, th1, params_id, telemetry_on)
        temps = [s.get("temp_c") for s in tele if s.get("temp_c") is not None]
        self.log(f"  {block_id}: {'valid' if res.valid else 'INVALID'} "
                 f"start {start_temp:.1f} C, peak {max(temps) if temps else '-'} C, "
                 f"{len(jobs)} jobs" + (f" - {reason}" if reason else ""))
        return res


# ---------------------------------------------------------------------------
# V0 and V4
# ---------------------------------------------------------------------------
def run_v0(execute, store: Store, prereg: dict = PREREG, log=print) -> dict:
    budget = Budget(prereg["v0_reruns"])

    def valid(position, plan, record):
        while True:
            n = budget.claim("v0", position)
            res = execute(f"V0{position}-e{n}", "v0", position, plan, None, True, record)
            store.block(res)
            if res.valid:
                return res

    try:
        a = valid("a", [{"spec": wl.JobSpec("UNIT", 1, n_b, prereg["v0a_units"])}
                        for n_b in prereg["unit_sizes"]
                        for _ in range(prereg["v0a_jobs"])], True)
        sizes = analyze_unit_sizes(a.jobs, prereg)
        n_b = choose_unit_size(sizes)
        if n_b is None:
            return {"status": INCOMPLETE, "reason": "no unit size qualifies "
                    "(median >= 2 ms and CV <= 2%)", "sizes": sizes}
        b = valid("b", [{"spec": wl.JobSpec("UNIT", 4, n_b, prereg["v0b_units"])}
                        for _ in range(prereg["v0b_jobs"])], True)
        u4 = [u for j in b.jobs for u in j["unit_ms"]]
        per_core = {}
        for j in b.jobs:
            for s in j["shards"]:
                per_core.setdefault(str(s["core"]), []).append(
                    s["compute_ms"] / max(1, s["units_done"]))
        c = valid("c", [{"spec": wl.JobSpec("ZERO", P, n_b, 0)}
                        for P in (1, 4) for _ in range(prereg["v0c_jobs"])], False)
        lat = {P: [j["latency_ms"] for j in c.jobs if j["P"] == P] for P in (1, 4)}
        skew = [j["skew_ms"] for j in c.jobs if j["P"] == 4]
    except (RerunsExhausted, CooldownCap) as e:
        return {"status": INCOMPLETE, "reason": str(e), "reruns_used": budget.used}
    t1 = sizes[str(n_b)]["t_u_ms"]
    t4 = float(np.median(u4))
    core_med = {k: float(np.median(v)) for k, v in per_core.items()}
    return {"status": "DONE", "n_b": n_b, "sizes": sizes,
            "t_u_ms": {"1": t1, "4": t4}, "cv_4": _cv(u4),
            "slowdown_4_vs_1": t4 / t1,
            "core_unit_ms": core_med,
            "slowest_core": max(core_med, key=core_med.get),
            "overhead_ms": {"1": float(np.median(lat[1])),
                            "4": float(np.median(lat[4]))},
            "skew_ms_median_4": float(np.median(skew)),
            "reruns_used": budget.used}


def run_v4(execute, frozen_p1: dict, store: Store, prereg: dict = PREREG) -> dict:
    """Telemetry off then on, frozen P=1 parameters. DESCRIPTIVE ONLY."""
    if not frozen_p1 or frozen_p1.get("stale"):
        return {"status": "NOT_AVAILABLE", "reason": "no usable frozen P=1 parameters",
                "label": V4_LABEL}
    hot, cool = wl.make_job_specs(frozen_p1)
    plan = pair_plan(prereg["v4_pairs"], hot, cool)
    budget = Budget(prereg["v4_reruns"])
    got = {}
    try:
        for pos, tel in (("off", False), ("on", True)):
            while True:
                n = budget.claim("v4", pos)
                res = execute(f"V4{pos}-e{n}", "v4", pos, plan,
                              frozen_p1["params_id"], tel, False)
                store.block(res)
                if res.valid:
                    got[pos] = res
                    break
    except (RerunsExhausted, CooldownCap) as e:
        return {"status": "NOT_AVAILABLE", "reason": str(e), "label": V4_LABEL}
    return {"status": "DONE", **describe_v4(got["off"].jobs, got["on"].jobs, prereg)}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
HASHED_FILES = ["validate_workload.py", "workload.py",
                "PREREG_workload_validation.md", "runner.py", "sensors.py"]


def file_hashes(base=None) -> dict:
    base = base or os.path.dirname(os.path.abspath(__file__))
    out = {}
    for f in HASHED_FILES:
        p = os.path.join(base, f)
        with open(p, "rb") as fh:
            out[f] = hashlib.sha256(fh.read()).hexdigest()
    return out


def new_report() -> dict:
    return {"overall": INCOMPLETE, "v0": None, "v4": None,
            "configs": {"1": {"status": NOT_RUN}, "4": {"status": NOT_RUN}}}


def run_all(execute, store: Store, prereg: dict = PREREG, log=print,
            report: dict | None = None, checkpoint=None) -> dict:
    """V0 -> V1 (P=1) -> V2 (P=4) -> V4.

    Fills `report` as each part finishes and calls `checkpoint()` after each,
    so finished results survive an interruption. On KeyboardInterrupt
    (Ctrl+C, or SIGTERM via the handler in main) the part in progress is
    recorded as INCOMPLETE ("interrupted"), the report is marked interrupted,
    and the interrupt is re-raised."""
    report = new_report() if report is None else report
    save = checkpoint or (lambda: None)
    phase, runs = "V0", {}
    try:
        v0 = run_v0(execute, store, prereg, log)
        report["v0"] = v0
        save()
        if v0["status"] != "DONE":
            report["v4"] = {"status": "NOT_AVAILABLE", "reason": "V0 incomplete",
                            "label": V4_LABEL}
            report["overall"] = INCOMPLETE
            save()
            return report
        for P in (1, 4):
            phase = f"P{P}"
            log(f"[validate] configuration P={P}")
            runs[P] = ConfigRun(P, v0, execute, store, prereg, log=log)
            report["configs"][str(P)] = runs[P].run()
            save()
            if report["configs"][str(P)]["status"] == INCOMPLETE:
                # Rerun limit or cooldown cap: validation stops here and reports.
                report["v4"] = {"status": "NOT_AVAILABLE", "label": V4_LABEL,
                                "reason": f"validation stopped: P={P} INCOMPLETE"}
                report["overall"] = INCOMPLETE
                save()
                return report
        phase = "V4"
        report["v4"] = run_v4(execute, runs[1].current, store, prereg)
        report["overall"] = "COMPLETE"
        save()
        return report
    except KeyboardInterrupt as e:
        why = f"interrupted ({e or 'KeyboardInterrupt'}) during {phase}"
        if phase == "V0":
            report["v0"] = {"status": INCOMPLETE, "reason": why}
        elif phase == "V4":
            report["v4"] = {"status": "NOT_AVAILABLE", "reason": why, "label": V4_LABEL}
        else:
            P = int(phase[1:])
            report["configs"][str(P)] = runs[P].partial(why)
        if report.get("v4") is None:
            report["v4"] = {"status": "NOT_AVAILABLE", "reason": why, "label": V4_LABEL}
        report["overall"] = INCOMPLETE
        report["interrupted"] = why
        save()
        raise


def _blas_libs():
    try:
        with open("/proc/self/maps") as f:
            return sorted({ln.split()[-1] for ln in f
                           if ".so" in ln.split()[-1]
                           and any(k in ln.lower() for k in ("blas", "lapack"))})
    except OSError:
        return None


def _sigterm(signum, frame):
    raise KeyboardInterrupt("SIGTERM")     # same cleanup path as Ctrl+C


def _summary(report) -> str:
    lines = [f"WORKLOAD VALIDATION {report.get('run_id')}: {report['overall']}"]
    for k in ("reason", "interrupted"):
        if report.get(k):
            lines.append(f"{k}: {report[k]}")
    v0 = report["v0"] or {}
    lines.append(f"V0: {v0.get('status')}  n_b={v0.get('n_b')}  "
                 f"t_u={v0.get('t_u_ms')}  o={v0.get('overhead_ms')}")
    for k in ("1", "4"):
        c = report["configs"][k]
        line = f"P={k}: {c['status']}"
        if c.get("evaluation"):
            g = c["evaluation"]["gap"]
            line += (f"  gap {g['g_hat']:+.4f} [{g['ci_lo']:+.4f}, {g['ci_hi']:+.4f}]"
                     f" {g['class']}  criteria {c['evaluation']['criteria']}")
        if c.get("reason"):
            line += f"  ({c['reason']})"
        lines.append(line)
    lines.append(f"Statistics limitation: {LIMITATION}")
    v4 = report.get("v4") or {}
    lines.append(f"V4 ({V4_LABEL}): {v4.get('status')}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true",
                    help="print the pre-registered plan and exit (no load)")
    ap.add_argument("--outdir", default="results/workload_validation")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--backend", default=None)
    args = ap.parse_args(argv)
    if args.plan:
        print(json.dumps({"prereg": PREREG, "sha256": file_hashes()}, indent=1))
        return 0
    b = sensors.get_backend(args.backend)
    if b.name == "mock":
        print("validate_workload.py needs real hardware; use --plan to inspect.")
        return 2
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
    root = os.path.join(args.outdir, run_id)
    if os.path.exists(root):
        print(f"{root} exists; refusing to overwrite")
        return 2
    store = Store(root)
    store.json("prereg.json", {"prereg": PREREG, "sha256": file_hashes(),
                               "run_id": run_id,
                               "written": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "host": platform.node(), "python": sys.version,
                               "numpy": np.__version__, "blas_libs": _blas_libs(),
                               "backend": b.name, "pid": os.getpid()})
    old_handlers = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    signal.signal(signal.SIGTERM, _sigterm)
    report = new_report()
    report.update(run_id=run_id, pid=os.getpid())
    ex, rc = None, 0
    try:
        print(f"[validate] run {run_id}: measuring idle baseline "
              f"({PREREG['idle_window_s']:.0f} s). Start only after >= 15 min idle.")
        vals, end = [], time.monotonic() + PREREG["idle_window_s"]
        while time.monotonic() < end:
            t = b.read().get("temp_c")
            if t is not None:
                vals.append(t)
            time.sleep(0.5)
        if not vals:
            # No baseline: refuse before any worker pool exists (no NaN target).
            report["reason"] = ("refused: no valid temperature readings in the "
                                f"{PREREG['idle_window_s']:.0f} s idle window")
            rc = 2
            return rc
        idle = float(np.median(vals))
        report.update(idle_baseline_c=idle,
                      idle_window=[min(vals), max(vals), len(vals)])
        ex = HardwareExecutor(b, idle)
        run_all(ex.execute, store, report=report,
                checkpoint=lambda: store.json("report.json", report))
    except KeyboardInterrupt as e:
        report["overall"] = INCOMPLETE
        report.setdefault("interrupted", f"interrupted ({e or 'KeyboardInterrupt'})")
        rc = 130
    finally:
        # A second signal must not abort the cleanup or the report write.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if ex is not None:
            ex.close()
        report.update(prereg_sha256=file_hashes(), statistics_limitation=LIMITATION,
                      finished=time.strftime("%Y-%m-%d %H:%M:%S"))
        store.json("report.json", report)
        with open(os.path.join(root, "report.txt"), "w") as f:
            f.write(_summary(report) + "\n")
        print(_summary(report))
        signal.signal(signal.SIGTERM, old_handlers[0])   # cleanup done
        signal.signal(signal.SIGINT, old_handlers[1])
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
