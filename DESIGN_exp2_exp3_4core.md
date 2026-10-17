# Design note: measuring s(T) and running Experiment 3 under four-core load (DRAFT)

Status: design decided 2026-10-17 (section 4), before any Experiment 2, `P4b` or Experiment 3 data.
Nothing here has run. Every step below needs separate approval. The amendment text (A4) goes into README.md
and `tdas-pilot-protocol.md`, as A1–A3 did, when the Experiment 2 code is written.

## 1. Why the current plan cannot work on this Pi

Four findings, all from data already recorded:

1. **The firmware stops throttling as soon as load stops.** In `exp1-4core-a3-20261016-01`, the last loaded
   samples read 81.8–83.3 °C, with the ARM clock at 1629–1800 MHz and the soft limit active. **0.15 s** after
   the heaters stopped, the ARM clock was back at 1800 MHz and stayed there. The sensor fell to 77.4 °C after
   1 s, about 74 °C after 4 s and 70.5 °C after 11 s. Experiment 2's method (protocol Day 1d: "pre-heat, then
   immediately run a fixed batch") therefore probes an unthrottled chip every time.
2. **`exp2_curve.py` probes run to a deadline.** It uses `Workload.run_once`, which runs until a wall-clock
   deadline. A throttled CPU then does less work in the same time, and latency stays the same. `workload.py`'s
   own docstring says this. s(T) would be flat by construction, giving G2 FAIL, which is a structural KILL,
   for a measurement reason.
3. **One busy core never reaches the knee.** The archived one-core run (`exp1_1core`) peaked at 66.7 °C,
   with T_ss estimated at 64.8 °C. That is 16 °C below the A3 knee (80.8 °C). P=1 is the only ACCEPTED
   workload configuration (`wv-20261015-01`), so Experiment 3 as specified would almost certainly never reach
   the knee. Time-above-knee would be about 0 for every ordering, and G4 would fail because of the setup.
4. **G2 has no validity rule.** `analyze.py` scores any exp2 metadata with a slope as PASS or FAIL. An
   aborted run, a run that never reached the knee, and a deadline-probe run are all scored that way, and
   "excludes zero" ignores the sign of the slope.

The knee itself (G1 PASS, 80.8 °C, depth 9.5 %) is not affected. It was measured under sustained four-core
load, which is the regime that matters.

## 2. Why P=4 failed A6 (from `wv-20261015-01`, descriptive)

A6 as reported (`report.json`, accepted pairs): unit-time CV **0.76 %** at P=1 (limit 2 %) and **6.68 %**
at P=4 (limit 3 %); HOT latency CV 0.18 % and 3.08 % (limits 3 % and 4 %).

The rest of this table is descriptive. It covers all HOT jobs of the acceptance blocks, so its values differ
slightly from the report's.

| | P=1 HOT | P=4 HOT |
|---|---|---|
| units | 2970 | 12720 |
| median unit time | 3.699 ms | 3.755 ms (+1.5 %) |
| unit-time CV | 0.86 % | 6.54 % |
| robust CV (1.4826·MAD/median) | 0.04 % | 0.49 % |
| units > 1.5× median | 0.00 % | 0.58 % (share of variance: 74 %) |
| involuntary context switches per job (median) | 2 | 21 |
| HOT latency CV | 0.17 % | 3.31 % |

Typical units at P=4 are as stable as at P=1. A6 fails because of rare long units: 0.58 % of units take more
than 1.5× the median and carry 74 % of the variance. They are spread evenly over the four cores, and a P=4
job has about ten times as many involuntary context switches. The likely cause is preemption: with all four
cores busy, the parent process, its 5 Hz telemetry thread (which starts a `vcgencmd` process for every
sample) and system tasks must take time from a worker. This is a hypothesis. V4, the telemetry on/off
comparison, ran only at P=1, so the data cannot confirm it.

## 3. Proposal

### 3a. Find out what causes the P=4 outliers (diagnostic, non-confirmatory)

A short supervised run on the Pi, about 3 min, cold, at P=4 with the frozen `P4-F1` HOT job. It runs
three blocks in an interleaved order:
- **T0:** telemetry off;
- **T1:** telemetry reads only the sysfs temperature (a file read, no process start);
- **T2:** the current telemetry (sysfs plus `vcgencmd get_throttled`).

It records unit times and involuntary context switches. It is labelled DIAGNOSTIC, like the instrument
check: no acceptance decision uses it, and it only informs the next step.

