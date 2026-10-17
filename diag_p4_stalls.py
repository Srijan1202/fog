#!/usr/bin/env python3
"""
DIAGNOSTIC (non-confirmatory): what stalls four-core P4b jobs?

In validation wv-p4b-20261017-02, P4b was REJECTED on A6: 38 of 40 accepted HOT
jobs took 197.1 ms (CV ~0.1 %), but two were hit by a one-off 15-50 ms stall
(about 67 involuntary context switches against a median of 14), both right
after a COOL job. This runs the same frozen P4b jobs (HOT/COOL pairs from that
validation's report.json) in 16 blocks of 15 pairs, and around EVERY job
records what else ran on the Pi:
  - CPU time of every other thread (/proc/<pid>/task/<tid>/schedstat, ns),
    kernel threads included;
  - interrupts and softirqs per CPU (/proc/interrupts, /proc/softirqs).
Two conditions are interleaved F N N F x 4:
  F  P4b's scheme: one `vcgencmd get_throttled` between jobs (validation telemetry)
  N  no flag read between jobs
With --mode watchdog the blocks are W N N W x 4 instead: W runs a poller with the
external watchdog's 5 s commands (tools/validation_monitor.sh: pgrep, cat,
vcgencmd | cut, echo | bc, date, sleep), N none; every job keeps P4b's
between-job flag read, and each job is marked if a poller iteration overlapped
it. The poller stops on a stop file (no signal).
Both use P4b's 5 Hz sysfs-only logger during jobs. Snapshots are taken between
jobs, outside every latency measurement. No acceptance decision uses this; it
only informs the next configuration (P4c), which is pre-registered and
validated on the unchanged criteria.

    python3 diag_p4_stalls.py \
        --report results/workload_validation/wv-p4b-20261017-02/report.json \
        --outdir results/diag-stalls-<date>-01

Safety (real hardware): the same refusals as diag_p4_preemption.py (governor
performance, flags 0x0, start < 55 C, load1 < 0.5). Before every block it waits
with no load until the temperature is below --cooldown-below (default 50 C);
before every job it stops at --max-temp (default 70 C); time under load is
capped by --max-load-s. At most one SIGTERM per worker, never SIGKILL; an
interrupted run still saves its data. It changes no setting.

Outputs: <outdir>/stalls.{meta.json,jobs.jsonl,telemetry.jsonl}
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time

import threading

import numpy as np

import diag_p4_preemption as dp
import fwclock
import runner
import sensors
import validate_workload as vw
import workload as wl

ORDER = ("F", "N", "N", "F") * 4
CONDITIONS = {"F": "vcgencmd get_throttled between jobs (P4b scheme)",
              "N": "no flag read between jobs"}
# --mode watchdog: does the external watchdog's 5 s poll (tools/validation_monitor.sh:
# pgrep, cat, vcgencmd | cut, echo | bc, date, sleep - a burst of process starts)
# stall the jobs? W blocks run a poller with the same commands; N blocks none.
# Both keep P4b's between-job flag read.
MODES = {"telemetry": (ORDER, CONDITIONS),
         "watchdog": (("W", "N", "N", "W") * 4,
                      {"W": "watchdog-like poller every 5 s (validation_monitor.sh loop commands)",
                       "N": "no poller"}),
         # P4c check: W runs the fork-free watchdog's loop body (builtins only)
         "watchdog-nf": (("W", "N", "N", "W") * 4,
                         {"W": "fork-free poller every 5 s (P4c validation_monitor.sh loop body)",
                          "N": "no poller"}),
         # A4 stall control: W reads the firmware ARM clock through the mailbox at
         # 5 Hz from a parent thread (fwclock.py), as Experiment 2's telemetry would
         "mailbox": (("W", "N", "N", "W") * 4,
                     {"W": "5 Hz firmware-mailbox ARM-clock reads in a parent thread (A4)",
                      "N": "no clock reads"})}


class FakeClock:
    """Stands in for fwclock.ArmClockMailbox on the mock backend (tests only)."""

    def read(self):
        time.sleep(0.001)
        return 1800.0

    def close(self):
        pass


class ClockReader:
    """5 Hz reads of a clock in a thread; records (t_start, t_end, MHz or None)."""

    def __init__(self, clock, hz=5.0):
        self.clock, self.period = clock, 1.0 / hz
        self.samples, self._stop = [], threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.is_set():
            t0 = time.monotonic()
            v = self.clock.read()
            self.samples.append((t0, time.monotonic(), v))
            self._stop.wait(max(0.0, self.period - (time.monotonic() - t0)))

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=5.0)
        return self.samples
POLLER = r'''
STOP=$1; LOG=$2; VP=$3
while [ ! -e "$STOP" ]; do
  echo "$(cut -d' ' -f1 /proc/uptime) start" >> "$LOG"
  for c in $(pgrep -P "$VP" 2>/dev/null); do cat "/proc/$c/stat" > /dev/null 2>&1; done
  T=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
  TH=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
  printf '%6.1f' "$(echo "${T:-0}/1000" | bc -l)" > /dev/null
  [ $(( $(date +%s) - 0 )) -ge 0 ]
  echo "$(cut -d' ' -f1 /proc/uptime) end" >> "$LOG"
  for i in 1 2 3 4 5; do [ -e "$STOP" ] && break; sleep 1; done
done
'''


POLLER_NF = r'''
STOP=$1; LOG=$2; VP=$3
UV=""; for h in /sys/class/hwmon/hwmon*; do n=""; { read -r n < "$h/name"; } 2>/dev/null
  [ "$n" = rpi_volt ] && UV="$h/in0_lcrit_alarm"; done
exec {SFD}<> <(:)
up() { local u; read -r u _ < /proc/uptime; printf '%s %s\n' "$u" "$1" >> "$LOG"; }
while [ ! -e "$STOP" ]; do
  up start
  for f in "/proc/$VP/task/"*/children; do k=(); { read -r -a k < "$f"; } 2>/dev/null
    for c in "${k[@]}"; do { read -r s < "/proc/$c/stat"; } 2>/dev/null; a=(); { mapfile -d '' -t a < "/proc/$c/cmdline"; } 2>/dev/null; done; done
  T=""; { read -r T < /sys/class/thermal/thermal_zone0/temp; } 2>/dev/null
  U=""; [ -n "$UV" ] && { read -r U < "$UV"; } 2>/dev/null
  t=$(( (${T:-0} + 50) / 100 )); printf -v R '%4d.%d   uv=%s' $((t / 10)) $((t % 10)) "${U:-?}"
  printf -v ts '%(%F %T)T' -1; printf '%s %s\n' "$ts" "$R" >> "$LOG.readings"
  [ $(( EPOCHSECONDS - 0 )) -ge 0 ]
  up end
  for i in 1 2 3 4 5; do [ -e "$STOP" ] && break; read -r -t 1 -u "$SFD" _ ; done
done
'''


def start_poller(stop, log, ppid, script=POLLER):
    import subprocess
    return subprocess.Popen(["bash", "-c", script, "poller", stop, log, str(ppid)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def stop_poller(proc, stop, wait_s=12.0):
    """Ask the poller to stop (stop file, no signal). Returns its pid if it is
    still alive after wait_s, else None."""
    import subprocess
    with open(stop, "w"):
        pass
    try:
        proc.wait(timeout=wait_s)
        return None
    except subprocess.TimeoutExpired:
        return proc.pid


def poll_intervals(log, offset):
    """[(start, end)] of poller iterations in time.monotonic() seconds
    (offset = /proc/uptime - time.monotonic(), measured at start)."""
    out, start = [], None
    try:
        with open(log) as f:
            for ln in f:
                parts = ln.split()
                if len(parts) != 2:
                    continue
                t = float(parts[0]) - offset
                if parts[1] == "start":
                    start = t
                elif parts[1] == "end" and start is not None:
                    out.append((start, t))
                    start = None
    except OSError:
        pass
    return out


def mark_overlap(jobs, intervals):
    """job["poll_overlap"]: a poller iteration overlapped the job's [t0, t1].
    intervals: [(block, start, end)] - only the job's own block counts (uptime
    has 10 ms resolution, so a boundary iteration must not spill into the next
    block) - or [(start, end)], which apply to every job."""
    for j in jobs:
        j["poll_overlap"] = any(s < j["t1_mono"] and e > j["t0_mono"]
                                for *b, s, e in intervals if not b or b[0] == j.get("block"))
    return jobs


def _uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])
PROC = "/proc"
MIN_TASK_MS = 0.2              # per-job foreign thread CPU below this is not listed


def load_p4b_specs(report_path):
    """HOT and COOL JobSpecs of the frozen four-GEMM P=4 parameters in a validation
    report: configuration P4c ("4c") if present, else P4b ("4b")."""
    with open(report_path) as f:
        rep = json.load(f)
    cfgs = rep.get("configs") or {}
    cfg = cfgs.get("4c") or cfgs.get("4b") or {}
    fr = cfg.get("frozen") or {}
    if (not all(k in fr for k in ("P", "n_b", "u_h", "u_c", "S_ms"))
            or fr["P"] != 4 or fr.get("gemms_per_unit") != 4):
        raise SystemExit(f"no frozen P4b parameters in {report_path}")
    hot, cool = wl.make_job_specs(fr)
    return hot, cool, {"validation_run": rep.get("run_id"), "params_id": fr.get("params_id"),
                       "status": cfg.get("status")}


class Snapshot:
    """Per-thread CPU time (ns) and per-CPU interrupt / softirq counts."""

    def __init__(self, proc=PROC):
        self.proc = proc
        self.comm = {}                               # tid -> (comm, pid), cached

    def tasks(self):
        out = {}
        for pid in os.listdir(self.proc):
            if not pid.isdigit():
                continue
            try:
                tids = os.listdir(f"{self.proc}/{pid}/task")
            except OSError:
                continue
            for tid in tids:
                try:
                    with open(f"{self.proc}/{pid}/task/{tid}/schedstat") as f:
                        ns = int(f.read().split()[0])
                    if tid not in self.comm:
                        with open(f"{self.proc}/{pid}/task/{tid}/comm") as f:
                            self.comm[tid] = (f.read().strip(), int(pid))
                except (OSError, ValueError, IndexError):
                    continue
                out[tid] = ns
        return out

    def counters(self, name):
        """{label: [count per CPU]} from /proc/interrupts or /proc/softirqs."""
        out = {}
        try:
            with open(f"{self.proc}/{name}") as f:
                lines = f.read().splitlines()
        except OSError:
            return out
        ncpu = len(lines[0].split()) if lines else 0
        for ln in lines[1:]:
            parts = ln.split()
            if len(parts) < ncpu + 1:
                continue
            label = parts[0].rstrip(":")
            try:
                counts = [int(x) for x in parts[1:ncpu + 1]]
            except ValueError:
                continue
            desc = " ".join(parts[ncpu + 1:])
            out[f"{label} {desc}".strip()] = counts
        return out

    def take(self):
        return {"tasks": self.tasks(), "irq": self.counters("interrupts"),
                "softirq": self.counters("softirqs")}


def diff(snap, a, b, own_pids, parent_pid=None):
    """What ran between snapshots a and b, other than the worker processes in
    own_pids. The parent's own threads (e.g. its telemetry logger) are kept and
    named "parent:<comm>", because they can preempt a worker too."""
    foreign = []
    for tid, ns in b["tasks"].items():
        comm, pid = snap.comm.get(tid, ("?", -1))
        if pid in own_pids:
            continue
        if pid == parent_pid:
            comm = f"parent:{comm}"
        d = (ns - a["tasks"].get(tid, ns)) / 1e6       # new threads: 0 (unknown start)
        if d >= MIN_TASK_MS:
            foreign.append((comm, int(tid), round(d, 3)))
    foreign.sort(key=lambda x: -x[2])

    def counts(kind):
        out = {}
        for k, v in b[kind].items():
            dv = [y - x for x, y in zip(a[kind].get(k, v), v)]
            if any(dv):
                out[k] = dv
        return out
    return {"foreign_ms": round(sum(x[2] for x in foreign), 3), "foreign": foreign[:10],
            "irq": counts("irq"), "softirq": counts("softirq")}


def mark_stalls(jobs, lat_factor=1.05, unit_factor=1.5):
    """Descriptive stall flag per HOT job: latency > lat_factor x the block's HOT
    median, or any unit > unit_factor x the block's median unit."""
    for blk in sorted({j["block"] for j in jobs}):
        hot = [j for j in jobs if j["block"] == blk and j["class"] == "HOT"]
        if not hot:
            continue
        lat_med = float(np.median([j["latency_ms"] for j in hot]))
        units = [u for j in hot for u in (j.get("unit_ms") or [])]
        u_med = float(np.median(units)) if units else None
        for j in hot:
            umax = max(j.get("unit_ms") or [0.0])
            j["stall"] = bool(j["latency_ms"] > lat_factor * lat_med
                              or (u_med is not None and umax > unit_factor * u_med))
    return jobs


