"""
workload.py - hot / cool inference-like workloads for the TDAS pilot.

DESIGN DECISION THAT MATTERS MOST
---------------------------------
Hostile-reviewer attack #7 is "your latency difference comes from workload
heterogeneity (job lengths), not from thermal effects." We defuse that *by
construction*: HOT and COOL are calibrated to have the SAME cold-start
latency, and differ only in duty cycle (hence average power, hence heating).

If two job classes are indistinguishable in service time on a cold device,
then no SJF/queueing/convoy explanation can account for an ordering effect.
The only remaining channel is thermal. That is the whole point.

Two workload modes:
  synthetic  - numpy GEMM, exact FLOP control, no extra deps  (recommended
               for Experiments 1-3: falsification wants control, not realism)
  onnx       - a real model via onnxruntime                   (realism check
               only, after the synthetic result is known)

FIXED-WORK JOBS (Amendment A2) - see the section at the end of this file
------------------------------------------------------------------------
`Workload.run_once` runs to a wall-clock DEADLINE: a throttled CPU just does
less work in the same time, so its latency cannot show throttling, and orderings
do not do the same total work. It is kept unchanged ONLY as the Experiment 1/2
heat generator. Experiment 3 and workload validation use fixed-work jobs:
latency = time to finish a fixed number of compute units. HOT and COOL use the
same kernel; COOL does fewer units plus a calibrated sleep. CPU demand,
duty and coordination overhead are MEASURED, never assumed from settings, and
equal planned work does not imply equal energy (energy is not measured).
"""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import time
from dataclasses import dataclass
from multiprocessing.connection import wait as _conn_wait

import numpy as np

try:
    import resource                      # Linux/macOS only
except ImportError:                      # pragma: no cover - Windows
    resource = None


class Workload:
    """A job class. duty=1.0 is a tight compute loop; duty<1.0 duty-cycles."""

    def __init__(self, name: str, n: int, duty: float, target_ms: float):
        self.name = name
        self.n = n
        self.duty = duty
        self.target_ms = target_ms
        self.a = np.random.rand(n, n).astype(np.float32)
        self.b = np.random.rand(n, n).astype(np.float32)
        self._sink = 0.0

    def _burst(self):
        c = self.a @ self.b
        self._sink += float(c[0, 0])

    def run_once(self) -> float:
        """Execute one job. Returns wall-clock latency in ms."""
        t0 = time.perf_counter()
        deadline = t0 + self.target_ms / 1000.0
        if self.duty >= 0.999:
            while time.perf_counter() < deadline:
                self._burst()
        else:
            # duty-cycle: compute a slice, then idle a slice.
            slice_s = 0.010  # 10 ms control period
            while True:
                now = time.perf_counter()
                if now >= deadline:
                    break
                work_end = min(now + slice_s * self.duty, deadline)
                while time.perf_counter() < work_end:
                    self._burst()
                idle_end = min(now + slice_s, deadline)
                rem = idle_end - time.perf_counter()
                if rem > 0:
                    time.sleep(rem)
        return (time.perf_counter() - t0) * 1000.0

    def __repr__(self):
        return f"<Workload {self.name} n={self.n} duty={self.duty:.2f} target={self.target_ms}ms>"


def calibrate(n_hot: int = 512, target_ms: float = 200.0,
              cool_duty: float = 0.35, verbose: bool = True):
    """
    Build a HOT/COOL pair with matched cold latency.

    Because both classes run to a wall-clock deadline (target_ms), their
    latencies match by construction on a cold device; COOL simply performs
    less work inside that window and therefore draws less average power.
    Verify the match empirically before trusting it - run verify_match().
    """
    hot = Workload("HOT", n_hot, duty=1.0, target_ms=target_ms)
    cool = Workload("COOL", n_hot, duty=cool_duty, target_ms=target_ms)
    if verbose:
        print(f"[calibrate] {hot}")
        print(f"[calibrate] {cool}")
        print(f"[calibrate] both target {target_ms} ms cold latency; "
              f"COOL duty={cool_duty} -> lower average power")
    return hot, cool