### 3b. A new four-core workload configuration for Experiment 3 (new validation)

Pre-register, before any of its data, a new configuration (provisionally `P4b`). It keeps the job design
and changes only what the diagnostic implicates. For example, live telemetry would read sysfs only during
jobs, with `vcgencmd` (flags and ARM clock) read between jobs every Nth job. It is validated with **the same
acceptance criteria A1–A9, not loosened**, in a new validation run.

`P4-F1` stays REJECTED, as the pre-registration requires, and that rejection is reported. `P4b` is a new
configuration, not a rerun of `P4-F1`. If `P4b` is also REJECTED, Experiment 3 cannot run at four cores;
the options would then be P=3 (which probably does not reach the knee) or reconsidering the protocol.

### 3c. Experiment 2 redesigned: s(T) under the job stream's own load (Amendment A4)

- **Job:** the HOT fixed-work job of the four-core configuration that Experiment 3 will use (`P4b` if
  accepted). s(T) is then measured on exactly the job whose latency Experiment 3 reports. Only
  `units_done == units_planned` jobs count.
- **Load:** HOT jobs back to back on all four cores. The job stream is its own heater, so the load mix is the
  same at every temperature. No separate heater and no pause before probing.
- **Ramps:** two ramps of 12 min each, from idle (cooled to idle + 3 °C first, the protocol's cooldown
  rule), reaching the throttling plateau (about 82 °C; tau_heat about 109 s). Two ramps guard against a
  one-off drift.
- **Per job:** the sysfs temperature just before dispatch and the job's latency. The firmware ARM clock and
  throttle flags are read between jobs, on every 5th job, so `vcgencmd` never preempts a running job.
- **Supervision:** as for Experiment 1: `--require-monitor`, a fresh outdir, the no-load barrier, a
  watchdog that stops at 85.0 °C, a single SIGTERM and never SIGKILL. An interrupted run is ABORTED.
- **G2 statistic (A4):** jobs are grouped into 2 °C temperature bins per ramp. Bins with fewer than 20 jobs
  are dropped. The statistic is the OLS slope of bin-median latency on bin-median temperature, with a
  bootstrap over (ramp, bin) clusters (2000 resamples, seed 0) and a 95 % percentile CI.
  - **PASS:** the CI's lower bound is above 0 (latency rises with temperature).
  - **FAIL:** the CI includes 0.
  - **INCONCLUSIVE:** the CI's upper bound is below 0. Latency falling with temperature is physically
    unexpected and would point to an artifact.
- **G2 validity (A4, like A1):** G2 is decided only by a completed, valid Experiment 2:
  - not aborted, safety-stopped or interrupted;
  - fixed-work jobs from the validated configuration;
  - a G1-PASS knee recorded;
  - in each ramp, at least 2 kept bins at or above the knee and at least 2 kept bins at or below the knee
    minus 10 °C;
  - no under-voltage;
  - the ARM clock readable in at least 95 % of its readings.

  Otherwise G2 is INCONCLUSIVE, never FAIL. An exp2 record made before A4 is INCONCLUSIVE.
- **Descriptive only:** latency against ARM clock, as a check on the mediation link (latency should scale
  with 1/f); per-ramp curves; the exp2 knee in the ARM clock (supporting evidence only, as before).

### 3d. Run order after this note

1. Diagnostic (3a).
2. `P4b` pre-registered, then validated (3b).
3. A4 committed, then Experiment 2 (3c).
4. Experiment 3 at `P4b`.

Experiment 3 also needs a start temperature and k chosen with the fast thermal component in mind: the
sensor moves 4 °C in 1 s when load changes. That is a separate note before Experiment 3.

## 4. Decisions (made by the user on 2026-10-17, before any Experiment 2 or `P4b` data)

1. **Order:** the diagnostic (3a) first, then `P4b` is pre-registered based on what it shows.
2. **Experiment 2's job:** `P4b`'s HOT job. Experiment 2 waits for the `P4b` validation. The rejected
   `P4-F1` job is not used.
3. **G2 statistic:** the binned slope with the (ramp, bin) cluster bootstrap described in 3c.
4. **Sign rule:** a CI entirely below 0 counts as INCONCLUSIVE, never PASS or FAIL.

## 5. Not affected

G1 (A3) and the Experiment 1 record; the workload validation `wv-20261015-01` and its verdicts (P=1
ACCEPTED, P=4 REJECTED); the instrument check.
