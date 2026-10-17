# Amendment A4: Experiment 2 measures s(T) under its own four-core load

Status: approved by the user on 2026-10-18, before any Experiment 2 or Experiment 3 data. Drafted 2026-10-17.
Nothing here has run.

Once approved, the text in section 5 goes into README.md (as "Amendment A4") with a one-line pointer in
`tdas-pilot-protocol.md`, like A1–A3. It builds on `DESIGN_exp2_exp3_4core.md`, whose decisions (section 4)
were made on 2026-10-17, and on the four-core validation record
`provenance/WORKLOAD_VALIDATION_FOUR_CORE_20261017.md`.

## 1. What changed since the design note

- **The job.** The design note chose "P4b's HOT job". P4b was REJECTED, and P4c (P4b's job design under the
  fork-free watchdog) was ACCEPTED, so the job is **P4c's HOT job**: frozen `P4c-F1`, 13 four-GEMM units per
  shard, about 196 ms.
- **The ARM clock.** The design note proposed reading the ARM clock "between jobs, on every 5th job". That is
  withdrawn: the firmware releases throttling within 0.15 s of load ending, so a read after a job does not see
  it. The clock has to be read **during** load, and without starting a process. A `vcgencmd` process during
  jobs is what caused the P4b stalls.
- **The watchdog.** Experiment 2 needs an 85 °C watchdog, because it must reach the knee. The only one,
  `tools/exp1_monitor.sh`, is a copy of the old forking validation watchdog. Its 5 s process bursts would add
  exactly the 15–50 ms stalls that A6 detected.

## 2. Measurement

- **Job.** P4c's HOT job, from an ACCEPTED P4c validation made with the current `workload.py`
  (`resolve_workload(..., config="4c")`). Only jobs with `units_done == units_planned` count; any worker
  failure or timeout aborts the run.
- **Load.** HOT jobs back to back on all four cores. The job stream is its own heater, so the load mix is the
  same at every temperature.
