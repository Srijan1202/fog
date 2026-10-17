#!/usr/bin/env python3
"""
DIAGNOSTIC (non-confirmatory): what interrupts four-core fixed-work jobs?

DESIGN_exp2_exp3_4core.md, section 3a. In workload validation wv-20261015-01,
P=4 failed A6 on rare long compute units (0.58 % of units > 1.5x the median
carried 74 % of the variance; 21 involuntary context switches per job vs 2 at
P=1). This runs the frozen P4-F1 HOT job (P=4, from that validation's
report.json) under three telemetry conditions, interleaved T0 T1 T2 T2 T1 T0:

  T0  no telemetry
  T1  sysfs temperature only, 5 Hz (a file read, no process start)
  T2  the validation's telemetry: sensors backend at 5 Hz (sysfs temperature
      and frequency + a vcgencmd get_throttled process per sample)

Every unit time and every job's involuntary context switches are recorded.
No acceptance decision uses this; it only informs the next configuration
(P4b), which is then pre-registered and validated on the unchanged criteria.

    python3 diag_p4_preemption.py \
        --report results/workload_validation/wv-20261015-01/report.json \
        --outdir results/diag-p4-<date>-01
    python3 diag_p4_preemption.py --backend mock --report <report> --outdir <dir>

Safety (real hardware): refuses before any load unless the governor is
performance on all cores, the throttle flags are 0x0, the temperature is
below --max-start-temp and load1 is below 0.5. Before every job it reads the
sysfs temperature and stops at --max-temp (default 70 C); total load is
capped by --max-load-s. The worker pool stops with at most one SIGTERM per
worker, never SIGKILL. SIGTERM / Ctrl+C stop the run and the data so far is
saved, marked interrupted. It changes no setting. The mock backend skips the
hardware checks and is for tests only.

Outputs: <outdir>/diag.{meta.json,jobs.jsonl,telemetry.jsonl}
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import signal
import time

import numpy as np

import runner
import sensors
import workload as wl

THERMAL = "/sys/class/thermal/thermal_zone0/temp"
GOVERNORS = [f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_governor" for c in range(4)]
ORDER = ("T0", "T1", "T2", "T2", "T1", "T0")
CONDITIONS = {"T0": "no telemetry",
              "T1": "sysfs temperature only, 5 Hz",
              "T2": "validation telemetry (sensors backend, 5 Hz, vcgencmd get_throttled)"}
SLOW = 1.5                      # a unit is "long" above SLOW x the median


def read_sysfs_temp(path=THERMAL):
    try:
        with open(path) as f:
            return int(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


class SysfsTemp:
    """T1 telemetry source: the temperature file only, no subprocess."""
    name = "sysfs_temp"

    def __init__(self, path=THERMAL):
        self.path = path

    def read(self):
        return {"t_wall": time.monotonic(), "temp_c": read_sysfs_temp(self.path)}


def load_p4_spec(report_path):
    """HOT JobSpec of the frozen P=4 parameters in a validation report."""
    with open(report_path) as f:
        rep = json.load(f)
    fr = ((rep.get("configs") or {}).get("4") or {}).get("frozen") or {}
    if not all(k in fr for k in ("P", "n_b", "u_h")) or fr["P"] != 4:
        raise SystemExit(f"no frozen P=4 parameters in {report_path}")
    return wl.JobSpec("HOT", 4, int(fr["n_b"]), int(fr["u_h"]), 0.0), {
        "validation_run": rep.get("run_id"), "params_id": fr.get("params_id"),
        "status": ((rep.get("configs") or {}).get("4") or {}).get("status")}


def summarize(jobs, warmup):
    """Descriptive unit and job statistics, first `warmup` jobs excluded."""
    js = jobs[warmup:]
    units = np.array([u for j in js for u in (j.get("unit_ms") or [])], float)
    lat = np.array([j["latency_ms"] for j in js], float)
    out = {"jobs": len(js), "units": int(units.size),
           "units_complete": all(j["units_done"] == j["units_planned"] for j in js)}
    if units.size:
        med = float(np.median(units))
        slow = units > SLOW * med
        var = float(((units - units.mean()) ** 2).sum())
        out.update(unit_median_ms=med, unit_cv=float(units.std() / units.mean()),
                   unit_robust_cv=float(1.4826 * np.median(np.abs(units - med)) / med),
                   share_long=float(slow.mean()),
                   var_share_long=(float(((units[slow] - units.mean()) ** 2).sum()) / var
                                   if var > 0 else 0.0))
    if lat.size:
        out.update(latency_median_ms=float(np.median(lat)),
                   latency_cv=float(lat.std() / lat.mean()),
                   nivcsw_median=float(np.median([j.get("nivcsw") or 0 for j in js])))
    return out


def hw_precheck(b, max_start_temp):
    """Reason not to start load on real hardware, or None."""
    for g in GOVERNORS:
        try:
            with open(g) as f:
                gov = f.read().strip()
        except OSError:
            return f"governor unreadable ({g})"
        if gov != "performance":
            return f"governor is {gov!r}, not performance ({g})"
    th = b.read().get("throttled")
    if th is None:
        return "throttle flags unreadable"
    if th != 0:
        return f"throttle flags {hex(th)} (need 0x0)"
    t = read_sysfs_temp()
    if t is None or t >= max_start_temp:
        return f"temperature {t} C (need < {max_start_temp} C)"
    if os.getloadavg()[0] >= 0.5:
        return f"load1 {os.getloadavg()[0]:.2f} (need < 0.5)"
    return None


def _sigterm(signum, frame):
    raise KeyboardInterrupt("SIGTERM")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None)
    ap.add_argument("--report", required=True,
                    help="workload validation report.json with frozen P=4 parameters")
    ap.add_argument("--outdir", required=True, help="fresh directory (never overwritten)")
    ap.add_argument("--jobs-per-block", type=int, default=40)
    ap.add_argument("--warmup", type=int, default=5, help="jobs excluded per block")
    ap.add_argument("--max-temp", type=float, default=70.0)
    ap.add_argument("--max-start-temp", type=float, default=55.0)
    ap.add_argument("--max-load-s", type=float, default=180.0)
    ap.add_argument("--hz", type=float, default=5.0)
    args = ap.parse_args(argv)
    if args.warmup >= args.jobs_per_block:
        ap.error("--warmup must be smaller than --jobs-per-block")

    b = sensors.get_backend(args.backend)
    is_mock = b.name == "mock"
    spec, src = load_p4_spec(args.report)
    print(f"[diag] DIAGNOSTIC, not confirmatory. backend={b.name} job={spec} "
          f"(validation {src['validation_run']} {src['params_id']}, status {src['status']})")
    if not is_mock:
        why = hw_precheck(b, args.max_start_temp)
        if why:
            print(f"refused before any load: {why}")
            return 2
    try:
        os.makedirs(args.outdir)
    except FileExistsError:
        print(f"refused: {args.outdir} exists; refusing to overwrite")
        return 2
    temp = (lambda: 45.0) if is_mock else read_sysfs_temp
    signal.signal(signal.SIGTERM, _sigterm)

    jobs, tele, blocks = [], [], []
    stop_reason = interrupted = None
    pool = log = None
    t_load = None
    try:
        pool = wl.FixedWorkPool(4)
        t_load = time.monotonic()
        for bi, cond in enumerate(ORDER):
            src_t = {"T0": None, "T1": SysfsTemp(), "T2": b}[cond]
            if is_mock and cond == "T1":
                src_t = b
            log = runner.TelemetryLogger(src_t, hz=args.hz) if src_t is not None else None
            if log:
                log.start()
            block = []
            try:
                for i in range(args.jobs_per_block):
                    t = temp()                       # between jobs, outside the latency
                    if t is None:
                        stop_reason = "temperature unreadable"
                    elif t >= args.max_temp:
                        stop_reason = f"max_temp: {t:.1f} C >= {args.max_temp} C"
                    elif time.monotonic() - t_load > args.max_load_s:
                        stop_reason = f"max_load_s {args.max_load_s:.0f} s reached"
                    if stop_reason:
                        break
                    rec = pool.run_job(spec, record_units=True)
                    rec.update(block=bi, condition=cond, seq=i, temp_before_c=t)
                    block.append(rec)
            finally:                                 # keep a partial block too
                if log:
                    for s in log.stop():
                        tele.append({**s, "block": bi, "condition": cond})
                    log = None
                jobs += block
                blocks.append({"block": bi, "condition": cond, "jobs_run": len(block),
                               **summarize(block, args.warmup)})
            print(f"  block {bi} {cond}: {len(block)} jobs, unit CV "
                  f"{blocks[-1].get('unit_cv', float('nan')):.4f}, long units "
                  f"{blocks[-1].get('share_long', float('nan')):.4f}, nivcsw median "
                  f"{blocks[-1].get('nivcsw_median')}", flush=True)
            if stop_reason:
                break
    except (wl.WorkerFailure, wl.JobTimeout) as e:
        stop_reason = f"{type(e).__name__}: {e}"
    except KeyboardInterrupt as e:
        interrupted = f"interrupted ({e or 'KeyboardInterrupt'})"
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if log:
            log.stop()
        unstopped = []
        if pool is not None:
            pool.stop()
            unstopped = list(pool.unstopped)

    by_cond = {c: summarize([j for j in jobs if j["condition"] == c and
                             j["seq"] >= args.warmup], 0) for c in CONDITIONS}
    meta = {"experiment": "diag_p4_preemption", "label": "DIAGNOSTIC - not confirmatory",
            "backend": b.name, "mock": is_mock, "job": dataclasses.asdict(spec), "source": src,
            "order": list(ORDER), "conditions": CONDITIONS,
            "jobs_per_block": args.jobs_per_block, "warmup": args.warmup,
            "max_temp_c": args.max_temp, "max_load_s": args.max_load_s,
            "load_s": round(time.monotonic() - t_load, 3) if t_load else 0.0,
            "stop_reason": stop_reason, "interrupted": interrupted,
            "complete": stop_reason is None and interrupted is None and len(blocks) == len(ORDER),
            "unstopped_workers": unstopped, "blocks": blocks, "by_condition": by_cond,
            "temp_max_c": max((j["temp_before_c"] for j in jobs), default=None)}
    for j in jobs:
        j.pop("shards", None)
    runner.save_run(args.outdir, "diag", meta, jobs, tele)

    print("\nDIAGNOSTIC SUMMARY (descriptive; first "
          f"{args.warmup} jobs of each block excluded)")
    for c, s in by_cond.items():
        if s.get("units"):
            print(f"  {c} {CONDITIONS[c]}: jobs {s['jobs']}, unit CV {s['unit_cv']:.4f}, "
                  f"robust CV {s['unit_robust_cv']:.4f}, long units {s['share_long']:.4f} "
                  f"(variance share {s['var_share_long']:.2f}), nivcsw median "
                  f"{s['nivcsw_median']}, latency CV {s['latency_cv']:.4f}")
    if stop_reason or interrupted:
        print(f"  STOPPED EARLY: {stop_reason or interrupted}")
    if unstopped:
        print(f"  INTERVENTION REQUIRED: workers still alive after one SIGTERM: {unstopped}")
    print(f"wrote {args.outdir}/diag.*")
    return 130 if interrupted else (1 if unstopped else 0)


if __name__ == "__main__":
    raise SystemExit(main())
