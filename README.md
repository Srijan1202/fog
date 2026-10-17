# TDAS Hardware Pilot — Execution Kit

Falsification-first. The goal is to **kill TDAS cheaply** if the physics
doesn't hold on your hardware. Experiment 2 alone can end this in an
afternoon.

---

## Go / No-Go decision tree

```
probe.py
  └─ no temp sensor, or no (freq OR throttle flags)? ────────────► NO-GO
     (cannot measure the knee; get Pi-class hardware)
  └─ governor not pinnable (VM/container/Colab)? ─────────────► NO-GO*
     (*NVML read-only works on Colab, but datacenter GPUs are
       actively cooled and rarely reach the knee)
       │
       ▼
exp1_thermal.py    → tau_heat, tau_cool, knee, throttle depth
  └─ no knee reached under sustained load ──────────────────► KILL (P4)
     (only a completed, valid full-load run - see Amendment A1;
      on the Pi the knee is read from the firmware ARM clock - A3)
  └─ tau/horizon far from 1 for all feasible k ─────────────► KILL (P3)
       │
       ▼
exp2_curve.py      → s(T)
  └─ slope CI includes zero (s(T) flat) ────────────────────► KILL (P1)
       │
       ▼
exp3_ordering.py   → identical-multiset orderings
  └─ no valid Exp 1 knee (G1 not PASS) ─────────────────────► refuses to run (A1)
  └─ time-above-knee identical across orderings ────────────► KILL (no pathway)
  └─ mediator moves, no metric responds ────────────────────► LIKELY KILL
  └─ mediator moves, mean/throughput responds, P99 doesn't ─► REFORMULATE
  └─ full chain intact ─────────────────────────────────────► PROCEED to baselines
```

---

## Setup

```bash
# Raspberry Pi / Linux
sudo apt install -y python3-pip
pip3 install numpy
# optional realism check later:  pip3 install onnxruntime
# optional NVIDIA:               pip3 install nvidia-ml-py

# CRITICAL — pin the governor. Without this, DVFS confounds thermal
# throttling and the entire pilot is uninterpretable.
sudo sh -c 'for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
              echo performance > $f; done'
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor   # verify

# quiet the machine
sudo systemctl stop unattended-upgrades 2>/dev/null
# no GUI, no network sync, nothing else running
```

Physical setup: **no heatsink, no fan**, open air, fixed orientation, same
power supply throughout. Log room temperature at the start of every session.

---

## Run order

```bash
python3 probe.py                                    # Experiment 0   (5 min)
# Exp 1 (~30 min; separate approval) - only via tools/launch_exp1.sh, see below
python3 exp2_curve.py --stages 8 --outdir results         # Exp 2  (~2 h)
# workload validation (A2; separate approval) - only via tools/launch_validation.sh, see below
python3 exp3_ordering.py --reps 10 --k 60 --parallel 4 \
    --workload-validation results/workload_validation/<run>/report.json \
    --exp1-meta results/exp1.meta.json --outdir results           # Exp 3 (~4-6 h)
python3 analyze.py --outdir results                 # verdict
```

Pipeline check without hardware (**simulated — never report as a result**):

```bash
python3 probe.py --backend mock
python3 exp1_thermal.py --backend mock --minutes 2 --cool-minutes 2 --outdir results_mock
```

### Launching Experiment 1 (four-core thermal / throttle knee)

Only with explicit approval, and only through **`tools/launch_exp1.sh`** (status:
`tools/status_exp1.sh`). On real hardware `exp1_thermal.py` refuses to run without
`--require-monitor`; the mock backend may run without it, for pipeline checks only.
The launch follows the workload-validation procedure below (idle screen, identity
checks, no-load barrier, `MONITOR_OK`, SIGTERM only, never SIGKILL), with these
differences:
- It runs `exp1_thermal.py --workers 4 --minutes 15` into a fresh `results/<RUN>`
  directory and pins `exp1_thermal.py`, `analyze.py`, `workload.py`, `monitor_guard.py`,
  `runner.py`, `sensors.py` and `tools/exp1_monitor.sh`.
- The watchdog is `tools/exp1_monitor.sh`: the same stop rules as the validation
  watchdog, but it stops at **85.0 °C** (the run has to reach firmware throttling,
  around 80 °C on a Pi 4) and has a 45 min cap (`CAP=2700`).
