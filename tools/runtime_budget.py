#!/usr/bin/env python3
"""Worst-case runtime of one validate_workload.py run, recomputed from the repository SOURCES.

Every constant is read from the files (ast / regex) and asserted, so a later edit cannot
silently invalidate this calculation. Nothing is imported from the validator and nothing
is executed. Run:  python3 tools/runtime_budget.py [repository root]

The watchdog's MAX_RUN_S clock starts when the watchdog starts (launch.sh phase 5), i.e.
seconds before the validator's barrier is released; everything after that counts.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent   # repository root
vw_src = (ROOT / "validate_workload.py").read_text()
wl_src = (ROOT / "workload.py").read_text()
se_src = (ROOT / "sensors.py").read_text()
rn_src = (ROOT / "runner.py").read_text()
la_src = (ROOT / "tools" / "launch_validation.sh").read_text()
wd_src = (ROOT / "tools" / "validation_monitor.sh").read_text()


def prereg():
    tree = ast.parse(vw_src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "PREREG":
            return ast.literal_eval(node.value)
    raise SystemExit("PREREG not found")


def grab(pattern, text, what):
    m = re.search(pattern, text, re.M)
    if not m:
        raise SystemExit(f"constant not found: {what} /{pattern}/")
    return float(m.group(1))


P = prereg()
POLL_S = grab(r"poll_s: float = ([0-9.]+)", vw_src, "HardwareExecutor poll_s")
READY_S = grab(r"ready_timeout_s: float = ([0-9.]+)", wl_src, "FixedWorkPool ready_timeout_s")
STOP_S = grab(r"def stop\(self, timeout_s: float = ([0-9.]+)\)", wl_src, "FixedWorkPool.stop timeout")
TERM_JOIN_S = grab(r"p\.terminate\(\)\s+# SIGTERM, once\s+p\.join\(([0-9.]+)\)", wl_src, "join after SIGTERM")
VC_T1 = grab(r"p\.communicate\(timeout=([0-9.]+)\)\s+except subprocess\.TimeoutExpired:\s+p\.terminate", se_src, "vcgencmd timeout")
VC_T2 = grab(r"p\.terminate\(\)\s+try:\s+p\.communicate\(timeout=([0-9.]+)\)", se_src, "vcgencmd wait after SIGTERM")
JOIN_S = grab(r"self\._thread\.join\(timeout=([0-9.]+)\)", rn_src, "TelemetryLogger.stop join")
CAP = grab(r"^CAP=([0-9]+)$", la_src, "launch.sh CAP")
GRACE = grab(r"^GRACE=([0-9]+)$", la_src, "launch.sh GRACE")
BARRIER = grab(r"^BARRIER_S=([0-9]+)$", la_src, "launch.sh BARRIER_S")
ACK_POLLS = grab(r"for k in \$\(seq 1 ([0-9]+)\); do grep -qF \"\[validate\] monitoring verified:\"", la_src, "ack polls")
WD_INTERVAL = grab(r"^INTERVAL_S=([0-9]+)$", wd_src, "watchdog INTERVAL_S")
assert "calibration_check" not in re.search(r"runs\[P\] = ConfigRun\((.*)\)", vw_src).group(1), \
    "run_all passes calibration_check: the stale/recalibration loop becomes reachable; redo the budget"

R_MAX = VC_T1 + VC_T2              # one backend.read(): sysfs + vcgencmd hung to its limits
COOL_MAX = P["cooldown_cap_s"] + POLL_S + R_MAX   # last successful poll just inside the cap
JOB_MAX = P["job_timeout_s"]
POOL_STOP = {1: STOP_S + 1 * TERM_JOIN_S, 4: STOP_S + 4 * TERM_JOIN_S}

# ---- block plans, straight from PREREG (jobs per execution, pools needed) -------------
def phase(name, positions, reruns):
    """positions: list of (label, n_jobs, n_zero_jobs, pools). Reruns go to the most
    expensive position (worst case), executions = positions + reruns."""
    worst = max(positions, key=lambda p: (p[1], len(p[3])))
    return {"name": name, "execs": list(positions) + [worst] * reruns, "reruns": reruns}

ub, cp = P["unit_sizes"], P["calib_pairs"]
phases = [
    phase("V0", [("V0a", len(ub) * P["v0a_jobs"], 0, (1,)), ("V0b", P["v0b_jobs"], 0, (4,)),
                 ("V0c", 2 * P["v0c_jobs"], 2 * P["v0c_jobs"], (1, 4))], P["v0_reruns"]),
    phase("P1", [("C1", 2 * cp, 0, (1,)), ("C2", 2 * cp, 0, (1,))]
                + [(f"A{i+1}", 2 * n, 0, (1,)) for i, n in enumerate(P["accept_blocks"]["1"])],
          P["reruns_per_config"]),
    phase("P4", [("C1", 2 * cp, 0, (4,)), ("C2", 2 * cp, 0, (4,))]
                + [(f"A{i+1}", 2 * n, 0, (4,)) for i, n in enumerate(P["accept_blocks"]["4"])],
          P["reruns_per_config"]),
    phase("V4", [("off", 2 * P["v4_pairs"], 0, (1,)), ("on", 2 * P["v4_pairs"], 0, (1,))], P["v4_reruns"]),
]
execs = [e for ph in phases for e in ph["execs"]]
n_exec = len(execs)
n_jobs = sum(e[1] for e in execs)
n_zero = sum(e[2] for e in execs)
n_pool_uses = sum(len(e[3]) for e in execs)

# ---- tiers ------------------------------------------------------------------------------
MAX_INVALID_FULL_RUN = P["v0_reruns"] + 2 * P["reruns_per_config"] + P["v4_reruns"]


def tier(read_s, job_s, zero_s, pool_start_s, resets_every_exec, store_s, label, n_resets=None):
    """n_resets (if given): pool replacements limited to that many (each at pool_start_s
    plus a P=4 stop); otherwise every execution replaces its pools when resets_every_exec."""
    if n_resets is not None:
        _, blocks, _, pre, stats, close, _ = tier(read_s, job_s, zero_s, pool_start_s, False, store_s, label)
        extra = n_resets * (pool_start_s + POOL_STOP[4])
        first_pools = 2 * pool_start_s
        total = blocks + extra + first_pools + pre + stats + close
        return label, blocks + extra, first_pools, pre, stats, close, total
    per = []
    for (_, n, nz, pools) in execs:
        t = 0.0
        t += sum(pool_start_s for _ in pools) if resets_every_exec else 0.0
        t += P["cooldown_cap_s"] + POLL_S + read_s          # cooldown, worst successful poll
        t += 2 * read_s                                       # th0 + th1
        t += (n - nz) * job_s + nz * zero_s                  # jobs
        t += n * 0.01                                         # monitor check + sample checks per job
        t += min(JOIN_S, read_s) if read_s > 0.05 else 0.0   # telemetry stop join
        t += sum(POOL_STOP[p] for p in pools) if resets_every_exec else 0.0
        t += store_s
        per.append(t)
    blocks = sum(per)
    first_pools = 0.0 if resets_every_exec else 2 * pool_start_s
    pre = ACK_POLLS * 0.25 + P["idle_window_s"] + read_s   # watchdog start -> barrier release (<= ack window) + idle window
    stats = 60.0                                            # bootstrap/evaluation compute (estimate, not a code bound)
    close = POOL_STOP[1] + POOL_STOP[4] + 5.0               # final pool stop + report write
    total = blocks + first_pools + pre + stats + close
    return label, blocks, first_pools, pre, stats, close, total

tiers = [
    tier(0.05, 0.30, 0.01, 5.0, False, 0.2,
         "A planned worst case: max reruns, EVERY cooldown just under its cap, nominal job/sensor/pool times"),
    tier(R_MAX, 0.30, 0.01, 30.0, True, 1.0,
         "B degraded: A + every vcgencmd read at its 3 s limit, pool replaced every block (30 s start + stop)"),
    tier(R_MAX, JOB_MAX, JOB_MAX, READY_S, False, 1.0,
         f"C' pathological but consistent with a full-length run: every job just under 5 s, every read hung,"
         f" pools replaced only after the <= {MAX_INVALID_FULL_RUN} invalid blocks a full-length run can absorb"
         f" (each 120 s)", n_resets=MAX_INVALID_FULL_RUN),
    tier(R_MAX, JOB_MAX, JOB_MAX, READY_S, True, 1.0,
         "C absolute code bound: B + every job just under its 5 s timeout + EVERY pool start just under its"
         " 120 s timeout in EVERY block (needs worker deaths between blocks as well)"),
]

print("Constants read from the rev3 sources")
for k in ("cooldown_cap_s", "job_timeout_s", "idle_window_s", "reruns_per_config", "v0_reruns", "v4_reruns",
          "calib_pairs", "v4_pairs", "v0a_jobs", "v0b_jobs", "v0c_jobs"):
    print(f"  PREREG[{k!r}] = {P[k]}")
print(f"  PREREG['accept_blocks'] = {P['accept_blocks']}   unit_sizes = {P['unit_sizes']}")
print(f"  cooldown poll {POLL_S} s; one sensor read <= {R_MAX} s (vcgencmd {VC_T1} s + {VC_T2} s after SIGTERM)")
print(f"  cooldown (successful) <= {COOL_MAX} s; telemetry join <= {JOIN_S} s; pool ready <= {READY_S} s;"
      f" pool stop <= {POOL_STOP[1]} s (P=1) / {POOL_STOP[4]} s (P=4)")
print(f"  launch.sh: CAP={CAP:.0f} s GRACE={GRACE:.0f} s BARRIER_S={BARRIER:.0f} s ack window {ACK_POLLS * 0.25:.0f} s;"
      f" watchdog poll {WD_INTERVAL:.0f} s")
print()
print("Executions (positions + reruns assigned to the most expensive position)")
for ph in phases:
    print(f"  {ph['name']}: {len(ph['execs'])} executions ({ph['reruns']} reruns): "
          + ", ".join(f"{e[0]}({e[1]})" for e in ph["execs"]))
print(f"  total: {n_exec} executions, {n_jobs} jobs ({n_zero} ZERO jobs), {n_pool_uses} pool uses")
print()
for label, blocks, first_pools, pre, stats, close, total in tiers:
    print(f"{label}")
    print(f"    blocks {blocks:9.0f} s + first pools {first_pools:5.0f} s + pre-load {pre:4.0f} s"
          f" + statistics {stats:3.0f} s + final close {close:3.0f} s = {total:9.0f} s"
          f" = {total / 3600:5.2f} h -> {'WITHIN' if total <= CAP else 'EXCEEDS'} the {CAP:.0f} s cap"
          f" ({'margin' if total <= CAP else 'over by'} {abs(CAP - total):.0f} s)")
print()
print(f"Stop at the cap: the watchdog checks the cap every ~{WD_INTERVAL:.0f} s (plus read time), sends ONE SIGTERM;")
worst_stop = POOL_STOP[1] + POOL_STOP[4] + JOIN_S + 5.0
print(f"  graceful stop needs <= {worst_stop:.0f} s: the parent's wait is interrupted at once; a worker finishing"
      f" its current job (<= {JOB_MAX:.0f} s) does so inside its pool's {STOP_S:.0f} s join window, then at most"
      f" one SIGTERM + {TERM_JOIN_S:.0f} s per worker (pools {POOL_STOP[1] + POOL_STOP[4]:.0f} s); telemetry join"
      f" {JOIN_S:.0f} s; report write 5 s (estimate) vs GRACE_S={GRACE:.0f} s ->"
      f" {'OK' if worst_stop <= GRACE else 'GRACE TOO SHORT'} (margin {GRACE - worst_stop:.0f} s; if exceeded the"
      f" watchdog only logs INTERVENTION REQUIRED and keeps waiting - no SIGKILL)")
print("  Reaching the cap ends the run as INCOMPLETE (interrupted); the cap is a maximum, not a completion guarantee.")
