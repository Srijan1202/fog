# TDAS hardware pilot: closure (2026-10-18)

**Status: CLOSED.** The pilot's pre-registered verdict is **LIKELY KILL**. The negative result is kept as a
result. No further experiment runs under this pilot. Any follow-up is a new study, with its own pre-registered
hypothesis and success criteria defined before any data (section 5).

## 1. The question and the setup

**Question.** TDAS (`tdas-pilot-protocol.md`) asks whether ordering a fixed multiset of jobs changes tail
latency through the device's thermal state. The proposed mechanism: hot work crosses the throttle knee, the
firmware lowers the clock, and the jobs that run then are slower.

**Setup.**
- **Hardware:** Raspberry Pi 4.
- **Jobs:** the four-core fixed-work P4c HOT and COOL jobs, with equal cold latency (A2, P4c validation).
- **Supervision:** a fork-free external 85 °C watchdog; every run supervised.

**Amendments A1–A7,** each approved before the data it governs:

| amendment | what it settled |
|---|---|
| A1 | validity rules for G1 |
| A2 | "identical multiset" defined |
| A3 | knee measured in the firmware ARM clock |
| A4 | Experiment 2 under its own four-core load, G2 rule |
| A5 | paced warm-up (approved by Claude under the user's delegation) |
| A6 | 240 s back-to-back phase |
| A7 | Experiment 3 design, validity rule, Holm-corrected G5 |

## 2. Gates (all from valid runs)

| gate | result | evidence |
|---|---|---|
| G1 throttle knee (P4) | **PASS**: 80.8 °C, depth 9.5 % | `EXP1_RUN_exp1-4core-a3-20261016-01.md` |
| G2 s(T) rises with T (P1) | **PASS**: +0.208 ms/°C [+0.070, +0.343]; flat ~196 ms below the knee, ~208–212 ms on the plateau | `EXP2_RUN_exp2-a4-20261018-03.md` |
| G3 cold-latency match | **PASS**: HOT 196.3 vs COOL 196.2 ms (gap −0.1 %) | P4c validation, `WORKLOAD_VALIDATION_FOUR_CORE_20261017.md` |
| G4 ordering moves time above the knee | **PASS**: hot_first 13.3 s vs alternating 0 s, difference CI [+6.0, +23.8] s | `EXP3_RUN_exp3-a7-20261018-01.md` |
| G5 ordering moves P99 | **FAIL**: medians 201.3–202.1 ms, no pair survives Holm | same |
| G5b mean / throughput | **FAIL** | same |
| G6 mediation | not testable (needs G5) | same |

`analyze.overall_verdict`: G1–G4 pass, G5 and G5b fail → **LIKELY KILL**: "Thermal trajectory moves but no
performance metric responds. The effect exists physically but is immaterial to anything the paper would claim
to improve."

## 3. The negative result: what is and is not claimed

**Claimed.** On this device, with these jobs, at the tested operating point:
- the operating point is 600-job sequences of about 2 min each, half HOT, starting at 77 °C (3.8 °C below
  the knee);
- reordering an identical multiset changes the thermal trajectory: time above the knee, peak and mean
  temperature;
- it does **not** change P99, P95, mean latency or throughput by any amount the test could resolve. P99
  medians differ by under 1 ms across orderings, and no difference survives the pre-registered correction.

**Why, descriptively:**
- At this horizon throttling is shallow: jobs run while throttled took a median of 202 ms against 196.7 ms
  when cool.
- Only about 2–3 of 600 jobs per sequence exceed 205 ms in any ordering, so P99 (the 6th-largest job) lands
  on ordinary jobs.

**Not claimed:**
- that no operating regime exists where ordering affects latency;
- anything about other devices, other cooling, real inference models, longer horizons or other start
  temperatures. The protocol's three-band start-temperature sweep was not run (A7 chose the near-knee band
  only).

**Disclosures carried with the result:**
- Experiment 2 needed three runs. Run -01 failed A4's coverage rule (a design flaw in A4) and run -02 was
  safety-stopped at 85.2 °C. Both are INCONCLUSIVE and recorded.
