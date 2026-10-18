# TDAS Hardware Pilot — Go/No-Go Protocol

**Verdict going in: 🟡 RUN THIS PILOT FIRST.** Do not start implementation. Do not run another literature search. This protocol decides whether TDAS gets the next 3–4 months.

---

## PHASE 1 — The exact physical question

### Minimal model

Lumped first-order thermal system, discrete scheduler steps:

```
T(t+Δ) = T_amb + (T(t) − T_amb)·e^(−Δ/τ) + R·P(a_t)·(1 − e^(−Δ/τ))
f(T)   = f_max                    if T < T_knee
       = f_throttled(T) < f_max   if T ≥ T_knee   [hysteresis: release at T_knee − ΔH]
s_i    = w_i / f(T)               ← service time depends on thermal state
```

### What must be true for ordering to matter

All four must hold simultaneously. Any one failing kills TDAS.

| # | Condition | Why required | Fails if |
|---|---|---|---|
| P1 | `ds/dT > 0` in an accessible regime | If service time doesn't depend on temperature, thermal state is decoration | No measurable throttling effect on latency |
| P2 | Requests differ in heat generation (`q_i` heterogeneous) | Nothing to interleave otherwise | All inference workloads heat the device identically |
| P3 | τ comparable to the **reordering horizon** (k·s̄), not to a single request | Too fast → no memory. Too slow → quasi-static, final T is energy-determined and order-independent | τ ≪ s̄ **or** τ ≫ k·s̄ |
| P4 | Device operates **near the throttle knee** | RC is linear; ordering only changes outcomes when a discrete threshold crossing differs between sequences | Device never approaches T_knee under realistic load |

**P3 and P4 are what the earlier audits got wrong or missed.** The old gate ("τ ≳ 5× median service time") passes in exactly the regime where ordering does nothing, because with τ ≫ horizon the temperature is quasi-static across your decision window and a fixed multiset has fixed total energy. Design the pilot around P3 and P4.

> **Amendment A2:** "a fixed multiset has fixed total energy" should read: a fixed multiset has fixed **planned** work. Its total energy is approximately fixed and is not measured. The P3 argument is unaffected.

### Observables required

| Variable | Source (Raspberry Pi — **verify on your device**) | Essential? |
|---|---|---|
| SoC temperature | `/sys/class/thermal/thermal_zone0/temp` (millidegrees) | Yes |
| Core frequency | `/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq` (kHz) | Yes |
| Throttle flags | `vcgencmd get_throttled` (bitfield; low bits live, high bits sticky-since-boot) | Yes |
| Per-request latency | wall clock around each inference | Yes |
| Ambient temperature | external sensor or manual log | Yes (confound control) |
| Power draw | external USB power meter | Optional |

---

## PHASE 2 — Measurement protocol (2–3 days)

### Hardware
- Raspberry Pi 4B or 5, **no heatsink, no fan**, open air, fixed orientation
- Quality power supply (undervoltage flags will corrupt throttle data)
- Ambient thermometer
- Optional: USB power meter

### Software
- Raspberry Pi OS 64-bit, headless
- Python 3, ONNX Runtime or TFLite
- Models: **MobileNetV2 / ResNet-18** (hot, compute-bound) and a deliberately light workload (cool)
- Logger sampling temp / freq / throttle flags at **1 Hz minimum, 5 Hz preferred**

### Critical controls — get these wrong and the pilot is worthless

1. **Pin the governor to `performance`** (`scaling_governor`). Otherwise the `ondemand` governor's DVFS confounds with thermal throttling and you cannot attribute frequency drops to heat. Single most important control.
2. **Cool down to a fixed *temperature*, not a fixed *time*.** Block until `T < T_amb + 3 °C`. Fixed rests drift as ambient changes.
3. **Counterbalance/interleave run order** (A,B,B,A,…). Ambient drifts over hours; block designs confound ambient with condition.
4. **Kill background processes.** Pin with `taskset`, disable auto-updates, no GUI, no network sync during runs.
5. **Discard warm-up.** First ~20 requests per run: log but exclude from statistics.
6. **Log ambient continuously.** If ambient moves >2 °C within a comparison set, discard and rerun.

### Day 1 — Characterization (decides P1, P3, P4)

