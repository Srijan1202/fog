# Amendment A6: Experiment 2 ends each ramp 240 s after the paced warm-up

**Status:** approved by the user on 2026-10-18. The user reviewed it after runs -01 and -02, chose this option
("A6: 4-min back-to-back") and, by doing so, accepted A5 as run. A6 was approved before any data under it.
It changes only the Experiment 2 procedure: the A4 analysis is unchanged, and A5's paced warm-up is
unchanged.

## 1. Why

Run `exp2-a4-20261018-02` (A5; record `provenance/EXP2_RUN_exp2-a4-20261018-02.md`) solved A4's coverage
problem: every paced bin in both ramps reached 30 jobs. But the run was safety-stopped:

- On the plateau the die sits at a median of about 83.3 °C. Single readings reach 85.2 °C, the sensor step
  above 84.7 °C.
- The watchdog read one of these at 672.8 s of ramp 1. Its 85.0 °C stop sent one SIGTERM, which makes G2
  INCONCLUSIVE.
- Across the four ramps run so far, readings ≥ 85.0 °C came no earlier than 5.0 min after back-to-back load
  began: 5.0–6.8 min in run -02, 8–10 min in run -01.

## 2. Change

- After the paced warm-up of A5 ends (all paced bins full, or its 300 s cap), the jobs run back to back for
  **240 s**. Then the ramp ends.
- A ramp never runs longer than 12 min from its start, as before. With the 300 s pacing cap, a ramp lasts at
  most 540 s.
- **Unchanged:**
  - the job, the telemetry, the cooldown and the two ramps;
  - the paced warm-up (A5);
  - the watchdog, including its 85.0 °C stop;
  - the analysis, the validity rules and the coverage rule (A4).
- **Recorded:** per ramp, `pacing.ramp_end_s`, the time from the ramp's start to its planned end. The
  procedure field reads `A4+A5 paced warm-up+A6 240 s back to back`.
- **Options:** `--post-pace-s` (default 240) sets the back-to-back phase. `--full-ramp` gives the A5
  procedure.

## 3. Check of the choice against earlier timing (not outcomes)

The 240 s value was checked against the timing of run -02's jobs and temperatures. No latency or slope was
used. The script is `a4/a6_feasibility.py` and its output is `a4/a6_feasibility.log`.

| back-to-back phase | ramp 0: kept bins ≥ knee (jobs) | ramp 1: kept bins ≥ knee (jobs) | readings ≥ 85.0 °C inside |
|---|---|---|---|
| 180 s | 80 °C (193), 82 °C (491) | 80 (137), 82 (584), 84 (25) | none |
| **240 s** | **80 (201), 82 (751), 84 (28)** | **80 (138), 82 (818), 84 (68)** | **none** |
| 300 s | 80 (203), 82 (1008), 84 (51) | 80 (139), 82 (1038), 84 (123) | none |

With 240 s, each ramp would have had three kept bins at or above the knee, where A4 needs two. There would
have been no reading at or above 85.0 °C: the earliest such reading came 303 s after back-to-back load began.

## 4. Disclosed risks

- **A safety stop is less likely, not impossible.** The margin to the earliest reading ≥ 85.0 °C seen so far
  is about 60 s. A stop would again make G2 INCONCLUSIVE, never FAIL.
- **Fewer plateau jobs.** The bins above the knee still hold hundreds of jobs. The bins near the knee (80 °C)
  hold about the same number as before, because they fill during the climb.
- **What was seen beforehand.** Runs -01 and -02 showed bin-median latencies flat at about 196 ms below the
  knee and 210–215 ms above it. A6 changes no analysis rule, so this cannot steer the analysis. It is
  disclosed anyway.

## 5. Stopping rule

- **One run under A6:** `exp2-a4-20261018-03`. Its G2 is decided by the A4 rule, as computed by
  `analyze.classify_g2`.
- **If it is INCONCLUSIVE,** no further Experiment 2 run is made until the user has reviewed it.

## 6. README text

> ### Amendment A6 (2026-10-18, after Experiment 2 run -02 and before run -03)
> Run `exp2-a4-20261018-02` (A5) filled every low-temperature bin, but the watchdog's 85.0 °C stop ended it.
> On the plateau the die sits at about 83.3 °C, and single readings reach 85.2 °C 5–7 min into back-to-back
> load. Each Experiment 2 ramp now ends 240 s after its paced warm-up. On run -02's timing that leaves at least
> three kept bins at or above the knee per ramp and no reading at or above 85.0 °C. The watchdog stop, the job,
> the warm-up and the A4 analysis are unchanged. Approved by the user. Details: `AMENDMENT_A6_exp2.md`.
