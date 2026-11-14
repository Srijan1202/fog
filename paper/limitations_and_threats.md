# Limitations and threats to validity

This is a companion to Section VI of `paper.md`. Each item states the threat, its evidence or status, and its
likely direction of influence.

## Construct validity

| Threat | Evidence / status | Effect on conclusions |
|---|---|---|
| "Time above knee" is computed from sysfs SoC temperature (≥ 80.828 °C), not from the firmware clock, which was not read live in Exp. 3 (Amendment A4) | In Exp. 1, 477 samples were below 98 % clock. The knee is the lowest temperature among them, so throttling also occurs only at or above it. Above the knee, throttling depth varies. | The mediator measures exposure to the throttling *region*, not actual throttling. |
| P99 per sequence is a near-extreme order statistic | With n = 600, numpy type-7 P99 is the 7th largest, interpolated 1 % toward the 6th. The median number of jobs > 205 ms per sequence was 2.0–2.5. | P99 is insensitive to effects confined to fewer than about 7 jobs per sequence. |
| Synthetic workload | Dense 128×128 float32 GEMM units, P4c-F1, single-threaded reference BLAS | Real inference workloads (memory-bound, accelerators) may respond differently. |
| Energy not measured | No meter; the Pi 4 has no energy counter (Amendment A2) | "Identical multiset" means identical planned work, not identical energy. |

## Internal validity

| Threat | Evidence / status | Effect |
|---|---|---|
| Non-stationary thermal state within the session | Repetitions 0–2 had median cooldowns of 152–262 s and pre-heats of 32–36 s; repetitions 3–9 had 57–75 s and 43–48 s. The cause is unknown; ambient and airflow were not measured. | Counterbalancing within each repetition prevents a systematic bias between orderings but inflates variance, especially for hot_first and cool_first. |
| Serial dependence between sequences | One device; 40 sequences in series | The bootstrap's exchangeability assumption is approximate, and CIs may be too narrow or too wide. |
| Measurement interference | Between-job `vcgencmd get_throttled` (a short-lived process); a status observer starting a few processes every 60 s | Present in all orderings alike. It was characterised during the workload validation (accepted under the same scheme). |
| Supervision overhead | Fork-free watchdog (no processes started in its loop) | Validated in diagnostics: with the fork-free loop, HOT latency CV was 0.84 % against 0.85 % without a poller. |
| Job-length confound | Cold-latency gap −0.050 % [−0.080 %, −0.001 %] | Excluded within this tolerance (G3). |
| G4 contrast selected from the data | Largest minus smallest median, as preregistered | Optimistic for G4. It does not affect the negative G5 result. |

## Statistical conclusion validity

| Threat | Evidence / status | Effect |
|---|---|---|
| Low power for small P99 effects | 10 sequences per ordering. Approximate minimum detectable P99 difference (80 % power, Holm first step, normal approximation, exploratory): 0.9–5.6 ms depending on the pair | Effects below about 1–5 ms could be missed. Non-significance is **not** equivalence. |
| Multiplicity | Holm within each metric (6 pairs); no correction across metrics | Family-wise error is controlled per metric only. |
| Nonparametric bootstrap with n = 10 per group | Percentile intervals with small n can be inaccurate | CIs are approximate. |
| P99 estimation uncertainty within a sequence | Job-level bootstrap (assumes exchangeable jobs; exploratory): median 95 % width 3.3 ms, range 0.9–18.3 ms | Per-sequence P99 values are noisy, and the test compares medians of noisy statistics. |

## External validity

| Threat | Status |
|---|---|
| One device (Raspberry Pi 4 Model B Rev 1.4, 2 GB) | Unit-to-unit variation is not assessed. |
| Cooling configuration | The protocol specifies no heatsink and no fan; an earlier provenance record says "bare board"; no fan is registered with the kernel. Not verified on the Exp. 3 day. |
| One start band (77 °C) and one horizon (600 jobs, about 120 s) | The protocol's three-band start sweep was not run (Amendment A7 chose the near-knee band only). |
| One software stack | Debian 13, kernel 6.18.50, firmware a8698392, Python 3.13.5, NumPy 2.2.4 |

## Process and provenance

| Item | Status |
|---|---|
| Amendment A5 | Approved by the experimenter's assistant under the user's delegation, before rerun data; accepted by the user afterwards (with A6). |
| Experiment 2 runs -01 (coverage rule unmet) and -02 (85.2 °C safety stop) | Both INCONCLUSIVE and recorded. G2 was decided by run -03. |
| Evidence checksums | All data archives and their extracted files match `SHA256SUMS`. In the `a3/` folder, 4 of 132 checksum entries fail: helper scripts (`diag/build_pi_diag.sh`, `diag/t_pi_diag.sh`, `diag/p4c/harness/t_launch_p4b.sh`) edited after their checksums were recorded. No data file is affected. |
| Earlier record wording | The Exp. 2 run -01 record mentions "board and heatsink"; the protocol says no heatsink. The cooling hardware on the day is unverified. The Exp. 3 record says P99 is "the 6th largest"; it is the 7th largest, interpolated 1 % toward the 6th. The run summary rounded G3's gap to "−0.1 %"; the value is −0.050 %. |

**Update on cooling (2026-11-05).** The first author confirmed on 2026-11-05 that no heatsink or fan was fitted during the experiments. This confirmation is after the fact, not a record made on the day.