**1a — Heating step response.** From cold idle, sustained max load. Log T until steady state (≥15 min). Fit `T(t) = T_ss − (T_ss − T_0)·e^(−t/τ_heat)` → **τ_heat, T_ss**

**1b — Cooling response.** From steady hot → idle. Log until baseline. Fit decay → **τ_cool** (expect τ_cool > τ_heat)

**1c — Throttle characterization.** Throughout 1a, log frequency and throttle flags. Plot f vs T → **T_knee, throttle depth (f_throttled/f_max), hysteresis width ΔH**

**1d — Service time vs temperature (THE critical measurement).** Pre-heat to a target temperature, immediately run a *fixed* inference batch, record latency. Repeat at ~5 °C intervals from cold to throttling → **s(T) curve**

> If s(T) is flat, stop. TDAS is dead (P1 fails) and you've spent one day, not four months.

**1e — Per-workload heating rate.** Run each candidate workload in isolation from a fixed start temperature for a fixed duration → **ΔT per request per workload type.** Confirms P2 and identifies genuinely "hot" vs "cool" models.

### Day 2 — The ordering experiment (decides P3, P4 jointly)

Sequences with **identical multisets** — same requests, same total work, same total energy — differing only in order:

> **Amendment A2 (2026-10-14, before any Experiment 3 data):** read "identical multiset" as **the same jobs: the same number of each class, the same planned compute units per job and the same planned sleep per job**, differing only in order. Jobs are fixed-work (latency = the time to finish a fixed number of units), not deadline-based. Each run records its achieved units, CPU-seconds and sleep. **Energy is not measured**: there is no power meter, and the Pi 4 exposes no energy counter. Equal planned work does not imply equal energy, because energy per unit depends on frequency, voltage and temperature. No equal-energy claim is made unless a suitable meter is added and used. Day 2 runs only after an ACCEPTED workload validation (`validate_workload.py`, `PREREG_workload_validation.md`).

```
Sequence A (clustered):   H H H H H C C C C C
Sequence B (interleaved): H C H C H C H C H C
Sequence C (reverse):     C C C C C H H H H H
```

Run each ≥10 times, interleaved and counterbalanced, full cooldown between runs.

**Design the starting temperature to sit near T_knee.** Sweep three starting temperatures: well below the knee, just below, and at the knee. Ordering should show an effect *only* in the near-knee band — and demonstrating that band is itself a publishable characterization.

**Record per run:** full T trajectory; full f trajectory; **time spent above T_knee** (key mediating variable); per-request latency → P50/P95/P99; total throughput; throttle-flag timeline.

> Because A and B have identical total energy, their *final* temperatures will be nearly identical. Any effect must appear in the **trajectory** — specifically time-above-knee. If time-above-knee is identical across orderings, ordering has no mechanism to act through and TDAS dies.

> **Amendment A2:** A and B have identical **planned work**. Their total energy is only approximately equal and is not measured, so "nearly identical final temperatures" is an expectation, not a guarantee. The trajectory argument is unchanged.

### Day 3 — Baselines and statistics

Implement and run: **B0** FCFS · **B1** SJF/workload-aware · **B2** current-temperature-aware ordering · **B4** alternating hot/cool · **B5** threshold ("defer hot if T > θ", sweep θ) · **B6** offline oracle (exhaustive best ordering on a short sequence) · **B7** TDAS.

Report **paired differences with confidence intervals and effect sizes**, not bare p-values. With n=10 you have low power — state that explicitly rather than over-claiming.

---

## PHASE 3 — Identifying regimes from measurement

Define regimes from the **data**, not round-number temperatures:

- **COOL:** s(T) flat, f = f_max, no throttle flags. Ordering provably irrelevant.
- **WARM:** s(T) still flat, f = f_max, but T within ΔH of T_knee. *This is where predictive ordering could pay* — not throttled yet, but a hot burst would cross.
- **HOT:** f < f_max, flags active, s(T) elevated. Reactive schedulers already handle this; TDAS's edge is smaller here.

Check whether a single-pole RC fit explains the cooling curve. **If residuals are structured** (fast early decay, slow tail), the system is second-order (die → board → ambient), and a history-integrating "debt" term genuinely carries information beyond instantaneous T. That is the one way "thermal debt" becomes more than a rename. Fit one-pole vs two-pole and compare residuals explicitly.

