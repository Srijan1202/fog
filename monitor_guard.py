"""
monitor_guard.py - fail-closed link between validate_workload.py and its
external watchdog (tools/validation_monitor.sh). Sends no signals.

Barrier. With `--require-monitor SECONDS`, the validator writes prereg.json
(creating its run directory) and then WAITS, before any worker pool, idle
window or load, until the launcher writes <run>/MONITOR_OK:

    vpid=<pid> vstart=<ticks> mpid=<pid> mstart=<ticks>\n

It accepts the file only if:
  - vpid/vstart are this process's own pid and /proc start time;
  - mpid is alive (not a zombie) with start time mstart, its executable is
    bash, and its argv is [..., ".../validation_monitor.sh", "<vpid>", "<run directory>"];
  - <run>/monitor.log has this watchdog's start line for this pid, start time
    and run id, and was written to within STALE_S seconds.
If that does not happen within SECONDS, wait() returns a reason and the
validator exits without load (no signal from anyone is needed).

During the run, check() repeats the process and log-freshness checks; the
validator calls raise_if_lost() before each block, after each job, during
cooldowns and during the idle window. A failure raises MonitorLost, a
KeyboardInterrupt subclass, so the run stops through the same graceful path
as SIGTERM (pools stopped, report written as INCOMPLETE/interrupted).
"""

from __future__ import annotations

import os
import time

TOKEN = "MONITOR_OK"
STALE_S = 30.0
POLL_S = 0.5


class MonitorLost(KeyboardInterrupt):
    """The external watchdog could not be verified; stops the run gracefully."""


def proc_start(pid: int):
    """Start time (clock ticks since boot, /proc/<pid>/stat field 22), or
    None if the process does not exist, is a zombie, or cannot be read."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            s = f.read().decode("ascii", "replace")
    except OSError:
        return None
    try:
        fields = s.rsplit(")", 1)[1].split()   # fields[0] = field 3 (state)
        if fields[0] == "Z":
            return None
        return int(fields[19])                 # field 22
    except (IndexError, ValueError):
        return None


def proc_argv(pid: int):
    """argv list from /proc/<pid>/cmdline, or None."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if not raw:
        return None
    return raw.rstrip(b"\0").decode("utf-8", "replace").split("\0")


def proc_exe(pid: int):
    """Target of /proc/<pid>/exe, or None."""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def parse_token(text: str):
    """dict with int vpid/vstart/mpid/mstart, or None if malformed/partial."""
    if not text.endswith("\n"):
        return None
    try:
        d = dict(kv.split("=", 1) for kv in text.split())
        out = {k: int(d[k]) for k in ("vpid", "vstart", "mpid", "mstart")}
    except (ValueError, KeyError):
        return None
    if len(d) != 4:
        return None
    return out


class MonitorGuard:
    def __init__(self, run_root: str, run_id: str, pid: int | None = None,
                 stale_s: float = STALE_S, clock=time.time):
        self.root, self.run_id = run_root, run_id
        self.pid = os.getpid() if pid is None else pid
        self.start = proc_start(self.pid)
        self.stale_s, self.clock = stale_s, clock
        self.log_path = os.path.join(run_root, "monitor.log")
        self.token_path = os.path.join(run_root, TOKEN)
        self.mpid = self.mstart = None

    # -- helpers --------------------------------------------------------------
    def _watchdog_problem(self, mpid, mstart, check_exe=False):
        if proc_start(mpid) != mstart:
            return f"watchdog pid {mpid} is not running with start time {mstart}"
        argv = proc_argv(mpid)
        if (not argv or len(argv) < 3 or not argv[-3].endswith("validation_monitor.sh")
                or argv[-2] != str(self.pid) or argv[-1] != self.root):
            return f"watchdog pid {mpid} argv {argv!r} does not match"
        if check_exe:   # at the barrier only: pid + start time pin the identity afterwards
            exe = proc_exe(mpid)
            if not exe or os.path.basename(exe) != "bash":
                return f"watchdog pid {mpid} executable {exe!r} is not bash"
        return None

    def _log_problem(self, need_start_line: bool):
        try:
            age = self.clock() - os.path.getmtime(self.log_path)
        except OSError:
            return "monitor.log missing"
        if age > self.stale_s:
            return f"monitor.log not updated for {age:.0f} s"
        if need_start_line:
            want = f"monitor start: pid={self.pid} start={self.start} run={self.run_id} "
            try:
                with open(self.log_path) as f:
                    if not any(want in line for line in f):
                        return "monitor.log has no start line for this validator"
            except OSError:
                return "monitor.log unreadable"
        return None

    # -- barrier ----------------------------------------------------------------
    def wait(self, timeout_s: float, sleep=time.sleep):
        """Block until a verified MONITOR_OK appears. None on success, else the
        last reason (timeout). Performs no load and sends no signal."""
        if self.start is None:
            return "own /proc start time unreadable"
        deadline = time.monotonic() + timeout_s
        reason = "MONITOR_OK not written"
        while True:
            try:
                with open(self.token_path) as f:
                    tok = parse_token(f.read())
                if tok is None:
                    reason = "MONITOR_OK malformed or incomplete"
                elif (tok["vpid"], tok["vstart"]) != (self.pid, self.start):
                    reason = (f"MONITOR_OK names validator {tok['vpid']}/{tok['vstart']}, "
                              f"not {self.pid}/{self.start}")
                else:
                    reason = (self._watchdog_problem(tok["mpid"], tok["mstart"], check_exe=True)
                              or self._log_problem(need_start_line=True))
                    if reason is None:
                        self.mpid, self.mstart = tok["mpid"], tok["mstart"]
                        return None
            except OSError:
                reason = "MONITOR_OK not written"
            if time.monotonic() >= deadline:
                return f"no verified watchdog within {timeout_s:.0f} s: {reason}"
            sleep(POLL_S)

    # -- during the run -----------------------------------------------------------
    def check(self):
        """None if the verified watchdog is still alive and logging, else a reason."""
        if self.mpid is None:
            return "watchdog was never verified"
        return (self._watchdog_problem(self.mpid, self.mstart)
                or self._log_problem(need_start_line=False))

    def raise_if_lost(self):
        why = self.check()
        if why:
            raise MonitorLost(f"watchdog lost: {why}")

    def describe(self) -> dict:
        return {"validator_pid": self.pid, "validator_start": self.start,
                "watchdog_pid": self.mpid, "watchdog_start": self.mstart,
                "stale_s": self.stale_s}