def verify_match(hot: Workload, cool: Workload, reps: int = 15) -> dict:
    """
    Confirm HOT and COOL really do have equal cold latency.
    Run this on a COLD device. If the medians differ by more than a few
    percent, attack #7 is live and you must re-tune before Experiment 3.
    """
    h = [hot.run_once() for _ in range(reps)]
    c = [cool.run_once() for _ in range(reps)]
    hm, cm = float(np.median(h)), float(np.median(c))
    rel = abs(hm - cm) / max(hm, cm)
    return {
        "hot_median_ms": hm,
        "cool_median_ms": cm,
        "relative_gap": rel,
        "matched": rel < 0.05,
        "hot_samples": h,
        "cool_samples": c,
    }


def make_multiset(n_hot: int, n_cool: int, hot: Workload, cool: Workload):
    """The identical multiset used by every ordering in Experiment 3."""
    return [hot] * n_hot + [cool] * n_cool


def order_sequence(multiset, mode: str, rng: np.random.Generator | None = None):
    """
    Produce an execution ORDER of the SAME multiset.
    Every mode returns a permutation - total work is identical by construction.
    """
    hots = [j for j in multiset if j.name == "HOT"]
    cools = [j for j in multiset if j.name == "COOL"]
    if mode == "hot_first":
        return hots + cools
    if mode == "cool_first":
        return cools + hots
    if mode == "alternating":
        out, i, j = [], 0, 0
        while i < len(hots) or j < len(cools):
            if i < len(hots):
                out.append(hots[i]); i += 1
            if j < len(cools):
                out.append(cools[j]); j += 1
        return out
    if mode == "random":
        rng = rng or np.random.default_rng()
        seq = hots + cools
        idx = rng.permutation(len(seq))
        return [seq[k] for k in idx]
    if mode == "blocked":       # H H C C H H C C ...
        out, i, j = [], 0, 0
        while i < len(hots) or j < len(cools):
            for _ in range(2):
                if i < len(hots):
                    out.append(hots[i]); i += 1
            for _ in range(2):
                if j < len(cools):
                    out.append(cools[j]); j += 1
        return out
    raise ValueError(f"unknown ordering mode: {mode}")


ORDERINGS = ["hot_first", "cool_first", "alternating", "blocked", "random"]


# ---------------------------------------------------------------- ONNX mode
def load_onnx(path: str, input_shape=None):
    """Optional realism check. Only run AFTER the synthetic result is known."""
    import onnxruntime as ort  # noqa
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    shape = input_shape or [d if isinstance(d, int) else 1 for d in inp.shape]
    x = np.random.rand(*shape).astype(np.float32)

    def run_once() -> float:
        t0 = time.perf_counter()
        sess.run(None, {inp.name: x})
        return (time.perf_counter() - t0) * 1000.0

    return run_once


# ==========================================================================
# FIXED-WORK JOBS (Amendment A2)
#
# A job = P shards, one per worker process (each pinned to its own core).
# Each shard runs `units_per_shard` compute units of the SAME kernel; a COOL
# shard sleeps sleep_per_shard_ms / units after every unit. The job occupies
# all P workers at once; jobs never overlap. Latency is measured by the parent
# from just before the first dispatch to just after the last shard reply.
# ==========================================================================

JOB_TIMEOUT_S = 5.0          # no shard reply within this -> JobTimeout


class JobTimeout(RuntimeError):
    """A shard did not reply within the job timeout."""


class WorkerFailure(RuntimeError):
    """A worker process died or failed to start."""


@dataclass(frozen=True)
class JobSpec:
    """A fixed-work job. `name` is HOT or COOL (used by order_sequence).
    One unit = `gemms_per_unit` consecutive n_b x n_b GEMMs, timed together
    (1 for P=1 and P4-F1; 4 for P4b, PREREG_workload_validation_ADDENDUM_P4b.md)."""
    name: str
    P: int
    n_b: int
    units_per_shard: int
    sleep_per_shard_ms: float = 0.0
    gemms_per_unit: int = 1

    @property
    def planned_units(self) -> int:
        return self.P * self.units_per_shard

    @property
    def planned_sleep_ms(self) -> float:
        """Total planned sleep over all shards."""
        return self.P * self.sleep_per_shard_ms