---

## PHASE 4 — Non-separability test

**Claim under test:** ordering changes tail latency for an identical request multiset.

**Mediation chain to verify link by link:** ordering → time-above-knee → time-at-reduced-frequency → per-request service time → queue delay → P95/P99.

If ordering changes time-above-knee but **not** tail latency, the thermal effect is real but immaterial — report as a negative result. If it changes **neither**, the physics doesn't support TDAS.

**Confound to pre-empt:** run a control where "hot" and "cool" workloads have *identical* compute cost but different memory-access patterns. If the latency difference persists, it's thermal. If it vanishes, you measured workload heterogeneity — hostile-reviewer attack #7, and fatal.

---

## PHASE 5–6 — Baseline collapse and the surviving contribution

The paper lives or dies on **B5 vs B7**. If a one-parameter tuned threshold matches TDAS everywhere, TDAS is unnecessary complexity → kill.

TDAS survives only if it beats tuned-B5 in a mechanistically explicable regime — most plausibly bursty arrivals near the knee, where a threshold reacts too late because at decision time T is still below θ and only a forecast reveals the impending crossing.

**Narrowest defensible contribution statement:**

> We characterize, on a passively cooled edge device where the application layer has no DVFS authority, the operating band in which request-stream ordering alone measurably changes tail latency under thermal throttling; we show this band is bounded above and below by the relationship between the thermal time constant and the scheduling horizon; and we give an online ordering policy that outperforms threshold-based deferral within that band.

Claim only that. Not a new mechanism (Jayaseelan & Mitra 2008 owns sequencing). Not a new state variable (debt = headroom unless the two-pole fit says otherwise). Not generality beyond the tested device class.

---

## PHASE 7 — Remaining literature check (narrow, ≤2 hours)

Search **only** for: *thermal-aware request ordering for DNN inference without DVFS control*. Named targets to verify in full text: **IceEdge** (ACM ToSN 2026 — closest single-device deadline+thermal neighbor; confirm whether its mechanism is container consolidation rather than request ordering) and **Phoenix** (ACM TECS). If either already does request-stream ordering on one device without frequency control, narrow further or kill. Do not repeat the general sweep.

---

## PHASE 8 — Hostile reviewer

| # | Attack | Fatal? | Experiment that defeats it |
|---|---|---|---|
| 1 | "Just thermal-aware scheduling" | Survivable | Cite Jayaseelan/Coskun/Huang as ancestors; claim only the no-DVFS single-device ordering instantiation |
| 2 | "Just temperature-threshold scheduling" | **Potentially fatal** | B5 sweep over θ; must find a near-knee bursty regime where tuned-B5 loses |
| 3 | "Just MPC with T in state" | Survivable | Concede it; frame as application + characterization, never as a new controller |
| 4 | "Thermal debt is redundant" | Survivable | One-pole vs two-pole residual test; if one-pole, drop the term and the name |
| 5 | "Hardware-specific" | Survivable | Report τ, T_knee, throttle depth so others can locate the band on their hardware |
| 6 | "Effect vanishes controlling queue length" | Survivable | Identical-multiset design already controls this |
| 7 | "It's workload heterogeneity, not thermal" | **Potentially fatal** | Equal-compute/different-thermal control (Phase 4) |
| 8 | "Simple alternation does as well" | **Potentially fatal** | B4 must be beaten, not just B0 |
| 9 | "Throttles too weakly to matter" | **Fatal if true** | Day-1 s(T) curve settles it in hours |
| 10 | "Won't generalize beyond one Pi" | Survivable | Second device class if available; otherwise scope the claim honestly |

Attacks **2, 7, 8, 9** are the real threats. All four are settled by this pilot.

---

## PHASE 9 — Kill gates

**KILL if any of:**
- s(T) flat across the full accessible temperature range (P1 fails)
- Throttling cannot be induced within ~15 min of sustained realistic inference (P4 fails)
- Cooling recovery faster than a single request (P3 fails, low side)
- Identical-multiset orderings produce indistinguishable **time-above-knee** (no causal pathway)
- Tail-latency differences vanish under the equal-compute control (effect was workload heterogeneity)
- Tuned threshold B5 matches TDAS across all tested regimes (unnecessary complexity)