- Any stop (85 °C, under-voltage, read failures, cap, a lost watchdog, an operator
  SIGTERM) still writes `exp1.meta.json`, marked ABORTED, which is INCONCLUSIVE for G1. Only
  a complete, unstopped four-core run can establish the throttle knee that
  Experiment 3 needs (Amendment A1), and on the Pi only from the firmware ARM
  clock (Amendment A3).

### Launching the workload validation

Only with explicit approval, and only through **`tools/launch_validation.sh`**.
`validate_workload.py` refuses to run without `--require-monitor`, and then does no
load until the launcher has started and verified the watchdog. Do not start the
validator or `tools/validation_monitor.sh` by hand for a real run.

Before launching:
- The project on the Pi (`~/tdas-pilot`) must match the hashes in
  `provenance/WORKLOAD_VALIDATION_rev3.md`; the launcher refuses other file contents.
- The CPU governor must be `performance` on every core. Set it by hand; the
  launcher only checks it.
- Edit only the `# BEGIN SITE CONFIG` block of `tools/launch_validation.sh` and
  `tools/status_validation.sh`: an unused `RUN` id, the Pi's current boot id as `B0`,
  and (launcher) the expected file hashes.

Preparation (each step with its own approval; neither starts the validator or any workload):

```bash
ssh <pi> 'bash -s' < tools/pi_smoke_test.sh       # dummy-only smoke test of the watchdog and monitor guard
ssh <pi> 'bash -s' < tools/idle_observation.sh    # read-only idle observation, the launcher's Phase 1 rule
```

`pi_smoke_test.sh` refuses unless every project file matches its pinned hashes, records the device,
boot id and software versions, and exercises the barrier, watchdog stop paths and cleanup with dummy
processes only (exit 0 = SMOKE PASS). `idle_observation.sh` applies exactly the launcher's idle rule and
hard stops (exit 0 PASS, 1 NOT STABLE, 2 NOT EVALUABLE, 3 hard stop, 4 interrupted). Both write a new
record under `~/tdas-pilot/observations/` (with a `.sha256`) and never overwrite one. Neither is evidence
of thermal safety under load.

The launch and status scripts also run on the Pi through stdin; they are never stored or run from the Pi's copy:

```bash
ssh <pi> 'bash -s' < tools/launch_validation.sh   # exit 0 = monitoring established
ssh <pi> 'bash -s' < tools/status_validation.sh   # read-only report every 60 s; re-run to re-attach
```

The launcher:
1. Watches the idle Pi (a sample every 30 s; checks at 15, 20, 25 and 30 min) and
   continues at the first check where the two 5-minute means differ by ≤ 0.5 °C and
   the 10-minute least-squares slope is ≤ 0.05 °C/min. It stops on ≥ 50 °C, any
   throttle or under-voltage flag, a boot-id or governor change, a conflicting
   process, an unreadable sensor, or missing or irregular samples. This is idle
   screening only, not evidence of thermal safety under load.
2. Checks file and archive hashes, that the run's output paths do not exist, free
   disk and load.
3. Starts the validator (`--require-monitor 120`). It writes `prereg.json`, then waits.
4. Verifies the validator: pid, start time, executable, exact command line, own
   session, no child processes, waiting at its barrier.
5. Starts the watchdog with `MAX_RUN_S=21600` and verifies it the same way, plus its
   `monitor.log` start line (which must show the 21600 s cap) and a fresh reading.
6. Only then writes `MONITOR_OK`. The validator checks the watchdog itself before
   it starts its idle window and load.

Exit status: 0 monitoring established; 3 nothing launched; 4 launched but monitoring
not established (the validator exits at its barrier without load); 5 `MONITOR_OK`
written but not acknowledged within 15 s (no signal sent; run the status script).

During the run:
- The watchdog checks every 5 s and stops the run at **67 °C**, on a rise of more
  than 15 °C in 5 s, on under-voltage (bit 0 now, or bit 16 newly set), after 3
  failed reads, or at `MAX_RUN_S`. The launcher passes 21600 s (6 h) and verifies
  it; if the watchdog is started by hand without `MAX_RUN_S`, it falls back to
  7200 s (2 h). Other throttle flags (bits 1-3) invalidate the affected block in
  the validator's own guard; they do not stop the run.