def make_job_specs(frozen: dict) -> tuple[JobSpec, JobSpec]:
    """HOT and COOL specs from frozen calibration parameters
    {P, n_b, u_h, u_c, S_ms[, gemms_per_unit]}. HOT never sleeps."""
    g = int(frozen.get("gemms_per_unit", 1))
    hot = JobSpec("HOT", frozen["P"], frozen["n_b"], frozen["u_h"], 0.0, g)
    cool = JobSpec("COOL", frozen["P"], frozen["n_b"], frozen["u_c"],
                   float(frozen["S_ms"]), g)
    return hot, cool


def planned_totals(seq) -> dict:
    """Planned work of a job sequence. Equal for every ordering of a multiset."""
    return {"n_jobs": len(seq),
            "n_hot": sum(1 for j in seq if j.name == "HOT"),
            "n_cool": sum(1 for j in seq if j.name == "COOL"),
            "units": sum(j.planned_units for j in seq),
            "sleep_ms": round(sum(j.planned_sleep_ms for j in seq), 6)}


# ---- calibration arithmetic (inputs are MEASURED values) -----------------
def hot_units(target_ms: float, overhead_ms: float, unit_ms: float) -> int:
    """HOT units per shard: round((L* - o(P)) / t_u(P)), at least 1."""
    return max(1, int(round((target_ms - overhead_ms) / unit_ms)))


def cool_units(u_h: int, d: float) -> int:
    """COOL units per shard: round(d * u_h), at least 1."""
    return max(1, int(round(d * u_h)))


def initial_sleep_ms(u_h: int, u_c: int, unit_ms: float) -> float:
    """Starting COOL sleep per shard, from V0 unit timing."""
    return max(0.0, (u_h - u_c) * unit_ms)


def adjust_sleep_ms(S_ms: float, hot_lat_ms, cool_lat_ms) -> tuple[float, bool]:
    """Shift S by the remaining median gap. Returns (new S, clamped_at_zero)."""
    gap = float(np.median(cool_lat_ms)) - float(np.median(hot_lat_ms))
    new = S_ms - gap
    return (max(0.0, new), new < 0.0)


# ---- per-job aggregation (pure; unit-tested) ------------------------------
def aggregate_job(spec: JobSpec, latency_s: float, shards: list,
                  parent_cpu_s: float = 0.0, t_dispatch: float | None = None) -> dict:
    """Combine shard replies into one job record. Every quantity is measured;
    realised duty = job CPU / (latency x P), never the nominal setting."""
    lat_ms = latency_s * 1000.0
    walls = [s["wall_ms"] for s in shards]
    starts = [s["t_start"] for s in shards]
    max_wall = max(walls) if walls else 0.0
    mean_wall = sum(walls) / len(walls) if walls else 0.0
    cpu_ms = sum(s["cpu_ms"] for s in shards)
    unit_ms = None
    if shards and all(s.get("unit_ms") is not None for s in shards):
        unit_ms = [u for s in shards for u in s["unit_ms"]]
    return {
        "class": spec.name, "P": spec.P, "n_b": spec.n_b,
        "gemms_per_unit": spec.gemms_per_unit,
        "units_planned": spec.planned_units,
        "units_done": sum(s["units_done"] for s in shards),
        "latency_ms": lat_ms,
        "cpu_ms": cpu_ms,
        "compute_ms": sum(s["compute_ms"] for s in shards),
        "sleep_req_ms": spec.planned_sleep_ms,
        "sleep_actual_ms": sum(s["sleep_actual_ms"] for s in shards),
        "overhead_ms": lat_ms - max_wall,
        "imbalance": (max_wall - mean_wall) / max_wall if max_wall > 0 else 0.0,
        "skew_ms": (max(starts) - min(starts)) * 1000.0 if starts else 0.0,
        "start_lag_ms": ((min(starts) - t_dispatch) * 1000.0
                         if starts and t_dispatch is not None else None),
        "realized_duty": cpu_ms / (lat_ms * spec.P) if lat_ms > 0 else None,
        "nivcsw": sum(s.get("nivcsw") or 0 for s in shards),
        "parent_cpu_ms": parent_cpu_s * 1000.0,
        "unit_ms": unit_ms,
        "shards": [{k: v for k, v in s.items() if k != "unit_ms"} for s in shards],
    }


