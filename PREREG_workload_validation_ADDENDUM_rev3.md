# Operational addendum to PREREG_workload_validation.md (launch plan wv-20261015-01, rev3)

Status: DRAFT for review (not formally approved). On branch `launch-plan-rev3`, with uncommitted
integration changes; NOT deployed to the Pi.

`PREREG_workload_validation.md` itself is **unchanged** (sha256 `05ab24e5ea4c8bad9e356990328a9b1b4529c1f40d95b08f73471b0d28ece864`),
and so is the `PREREG` constants dict in `validate_workload.py` (diff-checked against the repository original).
No threshold, sample size, rerun limit, criterion or statistic changes. This addendum records only
how the run is launched, supervised and stopped, and two code corrections. Each item below can be
traced to a file and a test in this folder.

## 1. Launch and supervision (operational, not a pre-registered threshold)
- The validator is started only by `tools/launch_validation.sh`, with `--require-monitor 120`.
  The flag is mandatory: without it the validator refuses before reading a sensor or writing a
  file. It writes `prereg.json` and then waits at a
  **no-load barrier** (no worker pool, no idle window, no load) until `<run>/MONITOR_OK` names a watchdog
  that the validator verifies itself: watchdog pid and start time, executable `bash`, argv naming
  `validation_monitor.sh`, this validator's pid and this run directory, and a fresh `monitor.log` with this
  validator's start line. Without that within 120 s it exits without load.
- During the run, the validator re-checks the watchdog (process identity and `monitor.log` freshness
  of at most 30 s) before each block, after each job, during cooldowns and in the idle window, and
  stops through its normal SIGTERM path (`MonitorLost`) if it cannot verify it.
- The external watchdog keeps its pre-registered 67 °C backup limit, plus a 15 °C-per-5 s rise stop,
  under-voltage stops (bit 0 now, or bit 16 newly set), 3 consecutive failed reads, and an operational
  maximum runtime `MAX_RUN_S` = **21 600 s (6 h)**. That maximum is a backstop, not a completion
  guarantee: a run that reaches it is stopped gracefully and reported INCOMPLETE (interrupted).
- **No SIGKILL** is sent to the validator or its workers by any project code. A process still
  alive after its one SIGTERM is reported as INTERVENTION REQUIRED and left for a person.

## 2. Idle screening before launch (preflight, read-only)
Samples every 30 s; evaluated at 15, 20, 25 and 30 min on the last 21 samples (10 min):
|mean(samples 12–21) − mean(samples 2–11)| ≤ 0.5 °C **and** |least-squares slope over the 21 samples,
x = actual timestamps| ≤ 0.05 °C/min. Launch proceeds at the first passing checkpoint; NOT STABLE at
the 30 min checkpoint, or any NOT EVALUABLE window (missing, duplicate, out-of-order or late samples,
gaps outside 25–35 s, non-integer readings), stops the launch. Independently of the trend, these stop
the launch: temperature ≥ 50 °C, any throttle/under-voltage bit, boot id change, governor change on
the policy or any core, a conflicting process, an unreadable sensor. Passing this screen is **not**
evidence of thermal safety under load. The earlier 30-minute observation of 2026-10-14 remains FAILED
under the rule in force at the time.

## 3. Code corrections (behaviour changes, for review)
- `workload.FixedWorkPool.run_job`: a worker that exited between two jobs now raises `WorkerFailure`
  (block invalid, pool replaced, rerun budget applies), as PREREG "Guards and invalid blocks" already
  specifies ("invalid ... on a worker failure"). Before, the dispatch raised an uncaught
  `BrokenPipeError` and the validator left the run through an unhandled exception.
- `validate_workload.main`: `--require-monitor` is mandatory for every run (no unmonitored path).
- `validate_workload.HardwareExecutor.execute`: a pool that lost a worker between two jobs of a block
  now raises `WorkerFailure` (block invalid). Before (also in the original code), the executor silently
  started a new pool mid-block and the block could still count as valid, contrary to PREREG. Pools are
  (re)started only before a block's cooldown.
- `validate_workload.HardwareExecutor._stop_pool`: tolerates pool objects without an `unstopped`
  attribute (found by the repository unit test suite; real pools always have it).

## 4. What this addendum does not cover
Thermal behaviour under sustained four-core load; behaviour of the real validator on the Pi
(Python 3.13, aarch64, real `vcgencmd`); the run's duration (see `tools/runtime_budget.py`).
