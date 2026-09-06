#!/usr/bin/env python3
"""
EXPERIMENT 0 - hardware capability probe.

Run this FIRST. It tells you which sensor backend you get, whether the
governor is pinned, and whether the pilot is instrumentable at all.

    python3 probe.py
    python3 probe.py --backend mock     # validate pipeline without hardware
"""

from __future__ import annotations

import argparse
import glob
import os
import platform
import shutil
import subprocess
import time

import sensors


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def governor_report():
    if os.name == "nt":
        return {"available": False, "windows": True,
                "note": "Windows: use a power plan, not a cpufreq governor"}
    govs = sorted(glob.glob(
        "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"))
    if not govs:
        return {"available": False, "note": "no cpufreq sysfs (VM/container?)"}
    vals = {}
    for g in govs:
        cpu = g.split("/")[-3]
        vals[cpu] = _read(g)
    uniq = set(v for v in vals.values() if v)
    avail = _read(
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors")
    return {
        "available": True,
        "per_cpu": vals,
        "uniform": len(uniq) == 1,
        "current": list(uniq)[0] if len(uniq) == 1 else None,
        "available_governors": avail,
        "pinned_to_performance": uniq == {"performance"},
    }


def cpu_model():
    if platform.system() == "Linux":
        info = _read("/proc/cpuinfo") or ""
        for line in info.splitlines():
            if "model name" in line or "Model" in line:
                return line.split(":", 1)[-1].strip()
        dt = _read("/proc/device-tree/model")
        if dt:
            return dt.replace("\x00", "")
    return platform.processor() or platform.machine()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None,
                    help="force a backend (raspberrypi|linux_cpu|nvidia|mock)")
    ap.add_argument("--settle", type=float, default=5.0,
                    help="idle settle seconds before reading idle temp")
    args = ap.parse_args()

    print("=" * 68)
    print("EXPERIMENT 0 - HARDWARE CAPABILITY PROBE")
    print("=" * 68)

    print(f"\nplatform     : {platform.platform()}")
    print(f"cpu/soc      : {cpu_model()}")
    print(f"cores        : {os.cpu_count()}")
    print(f"vcgencmd     : {shutil.which('vcgencmd') or 'not found'}")
    print(f"nvidia-smi   : {shutil.which('nvidia-smi') or 'not found'}")

    gov = governor_report()
    print("\n--- CPU governor / power plan ---")
    if gov.get("windows"):
        print("  Windows detected. Pin the CPU to a fixed performance state:")
        print("    powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR "
              "PROCTHROTTLEMIN 100")
        print("    powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR "
              "PROCTHROTTLEMAX 100")
        print("    powercfg /setactive SCHEME_CURRENT")
        print("  Also: plug in the charger (battery mode caps clocks), and")
        print("  close everything else. Windows background noise is the main")
        print("  reason Windows is a weak falsification platform.")
    elif not gov["available"]:
        print(f"  {gov['note']}")
        print("  WARNING: cannot pin governor. If this is a VM or container,")
        print("           thermal results will NOT be meaningful.")
    else:
        print(f"  current   : {gov['current']}")
        print(f"  available : {gov['available_governors']}")
        if gov["pinned_to_performance"]:
            print("  OK: pinned to performance.")
        else:
            print("  ACTION REQUIRED: pin all cores to 'performance' before")
            print("    Experiments 1-3, otherwise DVFS confounds thermal")
            print("    throttling and the whole pilot is uninterpretable:")
            print("    sudo sh -c 'for f in /sys/devices/system/cpu/cpu*/"
                  "cpufreq/scaling_governor; do echo performance > $f; done'")

    print("\n--- sensor backend ---")
    try:
        b = sensors.get_backend(args.backend)
    except SystemExit as e:
        print(f"  {e}")
        return 2
    print(f"  selected  : {b.name}")
    for k, v in b.caps.items():
        print(f"    {k:16s}: {v}")

    print(f"\n--- live read (settling {args.settle}s idle) ---")
    time.sleep(args.settle)
    r = b.read()
    for k, v in r.items():
        print(f"  {k:12s}: {v}")

    print("\n--- GO / NO-GO on instrumentation ---")
    blockers, warnings = [], []
    if r.get("temp_c") is None:
        blockers.append("no temperature sensor -> pilot cannot run")
    if r.get("freq_mhz") is None and r.get("throttled") is None:
        blockers.append("no frequency AND no throttle flag -> cannot detect "
                        "the knee (P1/P4 unmeasurable)")
    if gov["available"] and not gov["pinned_to_performance"]:
        warnings.append("governor not pinned to performance")
    if b.name == "mock":
        warnings.append("MOCK BACKEND - simulated data, never report as result")
    if b.name == "nvidia":
        warnings.append("NVIDIA: datacenter/Colab GPUs are actively cooled and "
                        "often will not reach the knee under realistic load")
    if os.name == "nt" and b.name == "mock":
        blockers.append(
            "Windows with no sensor source. Install LibreHardwareMonitor, run "
            "it AS ADMIN, tick Options -> WMI Provider, leave it running, then "
            "'pip install wmi pywin32'. If you have an NVIDIA GPU, "
            "'pip install nvidia-ml-py' and use --backend nvidia instead.")
    if b.name == "windows_lhm":
        warnings.append("Windows is a WEAK falsification platform: active "
                        "cooling may prevent reaching the knee. A P4 failure "
                        "here is NOT evidence against TDAS - it means the "
                        "device is unmeasurable. Confirm on a Pi.")
        if getattr(b, "mode", None) == "powershell":
            warnings.append("PowerShell fallback is slow (~300ms/read). "
                            "Use --hz 2 for telemetry.")

    if blockers:
        print("  NO-GO. Blockers:")
        for x in blockers:
            print(f"    - {x}")
        print("\n  A device without temp + (freq or throttle flags) cannot")
        print("  falsify TDAS. Use a Raspberry Pi-class board if possible.")
    else:
        print("  GO on instrumentation. Proceed to Experiment 1.")
    for x in warnings:
        print(f"  WARNING: {x}")

    print(f"\n  idle temperature: {r.get('temp_c')} C")
    print("  Record this. Cooldowns in later experiments target idle + 3C.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
