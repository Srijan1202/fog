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
  └─ tau/horizon far from 1 for all feasible k ─────────────► KILL (P3)
       │
       ▼
exp2_curve.py      → s(T)
  └─ slope CI includes zero (s(T) flat) ────────────────────► KILL (P1)
       │
       ▼
exp3_ordering.py   → identical-multiset orderings
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
python3 exp1_thermal.py --minutes 15 --outdir results     # Exp 1  (~30 min)
python3 exp2_curve.py --stages 8 --outdir results         # Exp 2  (~2 h)
python3 exp3_ordering.py --reps 10 --k 60 --outdir results  # Exp 3 (~4-6 h)
python3 analyze.py --outdir results                 # verdict
```

Pipeline check without hardware (**simulated — never report as a result**):

```bash
python3 probe.py --backend mock
python3 exp1_thermal.py --backend mock --minutes 2 --cool-minutes 2 --outdir results_mock
```

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

HOT and COOL both run to the same wall-clock deadline, so they have
**identical cold latency**; COOL simply duty-cycles (default 0.35) and
therefore draws less average power. Two job classes indistinguishable in
service time on a cold device cannot produce an ordering effect through
SJF / queueing / convoy mechanisms. The only remaining channel is thermal.

`exp3` verifies this empirically before running and warns if the gap
exceeds 5%. If it does, re-tune `--target-ms` / `--cool-duty`.

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
| G1 | throttle knee exists (P4) | no frequency drop under sustained load |
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

---

## Expected confounders

| Confounder | Control |
|---|---|
| Ambient drift over a multi-hour session | counterbalanced + randomized run order; log ambient; discard sets where ambient moves >2 °C |
| DVFS governor changing frequency | pin to `performance` (verified in probe) |
| Fixed-duration rests drifting with ambient | `cool_to()` targets a temperature, never a time |
| Cache/allocator warm-up | first ~20 jobs excluded |
| Background processes | quiet the machine; pin with `taskset` |
| Job-length (SJF/convoy) explanation | matched cold latency by construction, verified |
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