- The validator re-checks the watchdog before each block, after each job, during
  cooldowns and in the idle window, and stops itself if the watchdog is gone or its
  log is more than 30 s old.
- **Signals: SIGTERM only, never SIGKILL; each sender sends the validator at most
  one.** The watchdog sends one at a limit or at the cap; the launcher sends one only
  if monitoring could not be established and the verified validator is still at its
  barrier with no children; the status script sends one only if the watchdog is
  confirmed absent and the verified validator is still running 60 s later. A process
  still alive after its SIGTERM is reported as **INTERVENTION REQUIRED** and left for
  a person.
- On SIGTERM the validator stops its worker pools (shutdown message, then one SIGTERM
  per worker still running; Python's exit handler may send a surviving worker one
  more, and the watchdog sends one to any worker left after the validator exits) and
  writes `report.json` with `overall: INCOMPLETE` and `interrupted` set. A run stopped at the 6 h cap is therefore INCOMPLETE; 6 h is a
  maximum, not a promise that the run completes.
- **SSH disconnection** does not stop a launched run: the validator and the watchdog
  run under `setsid nohup`. If the launcher is lost before `MONITOR_OK`, the validator
  exits at its barrier without load. Losing the status script only ends that observer;
  it never restarts anything.
- **Results:** `$OUT/$RUN/report.json` and `report.txt`; watchdog log
  `$OUT/$RUN/monitor.log`. Experiment 3 accepts only a report with
  `overall: COMPLETE`, no interruption, and an ACCEPTED configuration for its
  `--parallel`, made with the current `workload.py`.

Worst-case run time, computed from the sources: `python3 tools/runtime_budget.py`.

**Choosing `k` for Experiment 3.** Experiment 1 prints a `tau/horizon` table.
Pick `k` so `tau/horizon` lands near 1. This is the corrected P3 criterion —
*not* `tau >= 5 x service_time`. If `tau >> k x service_time`, temperature is
quasi-static over your decision window and ordering cannot matter; if
`tau << service_time` there is no memory. Both extremes kill TDAS.

---

## Experiment-by-experiment

**Exp 0 — probe.** Detects backend, governor, sensors. Records idle temp
(cooldowns target idle + 3 °C).

**Exp 1 — thermal response.** Sustained load to equilibrium, then idle.
Fits `T(t) = T_inf + (T0 - T_inf)e^(-t/tau)` by grid search over tau with
closed-form least squares — no scipy. Reports tau_heat, tau_cool, knee,
throttle depth, and flags **structured residuals** (possible second-order
system). Second-order matters: it is the only condition under which a
history-integrating "debt" term carries information beyond instantaneous T.

**Exp 2 — s(T).** Stage-heat, then probe immediately with a fixed batch.
Bootstrap CI on the slope (ms/°C) plus Spearman rho. **If the slope CI
includes zero, stop — TDAS is dead and you've spent one afternoon.**

**Exp 3 — ordering.** Same multiset, different permutations. Counterbalanced
and randomized within blocks; cooldown to a fixed **temperature** before every
run. Records per-job latency and 5 Hz telemetry, computes time-above-knee.

---

## The workload design (why attack #7 is defused)

> **Superseded for Experiment 3 by Amendment A2 (below).** The original
> design, kept here for the record: HOT and COOL both ran to the same
> wall-clock deadline, so they had identical cold latency by construction, and
> COOL duty-cycled at 0.35. On the Pi this was defective. One 512×512 multiply
> takes about 237 ms, longer than the 200 ms deadline, so both classes did
> exactly one multiply at 100% CPU and there was no contrast. A deadline job's
> latency also cannot show throttling, because a slower CPU just does less work
> in the same time. The deadline `Workload` class remains only as the
> Experiment 1/2 heat generator.

**Fixed-work design (A2).** Latency is the time to finish a fixed number of
compute units, using the same kernel for both classes. HOT runs `u_h` units
back to back. COOL runs `u_c ≈ 0.35·u_h` units plus a calibrated sleep, so its
cold latency matches HOT's while its CPU demand is about a third. P = 1 and
P = 4 (shards on 4 pinned worker processes; each job uses all of them) are
calibrated separately. `validate_workload.py` measures the units, overhead,
CPU time, realised duty and cold-latency gap against pre-registered criteria
(`PREREG_workload_validation.md`). Experiment 3 runs on real hardware only with
an **ACCEPTED** validation for its `--parallel`. Matched cold latency means
service times are indistinguishable when cold, within the measured gap, so
SJF, queueing and convoy effects cannot explain an ordering effect. That leaves
the thermal channel.

