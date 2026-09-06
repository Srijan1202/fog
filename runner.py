"""
runner.py - shared harness: telemetry sampling, cooldown control, logging.

Two controls in here are load-bearing for the whole pilot:

1. cool_to() blocks until the device reaches a TEMPERATURE, never a fixed
   duration. Fixed rests drift as ambient changes over a multi-hour session
   and silently confound condition with time-of-day.

2. Telemetry runs in a background thread at a fixed rate so the thermal
   trajectory is sampled independently of job boundaries. If you sample only
   between jobs you cannot compute time-above-knee correctly.
"""

from __future__ import annotations

import json
import os
import threading
import time


class TelemetryLogger:
    def __init__(self, backend, hz: float = 5.0):
        self.backend = backend
        self.period = 1.0 / hz
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.samples.append(self.backend.read())
            except Exception as e:  # never let telemetry kill a run
                self.samples.append({"t_wall": time.monotonic(), "error": str(e)})
            self._stop.wait(self.period)

    def start(self):
        self.samples = []
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        return self.samples

    def current_temp(self):
        try:
            return self.backend.read().get("temp_c")
        except Exception:
            return None


def cool_to(backend, target_c: float, timeout_s: float = 900.0,
            poll_s: float = 2.0, verbose: bool = True) -> dict:
    """
    Block until temp <= target_c. Returns how long it took and the final temp.
    A timeout is NOT a failure of the pilot - it is data about tau_cool.
    """
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout_s:
        r = backend.read()
        last = r.get("temp_c")
        if last is None:
            return {"ok": False, "reason": "no temperature sensor",
                    "elapsed_s": 0.0, "final_temp_c": None}
        if last <= target_c:
            el = time.monotonic() - t0
            if verbose:
                print(f"  [cooldown] reached {last:.1f}C in {el:.0f}s")
            return {"ok": True, "elapsed_s": el, "final_temp_c": last}
        time.sleep(poll_s)
    el = time.monotonic() - t0
    if verbose:
        print(f"  [cooldown] TIMEOUT after {el:.0f}s at {last}C "
              f"(target {target_c}C)")
    return {"ok": False, "reason": "timeout", "elapsed_s": el,
            "final_temp_c": last}


def ambient_estimate(backend, settle_s: float = 60.0) -> float | None:
    """Idle temperature after settling - our proxy for ambient + idle offset."""
    time.sleep(settle_s)
    return backend.read().get("temp_c")


def write_jsonl(path: str, records: list[dict]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def read_jsonl(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def save_run(outdir: str, name: str, meta: dict,
             jobs: list[dict], telemetry: list[dict]):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, f"{name}.meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    write_jsonl(os.path.join(outdir, f"{name}.jobs.jsonl"), jobs)
    write_jsonl(os.path.join(outdir, f"{name}.telemetry.jsonl"), telemetry)
