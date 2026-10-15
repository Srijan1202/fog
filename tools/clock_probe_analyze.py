#!/usr/bin/env python3
"""Apply the PRE-SPECIFIED decision rule of INSTRUMENT_CHECK_clock_probe.md to a
clock_probe_sampler.sh CSV. Read-only; prints a JSON summary and the verdict.

    python3 tools/clock_probe_analyze.py <clock.csv>

Rule (fixed before any data, see the .md):
  active    = samples whose throttled flags have bit 3 set (soft temperature limit now)
  drop(x)   = x < 0.98 * max(x over all valid samples)
  INCONCLUSIVE  fewer than MIN_ACTIVE active samples, or more than MAX_MISSING of rows
                with any unreadable value
  BLIND_SPOT    share of active samples with drop(arm_clock) >= MIN_DROP_SHARE, and
                scaling_cur_freq shows no drop in any sample
  NO_CLOCK_DROP no active sample has drop(arm_clock)
  MIXED         anything else (reported, no conclusion drawn)
"""
import csv
import json
import sys

MIN_ACTIVE = 30
MAX_MISSING = 0.05
MIN_DROP_SHARE = 0.20
DROP = 0.98


def analyze(path):
    rows = list(csv.DictReader(open(path, newline="")))
    n = len(rows)
    valid, missing = [], 0
    for r in rows:
        try:
            v = {"t": float(r["epoch"]), "temp_mC": int(r["temp_mC"]),
                 "sysfs_kHz": int(r["scaling_cur_kHz"]), "arm_Hz": int(r["arm_clock_Hz"]),
                 "th": int(r["throttled"], 16)}
        except (ValueError, KeyError, TypeError):
            missing += 1
            continue
        valid.append(v)
    out = {"rows": n, "valid": len(valid), "missing": missing}
    if n == 0 or missing / n > MAX_MISSING or not valid:
        out.update(verdict="INCONCLUSIVE", reason=f"missing/unreadable rows {missing}/{n} > {MAX_MISSING:.0%}")
        return out
    arm_max = max(v["arm_Hz"] for v in valid)
    sys_max = max(v["sysfs_kHz"] for v in valid)
    active = [v for v in valid if v["th"] & 0x8]
    arm_drop_active = [v for v in active if v["arm_Hz"] < DROP * arm_max]
    sys_drop_any = [v for v in valid if v["sysfs_kHz"] < DROP * sys_max]
    out.update(arm_max_Hz=arm_max, arm_min_Hz=min(v["arm_Hz"] for v in valid),
               sysfs_max_kHz=sys_max, sysfs_min_kHz=min(v["sysfs_kHz"] for v in valid),
               active_samples=len(active), active_with_arm_drop=len(arm_drop_active),
               sysfs_drop_samples=len(sys_drop_any),
               temp_max_mC=max(v["temp_mC"] for v in valid),
               first_active_temp_mC=active[0]["temp_mC"] if active else None,
               arm_min_while_active_Hz=min((v["arm_Hz"] for v in active), default=None))
    if len(active) < MIN_ACTIVE:
        out.update(verdict="INCONCLUSIVE", reason=f"only {len(active)} samples with the soft limit active (< {MIN_ACTIVE})")
    elif not arm_drop_active:
        out.update(verdict="NO_CLOCK_DROP", reason="the firmware ARM clock never dropped while the soft limit was active")
    elif len(arm_drop_active) / len(active) >= MIN_DROP_SHARE and not sys_drop_any:
        out.update(verdict="BLIND_SPOT", reason=(f"ARM clock below {DROP:.0%} of max in {len(arm_drop_active)}/{len(active)} "
                                                 "active samples while scaling_cur_freq never dropped"))
    else:
        out.update(verdict="MIXED", reason="clock drops present but the BLIND_SPOT conditions are not all met")
    return out


if __name__ == "__main__":
    print(json.dumps(analyze(sys.argv[1]), indent=1))