def summarize(jobs, conds=CONDITIONS):
    """Per condition: stall rate, and mean foreign CPU per thread name in stall
    vs other HOT jobs (the ranking that points at the cause). With poller data,
    also the stall rate of HOT jobs that a poller iteration overlapped vs not."""
    out = {}
    for c in conds:
        hot = [j for j in jobs if j["condition"] == c and j["class"] == "HOT"]
        st = [j for j in hot if j.get("stall")]
        ok = [j for j in hot if not j.get("stall")]

        def by_comm(js):
            acc = {}
            for j in js:
                for comm, _, ms in j["during"]["foreign"]:
                    acc[comm] = acc.get(comm, 0.0) + ms
            return {k: round(v / len(js), 3) for k, v in acc.items()} if js else {}
        s_c, o_c = by_comm(st), by_comm(ok)
        excess = sorted(((k, round(v - o_c.get(k, 0.0), 3)) for k, v in s_c.items()),
                        key=lambda x: -x[1])
        lat = [j["latency_ms"] for j in hot]
        out[c] = {"hot_jobs": len(hot), "stalls": len(st),
                  "stall_rate": round(len(st) / len(hot), 4) if hot else None,
                  "hot_latency_cv": float(np.std(lat) / np.mean(lat)) if lat else None,
                  "foreign_ms_stall_mean": round(float(np.mean([j["during"]["foreign_ms"]
                                                                for j in st])), 3) if st else None,
                  "foreign_ms_other_mean": round(float(np.mean([j["during"]["foreign_ms"]
                                                                for j in ok])), 3) if ok else None,
                  "excess_by_thread_in_stalls": excess[:10]}
        if any("poll_overlap" in j for j in hot):
            ov = [j for j in hot if j.get("poll_overlap")]
            no = [j for j in hot if not j.get("poll_overlap")]
            out[c].update(
                hot_jobs_poll_overlap=len(ov), stalls_poll_overlap=sum(1 for j in ov if j.get("stall")),
                hot_jobs_no_overlap=len(no), stalls_no_overlap=sum(1 for j in no if j.get("stall")))
    return out