---

## Data schema

`exp*.jobs.jsonl` — one record per job:
```
idx, class(HOT|COOL), latency_ms, t_start, temp_before_c,
temp_after_c, freq_mhz, throttled, rep, mode, run_index
```

`exp*.telemetry.jsonl` — one record per sample (5 Hz default):
```
t_wall, temp_c, freq_mhz, throttled, energy_uj, run_index, mode
```
Experiment 1 on the Pi also records `arm_clock_mhz` (firmware ARM clock,
Amendment A3); its `exp1.meta.json` has `knee` (from that clock, with
`clock_source`, `clock_samples`, `clock_missing`) and `knee_sysfs`
(from `freq_mhz`, descriptive only).

`exp*.meta.json` — fits, knee, per-run summaries:
```
wall_s, n_jobs, mean_ms, p50_ms, p95_ms, p99_ms, throughput_jobs_s,
peak_temp_c, mean_temp_c, time_above_knee_s, time_throttled_s,
rep, mode, run_index, start_temp_c, session_t
```

---

## Statistics

- **Bootstrap CIs on median differences** (10k resamples), not t-tests —
  latency distributions are skewed and n is small.
- **Cliff's delta** for effect size. |d| < 0.147 is negligible regardless
  of significance.
- **No invented thresholds.** The gates are stated as "CI excludes zero,"
  which is justified by the design, rather than an arbitrary "≥20%."
- With reps=10 you have low power. Say so in the writeup rather than
  over-claiming.

---

## Pre-registered kill gates

Committed **before** looking at Experiment 3 results.

