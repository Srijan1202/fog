# Evidence audit: manuscript claims → sources

## Source locations

| Symbol | Location |
|---|---|
| `<EV>` | the evidence folder (`exp1-rerun-prep/` of the TDAS evidence store) |
| `<REPO>` | the code repository, branch `exp1-rerun-prep` |
| `VN` | `paper/analysis/verified_numbers.json`, written by `paper/analysis/reproduce_stats.py` from the raw archives |
| `VN.log` | that script's log (`paper/analysis/verified_numbers.log`) |

## Archives (SHA-256)

Every archive's SHA-256 matches the hash recorded at retrieval from the device and its folder's `SHA256SUMS`:

| Archive | Contents | SHA-256 |
|---|---|---|
| `<EV>/a3/run_exp1-4core-a3-20261016-01.tar` | Exp. 1 (G1) | `a84997e7…` |
| `<EV>/a4/run_exp2-a4-20261018-01.tar` | Exp. 2 run -01 (INCONCLUSIVE) | `c7d8fec3…` |
| `<EV>/a4/run_exp2-a4-20261018-02.tar` | Exp. 2 run -02 (INCONCLUSIVE) | `4329aad2…` |
| `<EV>/a4/run_exp2-a4-20261018-03.tar` | Exp. 2 run -03 (G2) | `5bb8d13a…` |
| `<EV>/a7/run_exp3-a7-20261018-01.tar` | Exp. 3 (G3–G6 inputs) | `4e4ae7f6…` |
| `<EV>/a3/diag/p4c/run_extract/results/workload_validation/wv-p4c-20261017-01/report.json` | P4c validation (G3) | `d50f152b…` |

**Status legend:**
- **R:** independently recomputed from raw records by `reproduce_stats.py`, and equal to the official recorded
  value (tolerance 1e-6 or exact).
- **O:** taken from an official record or analysis output, checked for consistency but not recomputed.
- **X:** exploratory, computed here and labelled as such in the paper.
- **D:** documentary (a configuration or record statement).
- **U:** unverified.

## Abstract and Introduction

| # | Claim | Value in paper | Source | Status |
|---|---|---|---|---|
| 1 | Device model, OS | Pi 4 Model B Rev 1.4, Debian 13 | `paper/analysis/pi_environment.txt` (read-only query, same boot as Exp. 3) | D |
| 2 | HOT / COOL duty | 0.994 / 0.385 | P4c `report.json` → `configs.4c.evaluation.values`; `VN.G3.values` | R (read) |
| 3 | Cold-latency gap | −0.050 % [−0.080 %, −0.001 %] | `report.json` → `gap`; `VN.G3.gap` | R (read) |
| 4 | 40 sequences, 24,000 jobs, 10 per ordering, 300/300 | as stated | `exp3.meta.json` and `exp3.jobs.jsonl`; `VN.exp3_session` (jobs_total 24000, n_valid, ordering_structure_ok true) | R |
| 5 | Start temperatures 77.4–77.9 °C | 77.419–77.906 | `VN.exp3_session.start_temp_range` | R |
| 6 | Knee 80.8 °C | 80.828 | Exp. 1 telemetry; `VN.G1.knee_c` = record | R |
| 7 | G2 slope +0.208 [+0.070, +0.343] | 0.20800 [0.06964, 0.34327] | Exp. 2 run -03 jobs; `VN.G2` (matches record, bins identical) | R |
| 8 | ~196.4 ms below the knee; 208–212 ms on the plateau | 196.23–196.55; 207.94–212.15 | `VN.G2.bins` | R |
| 9 | G4: 13.3 s vs 0 s, CI [6.0, 23.8] | 13.3 [6.0, 23.8] | `VN.G4` (identical to `analyze.boot_diff`) | R |
| 10 | P99 medians 201.3–202.1 ms | 201.257–202.136 | `VN.exp3_per_ordering.p99_ms` | R |
| 11 | No pair survives Holm | all `survives_holm` false | `VN.G5.p99_ms` | R |
| 12 | Verdict LIKELY KILL | `LIKELY_KILL` | `analyze.main()` on the three records; `VN.official_verdict` | R |
| 13 | HOT jobs at/above the knee +0.7 % | 198.29 vs 196.87 ms | `VN.exploratory.hot_latency_by_knee` | X |
| 14 | 2–3 of 600 jobs > 205 ms | medians 2.0–2.5 | `VN.exploratory.jobs_over_205ms` | X |
| 15 | First-hour P99 205–211 ms for blocked orderings | 205.47–210.81 | `VN.exploratory.by_rep`, `hottest_sequences` | X |