- **Ramps.** Two ramps, each 12 min of back-to-back jobs, each starting from idle. Before each ramp: cooldown
  with no load to idle + 3 °C (the protocol's cooldown rule), at most 20 min. If idle is not reached, the run
  is aborted (INCOMPLETE).
- **Per job.**
  - the sysfs temperature just before dispatch (a file read, between jobs);
  - the job's latency, CPU time and units;
  - P4c's between-job `vcgencmd get_throttled` read, kept exactly as validated.
- **Telemetry during jobs.**
  - P4c's 5 Hz sysfs logger;
  - **plus the firmware ARM clock read in-process through the mailbox interface (`/dev/vcio`, property
    tag "get measured clock rate", ARM clock; `fwclock.py`).** This is a single ioctl from a parent thread,
    with no process start and no `vcgencmd`.
  - **Rate: about 1 Hz,** with each gap (1 s) × U(0.5, 1.5) from a seeded generator, so the reads cannot
    lock in phase with the job cadence.
  - **Experiment 2 only.** Experiment 3, whose outcome is tail latency, makes no live clock reads; see the
    stall control below.
- **Feasibility check (required before A4 is final).** A short read-only check on the Pi, with no load, must
  show three things:
  - the ioctl works without root;
  - it agrees with `vcgencmd measure_clock arm` within 1 %;
  - one read costs under 1 ms of **CPU time** (the time that can preempt a worker).

  If any of these fails, A4 is revised before any Experiment 2 data.
  - **Result, 2026-10-18 (with no load).** A first attempt failed: `/dev/vcio` was root-only. The user then
    added `/etc/udev/rules.d/99-tdas-vcio.rules`
    (`KERNEL=="vcio", GROUP="video", MODE="0660"`). After that:
    - the read works without root;
    - it agrees with `vcgencmd` to +0.000 % and +0.003 %;
    - CPU time per read is 0.022 ms median (0.085 ms at most);
    - wall time per read is 1.03 ms median (5.1 ms at most). The thread is blocked waiting for the firmware.
  - **Disclosure.** The draft said "costs under 1 ms" without naming wall or CPU time. It was clarified to CPU
    time after this check (the user's decision), before any Experiment 2 data. Read as wall time, the check
    fails narrowly. The stall control below tests the actual effect on the jobs.
  - **Evidence:** `exp1-rerun-prep/a3/diag/a4_vcio_feasibility*.log`.
- **Stall control (non-confirmatory, before the Experiment 2 run).** A `diag_p4_stalls` check with mailbox
  clock reads in W blocks and none in N blocks, using P4c's jobs. The rule written beforehand: HOT latency CV
  and the stall rate must not be worse in W than in N, excluding warm-up pairs. Three runs, 2026-10-18:

  | run | design | HOT latency CV, W / N | stalls, W / N | verdict |
  |---|---|---|---|---|
  | `diag-stalls-20261018-01` | HOT/COOL pairs, 5 Hz | 1.10 % / 0.82 % | 1 / 0 (the run's first warm-up job) | rule not met: W's tail had about 6 of 80 jobs +3–5 ms |
  | `diag-stalls-20261018-02` | HOT/COOL pairs, fixed 1 Hz | 0.89 % / 1.01 % | 1 / 1 | **invalid**: every read after warm-up fell on a COOL job (phase lock), so no HOT job was exposed |
  | `diag-stalls-20261018-03` | **HOT-only blocks (Experiment 2's load), jittered 1 Hz** | **0.92 % / 0.73 %** | **2 / 0** | **rule not met as written** |

  **Disclosure on run -03.**
  - The exposure was real: reads overlapped 33 post-warm-up HOT jobs.
  - The read-attributable effect is small: overlapped HOT jobs averaged 197.62 ms against 197.27 ms
    (+0.35 ms, about 0.2 %).
  - Both W stalls fell on jobs that no read overlapped.
  - Run -02 showed W–N differences of about ±0.1–0.2 points of CV even with no exposure.
  - The rule had no noise tolerance. That is a design flaw of the rule, but it was not met as written.

  **Decision (the user's, 2026-10-18, after these results).** Keep jittered 1 Hz reads in **Experiment 2
  only**: G2 uses bin medians, which an effect of about 0.2 % on about 20 % of jobs barely moves, and the
  clock feeds only the descriptive mediation check and the ≥ 95 % readability rule. Experiment 3 makes no live
  clock reads.
  - **Evidence:** `exp1-rerun-prep/a3/diag/stalls/run_diag-stalls-20261018-0[123].tar` and the analysis logs
    alongside.

## 3. Supervision

- **New `tools/exp2_monitor.sh`,** derived from the fork-free `tools/validation_monitor.sh` (commit `2db3d44`).
  The diff is limited to the process identity (`exp2_curve.py`), the 85.0 °C stop and the default cap.
  Otherwise it keeps the same rules: a rise of more than 15 °C per 5 s, under-voltage now (hwmon alarm),
  3 failed reads, one SIGTERM and never SIGKILL, and sticky bit 16 logged at exit.
- **`exp2_curve.py --require-monitor`** is mandatory on real hardware. Behaviour as in Experiment 1:
  - a fresh outdir, and no load until a verified watchdog;
  - it stops itself if the watchdog is lost;
  - an interrupted run is saved as ABORTED.
- **`tools/launch_exp2.sh` and `tools/status_exp2.sh`,** derived from the Experiment 1 launcher and status
  script (idle screen, identity checks, hash pins).

## 4. Analysis (pre-specified; the user's decisions of 2026-10-17)

- **Bins.** Jobs are grouped into **2 °C temperature bins per ramp**, using the temperature before dispatch.
  Bins with fewer than 20 jobs are dropped.
- **G2 statistic.** The OLS slope of bin-median latency on bin-median temperature, with a **bootstrap over
  (ramp, bin) clusters**: 2000 resamples, seed 0, 95 % percentile CI.
  - **PASS:** the CI's lower bound is above 0.
  - **FAIL:** the CI includes 0.
  - **INCONCLUSIVE:** the CI's upper bound is below 0. This is never PASS or FAIL.
- **G2 validity.** G2 is decided only by a completed, valid Experiment 2. Otherwise it is INCONCLUSIVE, never
  FAIL. A valid run meets all of these:
  - not aborted, safety-stopped or interrupted, and supervised as in section 3;
  - P4c's fixed-work HOT jobs (ACCEPTED validation; current `workload.py`);
  - a G1-PASS knee recorded (A1/A3);
  - **in each ramp, ≥ 2 kept bins with bin-median temperature ≥ the knee, and ≥ 2 kept bins ≤ knee − 10 °C;**
  - no under-voltage (live flags between jobs; sticky audit at the start and end of each ramp);
  - the ARM clock readable in ≥ 95 % of its reads (about 1 Hz, jittered).
- **Pre-A4 records.** Any exp2 record made before A4 is INCONCLUSIVE for G2.
- **Descriptive only:**
  - latency against the ARM clock (the mediation check: latency should scale with 1/f);
  - per-ramp curves;
  - the exp2 knee in the ARM clock (supporting evidence only, as before).

## 5. README text (to be inserted once approved)

> ### Amendment A4 (2026-10-18, before any Experiment 2 or Experiment 3 data)
> Experiment 2 measures s(T) under its own four-core load.
> - **Why.** On this Pi the firmware stops throttling within 0.15 s of load ending, so "pre-heat, then probe"
>   never sees a throttled probe. The original deadline probes cannot show throttling at all. And one busy
>   core reaches only about 65 °C, below the 80.8 °C knee.
> - **Measurement.**
>   - P4c's accepted HOT job, back to back on four cores: two 12-minute ramps from idle + 3 °C.
>   - Per job: temperature before dispatch and latency. The ARM clock is read in-process during load through
>     the firmware mailbox, starting no process, about once per second with jitter.
>   - Supervision as in Experiment 1, with a fork-free 85 °C watchdog.
> - **G2.** The slope of bin-median latency on temperature, in 2 °C bins per ramp with at least 20 jobs per
>   bin, with a cluster bootstrap over (ramp, bin). PASS if the 95 % CI is above 0, FAIL if it includes 0,
>   INCONCLUSIVE if it is below 0.
> - **Validity (as A1 does for G1).** G2 counts only from a completed, valid run: supervised, P4c jobs, a G1
>   knee, ≥ 2 bins at or above the knee and ≥ 2 bins at or below knee − 10 °C in each ramp, no
>   under-voltage, ARM clock ≥ 95 % readable. Otherwise G2 is INCONCLUSIVE, never FAIL. Pre-A4 exp2 records are
>   INCONCLUSIVE.
> - **Experiment 3** runs P4c jobs under P4c's validated telemetry: the sysfs logger and the between-job flag
>   read. It makes no live ARM-clock reads, because its outcome is tail latency. (The stall control
>   `diag-stalls-20261018-03` measured about +0.35 ms on read-overlapped jobs; see `AMENDMENT_A4_exp2.md`.)

## 6. Work after approval (each Pi step asks first)

1. Feasibility check of the mailbox clock read on the Pi (read-only, no load).
2. Stall control with mailbox reads (non-confirmatory) - done; see section 2.
3. Code: `sensors.py` mailbox clock reader; `exp2_curve.py` rewrite; `tools/exp2_monitor.sh`,
   `tools/launch_exp2.sh` and `tools/status_exp2.sh`; `analyze.py` G2 rule; tests and a dummy harness.
4. A4 committed to README and the protocol, then the Experiment 2 run.