- A5 was approved by Claude under the user's delegation while the user was asleep; the user later accepted
  it with A6.
- The status observer runs a few short processes on the Pi every 60 s during runs, as in the accepted P4c
  validation.

## 4. What is kept

- **Code:** branch `exp1-rerun-prep` of `Srijan1202/fog`, up to the session record `0394169`.
- **Run records:** `provenance/`, including:
  - `EXP1_RUN_exp1-4core-a3-20261016-01.md`;
  - `EXP2_RUN_exp2-a4-20261018-01.md`, `-02.md`, `-03.md`;
  - `EXP3_RUN_exp3-a7-20261018-01.md`;
  - the workload-validation records and the instrument check.
- **Amendments:** A4–A7 in their own files; A1–A3 in `README.md`.
- **Evidence:** `D:\dev\tdas-evidence\exp1-rerun-prep\` (`a3/`, `a4/`, `a7/`). Every folder has a
  `SHA256SUMS`, and each run archive's hash matches the copy on the Pi. The raw data also stays on the Pi
  under `~/tdas-pilot/results/`.
- **Reusable regardless of the verdict:**
  - the fixed-work four-core job pair with matched cold latency (P4c);
  - the fork-free supervision stack: watchdog, barrier, launch and status scripts;
  - the in-process firmware clock reader (`fwclock.py`);
  - the paced-warm-up method for filling temperature bins;
  - the integration harnesses.

## 5. Open question: is the longer-duration regime worth its own study?

Experiment 2 shows that sustained four-core load does slow these jobs: about 6–9 % per job after minutes on
the ~83 °C plateau. Experiment 3's horizon was too short to reach that. Whether that regime deserves a new
study is the user's decision. My assessment, for that decision:

**For.**
- The effect per job is real and large enough to matter (~8 %).
- A workload that keeps a device hot for minutes is common for edge inference and batch jobs.
- The measurement stack already exists.

**Against.**
- Over long horizons, every ordering of the same multiset converges toward the same average power and the
  same plateau. The thermal time constant is ~107 s. Ordering then changes only *when* the slow jobs happen,
  not how many there are, unless the hot fraction itself changes. That is a scheduling-policy question, not
  an ordering one.
- "Sustained load throttles a passively cooled SoC" is well known. A new study would add value only with a
  specific, testable ordering claim.
- Long hot blocks run into the 85 °C safety stop: single 85.2 °C readings appear after ~5 min of continuous
  load.

**Recommendation.** Do not start a follow-up by default. Start one only if a concrete application motivates
the long-duration regime. Before any data, its pre-registration must define:

1. **Hypothesis:** for example, "for a k-job multiset with hot fraction h on this device, ordering X reduces
   P99 relative to ordering Y by at least Δ over a horizon of H minutes". The variables are chosen so the
   question is not already answered by average power.
2. **Primary outcome and success criterion:** the metric, the smallest effect that matters (Δ), the test and
   correction, and the sample size from a power calculation. Experiment 3's run-to-run spread in P99 is the
   input for that calculation.
3. **Kill criterion:** the outcome that ends the follow-up, stated as firmly as this pilot's gates.
4. **Operating regime and safety:** horizon, start temperature, hot-block lengths kept below the observed
   ~5-minute 85 °C exposure (or an explicit decision about the watchdog limit), and the cooldown rule.
5. **Validity rules,** as A1, A4 and A7 did: what makes a run unable to decide the outcome.

## 6. Pi state after the pilot

The Pi is idle on boot `5766d426`. Changes the user made for the pilot are still in place; remove them if no
follow-up is planned:

- `/etc/udev/rules.d/99-tdas-vcio.rules` (vcio access for the video group);
- `/usr/local/sbin/tdas-governor`, `/etc/sudoers.d/tdas` (password-free governor script and reboot);
- `/etc/cron.d/tdas-governor` (`performance` governor at boot).

To remove them:

```bash
sudo rm /etc/sudoers.d/tdas /etc/cron.d/tdas-governor /usr/local/sbin/tdas-governor \
        /etc/udev/rules.d/99-tdas-vcio.rules
```