## Section III (Methods)

| # | Claim | Source | Status |
|---|---|---|---|
| 16 | Kernel 6.18.50+rpt-rpi-v8, firmware a8698392 (Sep 14 2026), `arm_freq` 1800, governor `performance`, 1.8 GiB, no `temp_limit`/`temp_soft_limit` override | `pi_environment.txt`; `<REPO>/provenance/PROVENANCE.md` (runtime environment) | D |
| 17 | Python 3.13.5, NumPy 2.2.4, single-threaded reference BLAS | `pi_environment.txt`; `PROVENANCE.md` | D |
| 18 | Cooling: no heatsink and no fan, open air | `<REPO>/tdas-pilot-protocol.md` (setup); `PROVENANCE.md` "bare board" (2026-10-14); no fan cooling device in `pi_environment.txt` | **U** for the Exp. 3 day |
| 19 | Unit = 4 GEMMs of 128×128; HOT 13 units; COOL 5 units + 119.55 ms sleep | `report.json` frozen `P4c-F1`; `VN.G3.frozen` | R (read) |
| 20 | Unit-time CV 0.83 %, HOT-latency CV 0.35 % | `report.json` values (`unit_cv` 0.00827, `hot_latency_cv` 0.00346) | O |
| 21 | Orderings, schedule (seed 0, blocks) | `exp3.meta.json` `schedule`, `plan`; `<REPO>/exp3_ordering.py` (`make_schedule`) | R |
| 22 | Cooldown target 48.3 °C (idle 45.28 + 3) | `exp3.meta.json` `idle_temp_c`, `plan.margin_c` | R |
| 23 | Pre-heat 31–53 s; sequence 119.6–120.4 s | `VN.exp3_session.preheat_s_range`; `per_sequence.wall_s` | R |
| 24 | 5 Hz logger, 23,843 samples; 0.487 °C quantisation | `exp3.telemetry.jsonl`; Exp. 1 telemetry steps | R |
| 25 | Watchdog rules (85.0 °C, single SIGTERM, fork-free) | `<REPO>/tools/exp3_monitor.sh`; `monitor.log` in the archive | D |
| 26 | Knee rule (98 %, firmware clock, A1/A3) | `<REPO>/README.md` A1, A3; `analyze.classify_g1`; `exp1_thermal.find_knee` | D |
| 27 | Gate definitions, A7 validity, verdict mapping | `<REPO>/AMENDMENT_A4_exp2.md`, `AMENDMENT_A7_exp3.md`, `analyze.py` (`g2_*`, `e3_invalid`, `holm`, `gates_4_to_6`, `overall_verdict`) | D |
| 28 | P99 = 7th largest + 1 % toward the 6th (n = 600) | `VN.exploratory.p99_estimator` | R |
| 29 | Per-sequence metrics reproduce to 1e-6 | `VN.exp3_session.per_sequence_mismatches_vs_record` = [] | R |
| 30 | `scaling_cur_freq` 1800 MHz in all 6,894 Exp. 1 samples | Exp. 1 telemetry; `exp1.meta.json` `knee_sysfs` | R |
| 31 | P4b stalls traced to the watchdog's process starts (8 of 8 stalls on poll-overlapped jobs) | `<REPO>/provenance/WORKLOAD_VALIDATION_FOUR_CORE_20261017.md` | O |
| 32 | Exp. 2 runs -01 / -02 INCONCLUSIVE (coverage; 85.2 °C stop) | `<REPO>/provenance/EXP2_RUN_exp2-a4-20261018-01.md`, `-02.md`; archives `c7d8fec3`, `4329aad2` | O |

## Section IV (Results)