# ---- worker process --------------------------------------------------------
def _make_kernel(kind: str, n_b: int):
    """The compute unit. 'gemm' = one n_b x n_b float32 matmul (the real
    kernel). 'noop', 'spin:<ms>' and 'threadspin:<ms>' (spins in a helper
    thread, to test that worker CPU accounting includes every thread) exist
    only for tests."""
    if kind == "gemm":
        a = np.random.rand(n_b, n_b).astype(np.float32)
        b = np.random.rand(n_b, n_b).astype(np.float32)
        sink = [0.0]

        def k():
            c = a @ b
            sink[0] += float(c[0, 0])
        return k
    if kind == "noop":
        return lambda: None
    if kind.startswith("spin:") or kind.startswith("threadspin:"):
        dur = float(kind.split(":", 1)[1]) / 1000.0

        def spin():
            end = time.perf_counter() + dur
            while time.perf_counter() < end:
                pass
        if kind.startswith("spin:"):
            return spin

        def k():
            import threading
            t = threading.Thread(target=spin)
            t.start()
            t.join()
        return k
    raise ValueError(f"unknown kernel: {kind}")


def _nivcsw():
    if resource is None:
        return None
    return resource.getrusage(resource.RUSAGE_SELF).ru_nivcsw


def _fw_worker(idx, core, kernel_kind, conn, parent_pid):
    signal.signal(signal.SIGINT, signal.SIG_IGN)     # the parent owns shutdown
    pinned = None
    if core is not None and hasattr(os, "sched_setaffinity"):
        try:
            os.sched_setaffinity(0, {core})
            pinned = core
        except OSError:
            pinned = None
    kernels = {}
    conn.send(("ready", idx, os.getpid(), pinned))
    while True:
        if not conn.poll(0.5):
            if os.getppid() != parent_pid:           # parent died: exit
                return
            continue
        try:
            msg = conn.recv()
        except EOFError:
            return
        if msg is None:
            return
        n_b, units = msg["n_b"], msg["units"]
        gap, rec = msg["sleep_gap_s"], msg["record_units"]
        g = msg.get("gemms", 1)
        k = kernels.get((n_b, g))
        if k is None:
            k1 = _make_kernel(kernel_kind, n_b)
            if g == 1:
                k = k1                                  # the original unit, unchanged
            else:
                def k(k1=k1, g=g):                      # P4b: g kernels per timed unit
                    for _ in range(g):
                        k1()
            kernels[(n_b, g)] = k
        # process_time: CPU of every thread in this worker (a multithreaded
        # BLAS would otherwise be missed). Same value for a single thread.
        v0, c0, t_start = _nivcsw(), time.process_time(), time.monotonic()
        compute = slept = 0.0
        done = 0
        unit_ms = [] if rec else None
        for _ in range(units):
            a = time.perf_counter()
            k()
            dt = time.perf_counter() - a
            compute += dt
            done += 1
            if rec:
                unit_ms.append(dt * 1000.0)
            if gap > 0:
                s = time.perf_counter()
                time.sleep(gap)
                slept += time.perf_counter() - s
        t_end = time.monotonic()
        cpu = time.process_time() - c0
        v1 = _nivcsw()
        conn.send({"shard": idx, "core": pinned, "pid": os.getpid(),
                   "units_done": done, "compute_ms": compute * 1000.0,
                   "sleep_actual_ms": slept * 1000.0, "cpu_ms": cpu * 1000.0,
                   "t_start": t_start, "t_end": t_end,
                   "wall_ms": (t_end - t_start) * 1000.0,
                   "nivcsw": (v1 - v0) if v0 is not None else None,
                   "unit_ms": unit_ms})