| Gate | Tests | KILL if |
|---|---|---|
| G1 | throttle knee exists (P4) | no frequency drop under sustained load (valid Exp 1 only; see A1) |
| G2 | s(T) not flat (P1) | slope CI includes zero |
| G3 | HOT/COOL cold-latency match | gap > 5% (attack #7 live) → reformulate |
| G4 | ordering moves the mediator | time-above-knee CI includes zero |
| G5 | ordering moves the outcome | no P99 difference survives its CI |
| G5b | any secondary metric moves | — (distinguishes kill from reformulate) |
| G6 | mediator co-varies with outcome | corr(time_above_knee, p99) < 0.3 |

**G1, G2, G4 are structural.** Any one failing = KILL, no appeal.

The mediation rule: a P99 difference counts as evidence for TDAS **only if
time-above-knee also moved**. Ordering → thermal trajectory → throttle
exposure → service time → tail latency. Flat middle links mean the latency
difference is noise or confounding.

### Amendment A1 (2026-10-14, before any Experiment 3 data)

What G1 needs, and where Exp 3's threshold comes from.

- **G1 is decided only by a completed, valid Exp 1.** "Valid" means: not
  safety-stopped or aborted; full load on every core recorded; full heating
  duration; CPU frequency recorded; no under-voltage flagged; and a complete
  knee record. A knee recorded in an invalid run does not count.
- **PASS:** a valid Exp 1 measured a knee. **FAIL:** a valid Exp 1 found no
  knee. **INCONCLUSIVE:** Exp 1 is invalid or incomplete. **NOT TESTED:** no
  Exp 1 data. A missing or truncated measurement is never a failed hypothesis.
- **If G1 is not PASS or FAIL,** the verdict is `FURTHER EVIDENCE REQUIRED —
  NOT A KILL, NOT A PASS`. G2 and G4 results are still reported, but they do
  not trigger a kill, because without a knee neither can test TDAS.
- **An Exp 2 knee is supporting evidence only.** It never establishes G1. If
  a valid Exp 1 found no knee but Exp 2 did, G1 is INCONCLUSIVE.
- **Exp 3's time-above-knee threshold** comes only from a G1-PASS Exp 1
  (`--exp1-meta`, default `<outdir>/exp1.meta.json`). Exp 3 never substitutes
  Exp 2's knee, and on real hardware it refuses to run without a valid knee.
  `--knee` works only with the mock backend.

Why: the first Exp 1 runs on the bare Pi 4 were a one-core run (the BLAS
library was single-threaded) and a four-core run that hit the 75 °C safety
stop. Neither observed a knee. Under the original wording, both would have
counted as a G1 FAIL and killed TDAS on missing evidence.

### Amendment A2 (2026-10-14, before any Experiment 3 data)

Fixed-work jobs and controlled planned work.

- **Fixed work, not deadlines.** Each job runs a fixed number of compute units
  (one `n_b × n_b` float32 multiply each). Its latency is the time to finish
  them. HOT and COOL use the same kernel. COOL does fewer units plus a
  calibrated sleep. Duty, CPU time and coordination overhead are measured,
  never taken from settings, and no ideal speedup from P = 4 is assumed.
- **Identical job multiset.** Every ordering runs the same jobs: the same number
  of each class, the same planned units per job and the same planned sleep per
  job. Only the order differs. Each run records its achieved units, CPU-seconds
  and sleep.
- **Energy is not measured.** There is no power meter, and the Pi 4 exposes no
  energy counter. Equal planned work does not imply equal energy, because
  energy per unit depends on frequency, voltage and temperature. No equal-energy
  claim is made unless a suitable meter is added and used.
- **Workload validation comes first.** `validate_workload.py` (V0, V1 = P1,
  V2 = P4, V4) checks the contrast and the cold-latency match against
  pre-registered criteria (`PREREG_workload_validation.md`), with exactly 40
  accepted HOT/COOL pairs per configuration and a paired bootstrap. Experiment 3
  refuses to run on real hardware unless the validation for its `--parallel` is
  ACCEPTED and was made with the current `workload.py`. It also still requires
  a valid Experiment 1 knee (A1). V3 (frequency caps) is excluded.

### Amendment A3 (2026-10-16, before any Experiment 3 data and before the next Experiment 1 run)

On the Pi, the throttle knee is read from the firmware ARM clock.

- **Why.** The Exp 1 knee was defined as a drop in `freq_mhz`, which on the Pi
  is Linux `scaling_cur_freq`. A pre-specified instrument check
  (`INSTRUMENT_CHECK_clock_probe.md`, run `clockprobe-20261016-01`, record in
  `provenance/`) found that on this Pi 4 the firmware lowers the ARM clock
  (to 1629 MHz) while `scaling_cur_freq` keeps reading 1800 MHz. Verdict
  BLIND_SPOT: `scaling_cur_freq` cannot show the knee.
- **Measurement.** On the Raspberry Pi backend, Exp 1 records the firmware ARM
  clock (`vcgencmd measure_clock arm`) in every telemetry sample as
  `arm_clock_mhz`. A hung `vcgencmd` gets one SIGTERM, never SIGKILL, and the
  reading counts as unreadable. Other experiments do not record it, so their
  sampling stays as validated. `freq_mhz` keeps its meaning.
- **Knee rule, unchanged except for the clock.** A sample is throttled when
  `arm_clock_mhz` is below 98 % of its maximum over the run. The knee is the
  lowest temperature among throttled samples, and depth is `1 − min/max`. The
  `scaling_cur_freq` knee is still recorded (`knee_sysfs`) for comparison only.
  Other backends keep `freq_mhz`.
- **Validity, added to A1.** On the Pi, an Exp 1 counts for G1 only if its knee
  was measured in `arm_clock_mhz` and the clock was unreadable in at most 5 %
  of samples. Otherwise G1 is INCONCLUSIVE.
- **Earlier runs.** Exp 1 runs made before A3 (`exp1_1core`, `exp1_4core`,
  `exp1-4core-20261016-01`) keep their recorded outcomes. No record is edited.
  They have no ARM clock record, so `analyze.py` treats them as INCONCLUSIVE
  for G1, never as PASS or FAIL. G1 is decided by an A3 run.
- **Not affected.** The workload-validation guard also stops on live throttle
  flags, so it is not blind. Exp 2's knee stays supporting evidence only.
  Exp 3's `time_throttled_s` stays descriptive, and no gate uses it.

### Amendment A4 (2026-10-18, before any Experiment 2 or Experiment 3 data)
Experiment 2 measures s(T) under its own four-core load.
- **Why.** On this Pi the firmware stops throttling within 0.15 s of load ending, so "pre-heat, then probe"
  never sees a throttled probe. The original deadline probes cannot show throttling at all. And one busy
  core reaches only about 65 °C, below the 80.8 °C knee.
- **Measurement.**
  - P4c's accepted HOT job, back to back on four cores: two 12-minute ramps from idle + 3 °C.
  - Per job: temperature before dispatch and latency. The ARM clock is read in-process during load through
    the firmware mailbox, starting no process, about once per second with jitter.
  - Supervision as in Experiment 1, with a fork-free 85 °C watchdog.
- **G2.** The slope of bin-median latency on temperature, in 2 °C bins per ramp with at least 20 jobs per
  bin, with a cluster bootstrap over (ramp, bin). PASS if the 95 % CI is above 0, FAIL if it includes 0,
  INCONCLUSIVE if it is below 0.
- **Validity (as A1 does for G1).** G2 counts only from a completed, valid run: supervised, P4c jobs, a G1
  knee, ≥ 2 bins at or above the knee and ≥ 2 bins at or below knee − 10 °C in each ramp, no
  under-voltage, ARM clock ≥ 95 % readable. Otherwise G2 is INCONCLUSIVE, never FAIL. Pre-A4 exp2 records are
  INCONCLUSIVE.
- **Experiment 3** runs P4c jobs under P4c's validated telemetry: the sysfs logger and the between-job flag
  read. It makes no live ARM-clock reads, because its outcome is tail latency. (The stall control
  `diag-stalls-20261018-03` measured about +0.35 ms on read-overlapped jobs; see `AMENDMENT_A4_exp2.md`.)
Details, checks and evidence: `AMENDMENT_A4_exp2.md`.

---

## Expected confounders

| Confounder | Control |
|---|---|
| Ambient drift over a multi-hour session | counterbalanced + randomized run order; log ambient; discard sets where ambient moves >2 °C |
| DVFS governor changing frequency | pin to `performance` (verified in probe) |
| Fixed-duration rests drifting with ambient | `cool_to()` targets a temperature, never a time |
| Cache/allocator warm-up | first ~20 jobs excluded |
| Background processes | quiet the machine; pin with `taskset` |
| Job-length (SJF/convoy) explanation | cold latency matched by an ACCEPTED workload validation (paired-bootstrap gap within ±5%, A2) |
| Sampling only between jobs | 5 Hz background thread samples independently |
| Mock data mistaken for results | every output labels `mock: true` |

---

## Schedule

**Day 1** — setup, probe, Exp 1, Exp 2. *Exp 2 can end the project today.*
**Day 2** — Exp 3 (~4–6 h unattended), then `analyze.py`.
**Day 3** — if PROCEED: implement B4 (alternation) and B5 (tuned threshold)
and rerun. **TDAS must beat both, not just FCFS.** If KILL: write the
negative result.

---

## Decision template

```
TDAS PILOT RESULT
hardware:            ______________________  ambient: _____ C
idle temp:  _____ C  tau_heat: _____ s  tau_cool: _____ s
knee:       _____ C  throttle depth: _____ %
s(T) slope: _____ ms/C  [CI _____, _____]   excludes zero: Y/N
cold-latency match:  hot _____ ms / cool _____ ms  gap _____ %
chosen k:   _____    tau/horizon: _____

GATES   G1 ___  G2 ___  G3 ___  G4 ___  G5 ___  G6 ___

VERDICT (circle):   KILL   /   REFORMULATE   /   PROCEED

If KILL — which physical assumption failed:
  [ ] P1 service time independent of temperature
  [ ] P2 workloads not thermally distinguishable
  [ ] P3 tau incommensurate with the reordering horizon
  [ ] P4 device never approaches the knee
A replacement candidate must have: a hidden state variable that is
history-dependent, measurable, and causally upstream of the metric —
verified in that order.

If PROCEED — the ONLY claim licensed:
  "We empirically characterize and exploit thermal-throttle hysteresis
   through request-stream ordering on passively cooled edge inference
   hardware, without DVFS control."
Do NOT claim: novel thermal-control mechanism · thermal debt theory ·
first thermal-aware scheduling · universal applicability · superiority
over all thermal schedulers · mechanism-level novelty.
```

---

## If it dies

Report the characterization as a negative result — "we measured the band and
it is empty on this device class" is publishable at workshop level and is
honest. Do not rescue by renaming, adding a neural network, or complicating
the scheduler.

The telemetry logger, cooldown controller, workload harness, and statistics
pipeline all transfer to any subsequent systems project regardless of outcome.
# fog