| # | Claim | Source | Status |
|---|---|---|---|
| 33 | Exp. 1: 6,894 samples, 0 missing; 1800.5 → 1629.4 MHz; depth 9.5 %; 477 throttled samples; peak 83.75 °C; τ_cool 106.7 s | `VN.G1` | R (τ: O, from `exp1.meta.json fit_cooling`) |
| 34 | Exp. 1 cores busy 99.9–100 % | `exp1.meta.json` `cpu_util_heating` | O |
| 35 | Exp. 2 run -03: 3,027 jobs, 36 bins, two ramps | `VN.G2` | R |
| 36 | Bins below the knee 196.23–196.55 ms (30 bins, 47.7–79.4 °C); 197.4–198.0 at 81.3–81.8 °C | `VN.G2.bins` (recomputed) | R |
| 37 | Session valid, no reruns, no under-voltage, cleanup verified | `VN.exp3_session`; `<EV>/a7/status_1.log` (cleanup verified) | R / O |
| 38 | Table I, all values | `VN.exp3_per_ordering` | R |
| 39 | Table II, all values | `VN.G5.p99_ms` | R |
| 40 | Smallest p for mean (0.037) and throughput (0.045) | `VN.G5.mean_ms`, `VN.G5.throughput_jobs_s` | R |
| 41 | Max median P99 difference 0.88 ms (0.44 %) | `VN.G5.p99_ms` (alternating − hot_first) | R |
| 42 | G6 not testable | `analyze.py` `gates_4_to_6`; `VN.official_verdict.gates.G6_mediation` = null | R |

## Section V (Discussion; exploratory unless noted)

| # | Claim | Source | Status |
|---|---|---|---|
| 43 | 1,258 of 12,000 HOT jobs started at or above the knee; median 198.29 vs 196.87; p90 204.99 vs 198.98 | `VN.exploratory.hot_latency_by_knee` | X |
| 44 | hot_first median 60 jobs per sequence started at or above the knee | `VN.exploratory.jobs_started_at_or_above_knee` | X |
| 45 | Jobs > 205 ms per sequence: 2.5 / 2.5 / 2.0 / 2.5; > 200 ms: 14.5–18 | `VN.exploratory.jobs_over_205ms`, `jobs_over_200ms` | X |
| 46 | Jobs > 215 ms: 11–15 per ordering, mostly started below the knee | `VN.exploratory.jobs_over_215ms_*` | X |
| 47 | Within-sequence P99 CI width: median 3.3 ms | `VN.exploratory.within_sequence_p99_ci_width_ms` | X |
| 48 | MDE 0.9–5.6 ms | `VN.exploratory.approx_mde_ms_80pct_power_holm_first_step` | X |
| 49 | Session shift: reps 0–2 vs 3–9 (cooldowns, pre-heats, times above the knee, P99 ranges) | `VN.exploratory.by_rep`; `per_sequence`; Fig. 7 | X |
| 50 | Pooled P99 204.1 / 207.9 / 202.2 / 201.6 | `VN.exploratory.pooled_p99_ms` | X |

## Figures

| Fig. | Generated from | Script |
|---|---|---|
| 1 | Exp. 1 telemetry (archive `a84997e7`) | `paper/analysis/make_figures.py` |
| 2 | Exp. 2 run -03 `exp2.meta.json` bins and g2 (archive `5bb8d13a`) | same |
| 3 | Exp. 3 telemetry (archive `4e4ae7f6`) | same |
| 4, 5 | Exp. 3 `exp3.meta.json` runs (recomputed values equal the record) | same |
| 6, 7 (exploratory) | Exp. 3 jobs and telemetry | same |

## Corrections to earlier project statements, found during this audit

1. **G3 gap.** Stated as "≈ −0.1 %" in the run summary. The value is **−0.050 %**; `analyze.py` prints it
   rounded to one decimal.
2. **P99 definition.** Described as "the 6th largest of 600" in the Exp. 3 record. It is the **7th largest,
   interpolated 1 % toward the 6th** (numpy type 7).
3. **Heatsink wording.** The Exp. 2 run -01 record mentions "board and heatsink". The protocol specifies no
   heatsink, and the hardware on that day is unverified (claim 18).
4. **Checksums.** 4 of 132 entries in `<EV>/a3/SHA256SUMS` fail (helper scripts edited after hashing). No data
   file is affected.

## Unverified or unavailable

- **Cooling hardware on the Exp. 3 day** (claim 18): not recorded at the time. The first author confirmed on 2026-11-05 that no heatsink or fan was fitted during the experiments. This confirmation is after the fact, not a record made on the day.
- **Ambient temperature and airflow:** not measured. The cause of the within-session shift is unknown.
- **Firmware ARM clock during Exp. 3:** not read, by design (A4).
- **Raw data of the Exp. 1 and Exp. 2 runs other than those listed:** not used.
- **Earlier exploratory runs** (`exp1_1core`, `exp1_4core`, pre-A3): not used for any claim.
