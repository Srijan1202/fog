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
"""

from __future__ import annotations

import time
import numpy as np


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
