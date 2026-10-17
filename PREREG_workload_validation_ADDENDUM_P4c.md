# Pre-registration addendum: workload configuration P4c (four cores, fork-free watchdog)

Status: approved by the user on 2026-10-17, before any P4c data. Nothing in it has run.
`PREREG_workload_validation.md` and its acceptance criteria are **unchanged**. P4c reuses the job design of
`PREREG_workload_validation_ADDENDUM_P4b.md`, as amended there (Amendment 1). The one change is the
supervising watchdog.

## 1. Why a new configuration

P4b was REJECTED on A6 in run `wv-p4b-20261017-02`: unit-time CV 4.63 % (limit 3 %) and HOT latency CV
4.43 % (limit 4 %). Every other criterion held. That verdict is final and stays on record. The non-confirmatory
diagnostics that followed all used P4b's frozen parameters on the same Pi; evidence is in
`exp1-rerun-prep/a3/diag/stalls/`.

| run | condition | HOT jobs | stalls | HOT latency CV |
|---|---|---|---|---|
| `diag-stalls-20261017-01` | no external watchdog | 240 | 1 | 1.47 % (with between-job flag read) / 0.97 % (without) |
| `diag-stalls-20261017-02` | poller with the old watchdog's 5 s commands | 120 | **8** (all 8 on jobs a poll overlapped; 0 of 112 otherwise) | **4.54 %** |
| | no poller | 120 | 0 | 0.99 % |
| `diag-stalls-20261017-03` | poller with the new fork-free loop body | 120 | 1 (the run's first warm-up job) | 1.44 %; **0.84 %** without warm-up pairs |
| | no poller | 120 | 0 | 0.84 %; **0.85 %** without warm-up pairs |

**Cause.** The external watchdog `tools/validation_monitor.sh` started about 12 short processes every 5 s
(`pgrep`, `cat`, `tr`, `vcgencmd`, `cut`, `date`, `bc`, `sleep`). With all four cores busy, each burst
preempted the validator's workers for tens of milliseconds.

**Fix (commit `2db3d44`).** The watchdog's loop now starts no process. In 20 s of monitoring it created 0
processes, against 75 for the previous version (dummy harness W14).

**Disclosure.**
- P4c was designed after P4b failed and after these diagnostics.
- The last diagnostic had only about 2 polls per block, so few polls coincided with HOT jobs.
- The criteria are not changed, and this validation run is the test.

## 2. Configuration P4c (fixed now)

**Identical to P4b, as amended:**

| item | value |
|---|---|
| workers | P = 4, one worker per core |
| unit | 4 consecutive 128×128 float32 GEMMs, timed as one unit |
| V0b′ | 10 P=4 UNIT jobs of 12 units each |
| V0c′ | 50 P=4 ZERO jobs |
| u_h, u_c, S0 | derived as in P4b |
| calibration | C1, C2: 10 pairs each |
| acceptance | 15, 15, 15, 15 pairs, warm-up 5 per block → 40 accepted pairs |
| reruns | 2 |
| telemetry during jobs | 5 Hz sysfs-only logger (temperature, `scaling_cur_freq`) |
| between jobs | one `vcgencmd get_throttled`; a live throttle or under-voltage bit makes the block invalid |
| around blocks | sticky-bit audit before and after each block |
| guards | 65 °C block guard, sysfs frequency ≥ 1800 MHz, cooldown to idle + 2 °C |

**What changes: the external watchdog** is `tools/validation_monitor.sh`, sha256
`1a98cdc1d52dc44143a1f701a1bebb61ccf562e8d4a0b5c50016b4b95f0cd765` (commit `2db3d44`).
- **No process is started while the validator runs.** Temperature (sysfs) and under-voltage now (the
  kernel's `rpi_volt` hwmon `in0_lcrit_alarm`) are read with builtins. Identity checks, child tracking,
  logging and waiting are builtins too.
- **Unchanged stop rules:** 67 °C, a rise of more than 15 °C per 5 s, under-voltage now, 3 failed reads,
  run cap 21 600 s; one SIGTERM, never SIGKILL.
- **Changed: the sticky "under-voltage occurred" bit (16).** It is read with `vcgencmd` only at watchdog
  start (baseline) and after the validator exits, and logged then. It no longer stops a run mid-way.
  During the run, the validator's own per-job live-flag guard and per-block sticky audit cover it, as
  before.

**Launch:** `tools/launch_validation.sh` with `VALIDATOR_ARGS="--config 4c"`. Every executed file is pinned
by hash, the watchdog included.

## 3. Decision (unchanged criteria)

Criteria A1–A9 with the **P = 4 column** of `PREREG_workload_validation.md`: A2 ≥ 0.90; A6 ≤ 3 % unit-time
CV and ≤ 4 % HOT latency CV; A9 ≤ 5 %. The gap classes, paired bootstrap (10 000 resamples, seed 0) and
statistics limitation are the same.

- **ACCEPTED:** all criteria hold. **REJECTED:** any criterion fails. **INCOMPLETE:** rerun limit,
  cooldown cap or V0 incomplete.
- A REJECTED P4c is final and is reported with P4-F1 and P4b.

## 4. Consequences

- **If ACCEPTED:** Experiments 2 and 3 use P4c's frozen parameters, the same telemetry scheme and the same
  fork-free watchdog.
- **ARM clock:** how the firmware ARM clock is recorded during load in those experiments is decided in
  Amendment A4 (P4b addendum, section 4), before any Experiment 2 data.
- **If REJECTED:** no further four-core variant is run without a new written pre-registration.

## 5. Implementation (in the same change as this addendum)

- `validate_workload.py`:
  - `--config 4c` runs V0b′, V0c′ and P4c. Constants are P4b's, with the label `P4c`; the report key is
    `"4c"`.
  - `prereg.json` and `report.json` record the hashes of both addenda and of the watchdog.
- `exp3_ordering.resolve_workload`: accepts configuration `"4c"`. Experiment 3 still refuses four-core
  configurations until A4.
- `tools/launch_validation.sh`: `VALIDATOR_ARGS="--config 4c"` is allowed; the site config holds the P4c
  run and pins.
- Tests for each change. A separate approval is needed before any run.
