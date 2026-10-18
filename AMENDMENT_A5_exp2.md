# Amendment A5: Experiment 2 paces the heating below knee − 10 °C

**Status: approved 2026-10-18 by Claude, under the user's explicit delegation, before any rerun data.**
That night the user wrote: "keep continuing the work … take the decisions which you recommend". The user was
asleep and has **not reviewed this text**. The recommendation the user had been shown (Option A: pace the
heating, analysis rule unchanged) is what is approved here. A5 changes only the Experiment 2 procedure. The
A4 analysis (bins, statistic, validity rule, coverage rule) is unchanged.

## 1. Why

Run `exp2-a4-20261018-01` (record: `provenance/EXP2_RUN_exp2-a4-20261018-01.md`) completed cleanly but G2
was INCONCLUSIVE. In ramp 1, no kept bin (2 °C, at least 20 jobs) lay at or below knee − 10 °C (70.8 °C).

- **Cause.** Four cores running the P4c job back to back heat the die from about 46 °C to 70 °C in 12–23 s.
  That spreads about 60–120 jobs over 12 bins, so each bin below 70 °C held 1–17 jobs and was dropped.
- **A4 design flaw.** A4 combined back-to-back load from idle with a ≥ 20-jobs-per-bin rule and a
  coverage rule below knee − 10 °C without checking the heating rate. That rate was already known: about
  15 °C in 10 s, from the P=4 diagnostic of 2026-10-17.
- **What the low bins showed.** Bins up to 78 °C showed bin-median latencies of 196.4–196.6 ms, and the bins
  above the knee showed 210–214 ms. These values were seen before A5 was written. A5 does not touch the
  analysis, so they cannot steer it. They are disclosed here anyway.

## 2. Change (procedure only)

Each ramp still lasts 12 min from its start, after the A4 cooldown to idle + 3 °C. It now begins with a
**paced warm-up**:

- **Pacing limit.** `L = G1 knee − 10 °C`. For the A3 knee of 80.828 °C, L = 70.828 °C.
- **Paced bins.** The 2 °C bins `[b, b + 2)` with `b` even and `b + 2 ≤ L`, from the bin that holds the
  ramp's start temperature up. For a 46.x °C start these are 46, 48, …, 68: twelve bins.
- **Rule.** Before each job the sysfs temperature `T` is read, as in A4. Let `b*` be the lowest paced bin
  with fewer than **30** jobs, counting jobs by `T` before dispatch, exactly as G2 bins them.
  - If `T < b* + 2`, the job is dispatched.
  - Otherwise no job is dispatched. The temperature is read again every 50 ms, checking the watchdog each
    time, until it is below `b* + 2`.
- **End of pacing.** Pacing ends when every paced bin has 30 jobs, or 300 s after the ramp starts, whichever
  comes first. The jobs then run back to back for the rest of the 12 min, as in A4.
- **Quota.** 30 is A4's 20-job minimum plus a margin, because a job dispatched near a bin edge can be counted
  in the bin below.
- **Unchanged:**
  - the job (P4c HOT, frozen P4c-F1);
  - the telemetry, including the 5 Hz sysfs logger, the between-job `get_throttled` read and the jittered
    1 Hz mailbox clock;
  - the cooldown, the two ramps, the 12 min per ramp and the watchdog (85.0 °C, fork-free);
  - the analysis, the validity rules and the run's outputs.
- **Added per job:** `paced` (whether the job was dispatched during pacing) and `wait_ms` (the idle wait
  before it). Added per ramp: `pacing` (limit, quota, cap, paced jobs, total wait, end time and reason, jobs
  per paced bin).
- **Option.** `--no-pace` reproduces the A4 procedure. Runs under A5 use the default, with pacing on.

## 3. What A5 does and does not change, disclosed

- **The job is the same at every temperature.** Below `L`, jobs may follow an idle gap, usually well under
  1 s and at most a few seconds. Above `L`, they run back to back.
  - The P4c validation accepted HOT jobs that immediately followed COOL jobs containing about 120 ms of sleep
    per shard. Its HOT latency CV was 0.35 %.
  - A first job after an idle gap could still differ (the first job of run -01 took 220.6 ms).
  - G2 uses bin medians. Descriptive, not used by G2: median latency of paced jobs, of paced jobs that
    followed a wait, and of unpaced jobs below the knee (`pacing_latency` in `exp2.meta.json`).
- **Less time on the plateau.** The warm-up uses part of each 12 min ramp. That leaves less time on the
  ~83 °C plateau.
  - In run -01, 2 of about 7200 telemetry samples reached 85.2 °C. The watchdog's 85.0 °C stop was not
    reached.
  - The watchdog limit is unchanged.
  - If the watchdog stops the rerun, the run is ABORTED and INCONCLUSIVE, as A4 says.
- **The coverage rule is unchanged.** At least 2 kept bins at or below knee − 10 °C and at least 2 at or
  above the knee, in each ramp. Pacing is designed to meet the first part. The second part was met by both
  ramps of run -01, with 197–2474 jobs per bin.
- **Run -01 stays INCONCLUSIVE.** A5 does not reclassify it.

## 4. Stopping rule for this amendment

- **One rerun under A5:** `exp2-a4-20261018-02`. Its G2 is decided by the A4 rule, as computed by
  `analyze.classify_g2`.
- **If the rerun is INCONCLUSIVE again** (for any reason), no further Experiment 2 run is made until the user
  has reviewed both runs and this amendment.
- **Experiment 3** is not started under this delegation. Its four-core design (sequence length, hot fraction,
  starting temperature, repetitions) needs the user's decisions first.

## 5. README text

> ### Amendment A5 (2026-10-18, after Experiment 2 run -01 and before its rerun)
> Experiment 2 paces the heating below knee − 10 °C. Run `exp2-a4-20261018-01` was INCONCLUSIVE because four
> cores heat the die from about 46 °C to 70 °C in about 12 s. That left fewer than 20 jobs in each 2 °C bin
> below knee − 10 °C, so the A4 coverage rule failed in ramp 1.
>
> Each 12-minute ramp now starts with a paced warm-up. Below knee − 10 °C, the next job is dispatched only
> while the temperature is under the top of the lowest 2 °C bin that has fewer than 30 jobs. Pacing ends when
> those bins are full, or after 300 s. The jobs then run back to back.
>
> The job, the telemetry, the watchdog and the whole A4 analysis are unchanged. A5 was approved by Claude
> under the user's delegation while the user was away; the user had been shown and had not rejected this
> option. Details: `AMENDMENT_A5_exp2.md`.