**SURVIVE if all of:**
- s(T) shows clear monotone increase in the throttling regime
- Orderings of identical multisets produce reproducibly different time-above-knee
- That difference propagates to P95/P99 with a confidence interval excluding zero
- Effect persists under the equal-compute control
- TDAS beats both B4 (alternation) and tuned B5 (threshold) in at least one mechanistically explicable regime
- Effect appears on real inference models, not only synthetic stress loads

**Amendment A1 (2026-10-14, before any Experiment 3 data):** "Throttling cannot be induced" (P4) is judged only from a completed, valid Day-1 run: full load on every core, full duration, not safety-stopped or aborted, no under-voltage. A missing, truncated or under-loaded run is inconclusive, never a kill. The Day-1d (s(T)) knee is supporting evidence only. Day 2 takes its time-above-knee threshold only from a valid Day-1 knee and does not run without one. Details are in README.md, Amendment A1.

**Amendment A3 (2026-10-16, before any Experiment 3 data and before the next Experiment 1 run):** On the Raspberry Pi, the Day-1 knee is read from the firmware ARM clock (`vcgencmd measure_clock arm`), not from Linux `scaling_cur_freq`. An instrument check showed that `scaling_cur_freq` stays at 1800 MHz while the firmware lowers the clock. The 98 % rule is unchanged. A Pi Day-1 run counts for P4 only if it recorded that clock, with at most 5 % of readings missing. Earlier runs keep their recorded outcomes but cannot decide P4. Details are in README.md, Amendment A3.

**Amendment A4 (2026-10-18, before any Experiment 2 or Experiment 3 data):** On this Pi the firmware stops throttling within 0.15 s of load ending, so Day-1d s(T) is measured under the job stream's own four-core load: the accepted P4c HOT job, back to back in two ramps, with a fork-free 85 °C watchdog. G2 is the slope of 2 °C bin-median latency on temperature, with a (ramp, bin) cluster bootstrap. It counts only from a valid run that covers the knee; otherwise it is INCONCLUSIVE, never FAIL. Details are in README.md, Amendment A4.

**Amendment A5 (2026-10-18, after Experiment 2 run -01 and before its rerun):** Four cores heat the die through the range below knee − 10 °C in about 12 s, too fast to fill the 2 °C bins that A4's coverage rule needs. Each Experiment 2 ramp therefore starts with a paced warm-up: below knee − 10 °C, a job is dispatched only while the temperature is under the top of the lowest bin with fewer than 30 jobs. The A4 analysis is unchanged. Approved by Claude under the user's delegation. Details are in README.md, Amendment A5.

**Amendment A6 (2026-10-18, after Experiment 2 run -02 and before run -03):** The A5 rerun was safety-stopped by single 85.2 °C readings 5–7 min into back-to-back load. Each Experiment 2 ramp now ends 240 s after its paced warm-up; the 85.0 °C stop and the A4 analysis are unchanged. Approved by the user. Details are in README.md, Amendment A6.

**On thresholds:** deliberately no "≥20% improvement" bar. Report effect sizes with CIs and let magnitude speak. A well-characterized 8% tail improvement with a clean mechanism is a better paper than a hand-waved 30%.

---

## PHASE 10 — Decision

**🟡 RUN THE PILOT.** Days 1a–1d alone (~6 hours) settle attacks #9 and #2 and answer P1/P4. If s(T) is flat or the device won't throttle, kill TDAS that same evening.

**If the pilot passes:** research question = *"In what operating band does request-stream ordering alone, absent DVFS authority, measurably improve tail latency on a passively cooled edge inference device, and does a horizon-aware policy beat threshold-based deferral within that band?"* Baselines B0–B6. Paper structure: motivation → thermal characterization → the band → policy → evaluation → honest limits.

**If the pilot fails:** report the characterization as a negative result (genuinely useful — "we measured the band and it is empty on this device class"), and the next seam within contribution-novel systems work is the **allocator-state/history angle** flagged earlier, which needs no thermal physics and reuses this harness.

---

### Reusable regardless of outcome
The telemetry logger, workload harness, cooldown controller, and statistical comparison pipeline all transfer to any subsequent systems project. Build them cleanly.