def _sigterm(signum, frame):
    raise KeyboardInterrupt("SIGTERM")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=None)
    ap.add_argument("--report", required=True,
                    help="P4b validation report.json (frozen P4b parameters)")
    ap.add_argument("--outdir", required=True, help="fresh directory (never overwritten)")
    ap.add_argument("--pairs-per-block", type=int, default=15)
    ap.add_argument("--max-temp", type=float, default=70.0)
    ap.add_argument("--max-start-temp", type=float, default=55.0)
    ap.add_argument("--cooldown-below", type=float, default=50.0)
    ap.add_argument("--cooldown-max-s", type=float, default=300.0)
    ap.add_argument("--poll-s", type=float, default=2.0)
    ap.add_argument("--max-load-s", type=float, default=600.0)
    ap.add_argument("--hz", type=float, default=5.0)
    ap.add_argument("--clock-hz", type=float, default=1.0,
                    help="--mode mailbox: ARM-clock reads per second in W blocks (A4: 1 Hz; "
                         "5 Hz added a small latency tail in diag-stalls-20261018-01)")
    ap.add_argument("--mode", choices=tuple(MODES), default="telemetry",
                    help="telemetry: F/N (between-job vcgencmd read or not); "
                         "watchdog: W/N (watchdog-like 5 s poller or not)")
    args = ap.parse_args(argv)
    order, conds = MODES[args.mode]

    b = sensors.get_backend(args.backend)
    is_mock = b.name == "mock"
    hot, cool, src = load_p4b_specs(args.report)
    print(f"[stalls] DIAGNOSTIC, not confirmatory. backend={b.name} HOT={hot} COOL={cool} "
          f"(validation {src['validation_run']} {src['params_id']}, status {src['status']})")
    if not is_mock:
        why = dp.hw_precheck(b, args.max_start_temp)
        if why:
            print(f"refused before any load: {why}")
            return 2
    try:
        os.makedirs(args.outdir)
    except FileExistsError:
        print(f"refused: {args.outdir} exists; refusing to overwrite")
        return 2
    clock = None
    if args.mode == "mailbox":
        try:
            clock = FakeClock() if is_mock else fwclock.ArmClockMailbox()
        except OSError as e:
            print(f"refused before any load: firmware mailbox unavailable ({e})")
            os.rmdir(args.outdir)
            return 2
    temp = (lambda: 45.0) if is_mock else dp.read_sysfs_temp
    old_handlers = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    signal.signal(signal.SIGTERM, _sigterm)

    snap = Snapshot()
    jobs, tele, blocks = [], [], []
    stop_reason = interrupted = None
    pool = log = None
    load_s = 0.0
    poller, poller_unstopped, intervals = None, [], []
    reader, clock_samples = None, []
    offset = _uptime() - time.monotonic()        # /proc/uptime -> time.monotonic()
    try:
        pool = wl.FixedWorkPool(4)
        own = {p.pid for p in pool.procs}                # the workers; the parent is reported
        for bi, cond in enumerate(order):
            t_start, waited, stop_reason = dp.cooldown(temp, args.cooldown_below,
                                                       args.cooldown_max_s, args.poll_s)
            if stop_reason:
                blocks.append({"block": bi, "condition": cond, "jobs_run": 0,
                               "start_temp_c": t_start, "cooldown_s": round(waited, 1)})
                break
            log = runner.TelemetryLogger(vw.SysfsReader(b), hz=args.hz)
            log.start()
            if cond == "W" and args.mode == "mailbox":
                reader = ClockReader(clock, args.clock_hz).start()
            elif cond == "W":
                stop_f = os.path.join(args.outdir, f"poller{bi}.stop")
                poll_log = os.path.join(args.outdir, f"poller{bi}.log")
                poller = start_poller(stop_f, poll_log, os.getpid(),
                                      POLLER_NF if args.mode == "watchdog-nf" else POLLER)
            block, t_block = [], time.monotonic()
            try:
                for item in vw.pair_plan(args.pairs_per_block, hot, cool):
                    t = temp()
                    if t is None:
                        stop_reason = "temperature unreadable"
                    elif t >= args.max_temp:
                        stop_reason = f"max_temp: {t:.1f} C >= {args.max_temp} C"
                    elif load_s + time.monotonic() - t_block > args.max_load_s:
                        stop_reason = f"max_load_s {args.max_load_s:.0f} s reached"
                    if stop_reason:
                        break
                    s0 = snap.take()
                    t0 = time.monotonic()
                    rec = pool.run_job(item["spec"], record_units=True)
                    t1 = time.monotonic()
                    s1 = snap.take()
                    rec.update(block=bi, condition=cond, seq=len(block), pair=item["pair"],
                               t0_mono=t0, t1_mono=t1,
                               pos=item["pos"], temp_before_c=t,
                               during=diff(snap, s0, s1, own, os.getpid()),
                               shard_walls=[round(s["wall_ms"], 3) for s in rec["shards"]])
                    rec.pop("shards", None)
                    if cond == "F" or args.mode.startswith("watchdog") or args.mode == "mailbox":
                        rec["throttled_after"] = vw._flags(b)   # between jobs, as P4b
                    block.append(rec)
            finally:
                for s in log.stop():
                    tele.append({**s, "block": bi, "condition": cond})
                log = None
                if poller is not None:
                    left = stop_poller(poller, stop_f)
                    if left:
                        poller_unstopped.append(left)
                    poller = None
                    intervals += [(bi, s, e) for s, e in poll_intervals(poll_log, offset)]
                if reader is not None:
                    got = reader.stop()
                    reader = None
                    clock_samples += [(bi, a, z, v) for a, z, v in got]
                    intervals += [(bi, a, z) for a, z, v in got]
                load_s += time.monotonic() - t_block
                jobs += block
                blocks.append({"block": bi, "condition": cond, "jobs_run": len(block),
                               "start_temp_c": t_start, "cooldown_s": round(waited, 1),
                               "end_temp_c": temp()})
            print(f"  block {bi} {cond}: {len(block)} jobs, start {t_start} C", flush=True)
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
        if poller is not None:
            left = stop_poller(poller, stop_f)
            if left:
                poller_unstopped.append(left)
        unstopped = []
        if pool is not None:
            pool.stop()
            unstopped = list(pool.unstopped)

    mark_stalls(jobs)
    if reader is not None:
        clock_samples += [(-1, a, z, v) for a, z, v in reader.stop()]
    if clock is not None:
        clock.close()
    if args.mode.startswith("watchdog") or args.mode == "mailbox":
        mark_overlap(jobs, intervals)
    summary = summarize(jobs, conds)
    meta = {"experiment": "diag_p4_stalls", "label": "DIAGNOSTIC - not confirmatory",
            "backend": b.name, "mock": is_mock, "source": src,
            "hot": {"units": hot.units_per_shard, "gemms_per_unit": hot.gemms_per_unit},
            "cool": {"units": cool.units_per_shard, "sleep_ms": cool.sleep_per_shard_ms},
            "mode": args.mode, "order": list(order), "conditions": conds,
            "poll_iterations": len(intervals), "poller_unstopped": poller_unstopped,
            "clock_reads": len(clock_samples),
            "clock_misses": sum(1 for x in clock_samples if x[3] is None),
            "clock_mhz_min": min((x[3] for x in clock_samples if x[3] is not None), default=None),
            "clock_read_wall_ms_median": (round(float(np.median([(z - a) * 1e3 for _, a, z, _ in
                                                                  clock_samples])), 3)
                                          if clock_samples else None),
            "pairs_per_block": args.pairs_per_block, "max_temp_c": args.max_temp,
            "cooldown_below_c": args.cooldown_below, "max_load_s": args.max_load_s,
            "clock_hz": args.clock_hz if args.mode == "mailbox" else None,
            "load_s": round(load_s, 3), "stop_reason": stop_reason, "interrupted": interrupted,
            "complete": stop_reason is None and interrupted is None and len(blocks) == len(order),
            "unstopped_workers": unstopped, "blocks": blocks, "summary": summary,
            "temp_max_c": max((j["temp_before_c"] for j in jobs), default=None)}
    runner.save_run(args.outdir, "stalls", meta, jobs, tele)

    print("\nSTALL DIAGNOSTIC SUMMARY (descriptive)")
    for c, s in summary.items():
        print(f"  {c} {conds[c]}: HOT jobs {s['hot_jobs']}, stalls {s['stalls']}, "
              f"HOT latency CV {s['hot_latency_cv'] if s['hot_latency_cv'] is None else round(s['hot_latency_cv'], 4)}, "
              f"foreign CPU per job: stall {s['foreign_ms_stall_mean']} ms vs other "
              f"{s['foreign_ms_other_mean']} ms")
        if "hot_jobs_poll_overlap" in s:
            print(f"     poller overlapped {s['hot_jobs_poll_overlap']} HOT jobs "
                  f"({s['stalls_poll_overlap']} stalls); not overlapped "
                  f"{s['hot_jobs_no_overlap']} ({s['stalls_no_overlap']} stalls)")
        if s["excess_by_thread_in_stalls"]:
            print(f"     threads with extra CPU in stall jobs: {s['excess_by_thread_in_stalls'][:6]}")
    if stop_reason or interrupted:
        print(f"  STOPPED EARLY: {stop_reason or interrupted}")
    if unstopped:
        print(f"  INTERVENTION REQUIRED: workers still alive after one SIGTERM: {unstopped}")
    print(f"wrote {args.outdir}/stalls.*")
    signal.signal(signal.SIGTERM, old_handlers[0])     # save done: restore the caller's handlers
    signal.signal(signal.SIGINT, old_handlers[1])
    return 130 if interrupted else (1 if unstopped else 0)


if __name__ == "__main__":
    raise SystemExit(main())