class FixedWorkPool:
    """P persistent worker processes, worker i pinned to core i. Each job
    uses all P workers at once (dispatch -> barrier on all replies); the
    parent sends the next job only after the last shard has replied."""

    def __init__(self, P: int, kernel: str = "gemm", cores=None,
                 timeout_s: float = JOB_TIMEOUT_S, ready_timeout_s: float = 120.0):
        if P < 1:
            raise ValueError("P must be >= 1")
        self.P, self.timeout_s, self.kernel = P, timeout_s, kernel
        cores = list(range(P)) if cores is None else list(cores)
        ctx = mp.get_context("spawn")
        self.procs, self.conns, self.pinned = [], [], []
        self.unstopped = []        # pids still alive after stop(); never SIGKILLed
        self._stopped = False
        try:
            for i in range(P):
                parent, child = ctx.Pipe()
                p = ctx.Process(target=_fw_worker, name=f"fw-{i}", daemon=True,
                                args=(i, cores[i], kernel, child, os.getpid()))
                p.start()
                child.close()
                self.procs.append(p)
                self.conns.append(parent)
            deadline = time.monotonic() + ready_timeout_s
            for i, c in enumerate(self.conns):
                while not c.poll(0.2):
                    if not self.procs[i].is_alive():
                        raise WorkerFailure(f"worker {i} died during startup")
                    if time.monotonic() > deadline:
                        raise WorkerFailure(f"worker {i} not ready in time")
                self.pinned.append(c.recv()[3])
        except BaseException:
            self.stop()
            raise

    def run_job(self, spec: JobSpec, record_units: bool = False) -> dict:
        if spec.P != self.P:
            raise ValueError(f"spec P={spec.P} but pool P={self.P}")
        u = spec.units_per_shard
        gap = (spec.sleep_per_shard_ms / 1000.0 / u) if u > 0 else 0.0
        msg = {"n_b": spec.n_b, "units": u, "sleep_gap_s": gap,
               "record_units": record_units, "gemms": spec.gemms_per_unit}
        c0 = time.thread_time()
        t0 = time.monotonic()
        for i, c in enumerate(self.conns):
            try:
                c.send(msg)
            except (BrokenPipeError, ConnectionResetError, EOFError) as e:
                # the worker exited since the last job: same handling as a worker that
                # dies during a job (block invalid, pool replaced), not a crash
                raise WorkerFailure(f"worker {i} gone before dispatch ({type(e).__name__})")
        shards = [None] * self.P
        pending = {c: i for i, c in enumerate(self.conns)}
        deadline = t0 + self.timeout_s
        while pending:
            left = deadline - time.monotonic()
            if left <= 0:
                raise JobTimeout(f"no reply from shard(s) {sorted(pending.values())} "
                                 f"within {self.timeout_s} s")
            for c in _conn_wait(list(pending), timeout=left):
                i = pending.pop(c)
                try:
                    shards[i] = c.recv()
                except EOFError:
                    raise WorkerFailure(f"worker {i} closed its pipe")
            for c, i in list(pending.items()):
                if not self.procs[i].is_alive():
                    raise WorkerFailure(f"worker {i} died")
        t1 = time.monotonic()
        return aggregate_job(spec, t1 - t0, shards, time.thread_time() - c0, t0)

    def zero_job(self, n_b: int) -> dict:
        """Dispatch and collect with no work: measures coordination overhead."""
        return self.run_job(JobSpec("ZERO", self.P, n_b, 0, 0.0))

    def alive(self) -> bool:
        return not self._stopped and all(p.is_alive() for p in self.procs)

    def stop(self, timeout_s: float = 5.0) -> list:
        """Idempotent. Graceful (shutdown message), then at most ONE SIGTERM
        per worker still running. Never SIGKILL: a worker still alive after
        that is recorded in `self.unstopped` (its pid) so the caller reports
        INTERVENTION REQUIRED instead of claiming the pool stopped."""
        if self._stopped:
            return [p.exitcode for p in self.procs]
        self._stopped = True
        for c in self.conns:
            try:
                c.send(None)
            except (OSError, BrokenPipeError):
                pass
        deadline = time.monotonic() + timeout_s
        for p in self.procs:
            p.join(max(0.0, deadline - time.monotonic()))
            if p.is_alive():
                p.terminate()                      # SIGTERM, once
                p.join(2.0)
            if p.is_alive():
                self.unstopped.append(p.pid)
        for c in self.conns:
            c.close()
        return [p.exitcode for p in self.procs]
