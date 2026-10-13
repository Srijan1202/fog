# Pre-registration: fixed-work workload validation (V0, V1, V2, V4)

Fixed on 2026-10-14, before any validation data was collected. Every value
below is a constant in `validate_workload.py` (`PREREG`). A test asserts that
the two match. Each run writes `prereg.json` (these constants plus the sha256 of
this file and of the code) into its run folder **before the first block**.
These values must not change after data is seen. Any re-attempt is a new,
separately labelled run, and every run is reported.

V3 (temporary frequency caps) is **excluded**. Experiment 2 is not part of this.

## Job design (Amendment A2)
- Fixed-work jobs: latency = time to finish a fixed number of compute units.
  One unit = one `n_b × n_b` float32 matrix multiply. HOT and COOL use the same
  kernel.
- HOT: `u_h` units per shard, no sleep. COOL: `u_c = round(d·u_h)` units per
  shard plus a sleep `S` per shard, split equally after each unit.
- `d = 0.35`. Cold HOT latency target `L* = 200 ms`.
- P = 1 and P = 4 are separate job definitions, calibrated separately and
  never compared with each other. P workers are pinned to cores 0..P−1. Each
  job uses all P workers at once, and jobs never overlap.
- Energy is **not measured**; equal planned work does not imply equal energy.

## V0: unit and overhead measurement
| Step | Content |
|---|---|
| V0a | P = 1, for each `n_b ∈ {64, 96, 128}`: 4 jobs × 50 units, each unit timed |
| V0b | P = 4, chosen `n_b`: 10 jobs × 50 units per shard, each unit timed → `t_u(4)` |
| V0c | 50 zero-unit jobs at P = 1 and 50 at P = 4 → `o(1)`, `o(4)` (median latency) |

**Choosing `n_b`.** A size qualifies if its median unit time is ≥ 2.0 ms and
the coefficient of variation (CV) of its unit times is ≤ 2%. Among qualifying
sizes, choose the one with the smallest median overhead fraction, where
overhead fraction = 1 − compute time / shard wall time. If no size qualifies,
V0 fails, the configurations are not run, and validation is **INCOMPLETE**.
`t_u(1)` = median unit time of the chosen size in V0a.

## Calibration (per configuration)
- `u_h = round((L* − o(P)) / t_u(P))` and `u_c = round(d·u_h)`, each at least 1.
- `S0 = (u_h − u_c)·t_u(P)`.
- Two calibration blocks of 10 pairs each (C1 with `S0`, C2 with `S1`). After
  each block, `S ← S − (median COOL latency − median HOT latency)`, using pairs
  6–10. S is clamped at 0, and any clamp is recorded.
- After C2, `u_h`, `u_c` and `S` are **frozen**. Calibration jobs are never used
  as acceptance data.

## Acceptance schedule
- A pair is two consecutive jobs: pair k runs HOT then COOL when k is even and
  COOL then HOT when k is odd. k counts from 0 within each block.
- The first **5 pairs of every block** are excluded as warm-up, by position
  only. There is no other outlier removal.
- P = 1: acceptance blocks of 20, 20 and 15 pairs give 15 + 15 + 10 = **40**
  accepted pairs. P = 4: 4 blocks of 15 pairs give 4 × 10 = **40**.
- Telemetry is **on** (5 Hz) for all calibration and acceptance blocks.

## Guards and invalid blocks
- Each block starts within **2 °C** of the idle baseline. The baseline is the
  median over a 30 s idle window at the start of the run. If that is not reached
  within **10 minutes**, validation stops and is INCOMPLETE.
- A block is **invalid** if any telemetry sample during it shows
  T ≥ **65 °C**, a frequency below **1800 MHz** (or unreadable), or a live
  throttle or under-voltage flag (bits 0–3); or if the before/after audit finds
  a new sticky flag (bits 16–19); or on a worker failure; or if a shard does not
  reply within **5 s**.
- Invalid blocks are discarded, with their raw data kept under `discarded/`,
  and rerun at the same size and position. **At most 2 reruns per
  configuration**, counting calibration and acceptance together. Each
  re-executed block counts, including re-collection of stale acceptance blocks.
  V0 has its own limit of 2 reruns. V4 has 1.
- If a calibration block is found invalid after freezing, the frozen
  parameters become **STALE** and are never used again. Acceptance blocks
  collected with them become stale and are not counted. C1–C2 are rerun, the
  parameters are re-frozen, and acceptance restarts at block 1.
- If a rerun would exceed the limit, the configuration is **INCOMPLETE**. It is
  never classified, and it is never analysed with fewer than 40 pairs.
- A failed criterion is a **failure**, never an invalid block, and is never
  rerun.
- The external on-Pi monitor (5 s) stops the run at **67 °C** as a backup.

## Statistics: cold-latency gap (A5), paired bootstrap
- Data: the 40 accepted pairs `(hᵢ, cᵢ)` in time order.
- `ĝ = median(c) / median(h) − 1`.
- `rng = numpy.random.default_rng(0)`. For b = 1…10 000:
  `idx = rng.integers(0, 40, size=40)`;
  `g_b = median(c[idx]) / median(h[idx]) − 1`.
- `lo, hi` = the 2.5th and 97.5th percentiles of `g_b` (numpy default linear
  interpolation).
- **TARGET MET**: −0.02 ≤ lo and hi ≤ +0.02. **WITHIN HARD LIMIT**:
  −0.05 ≤ lo and hi ≤ +0.05, but not TARGET MET. **FAIL**: otherwise.
  Unrounded values, inclusive bounds.
- Reported but not used for the decision: the median of
  `rᵢ = (cᵢ − hᵢ)/hᵢ` (same draws), Cliff's δ, the p95 gap, and ĝ per block.
- **Limitation.** The interval treats the 40 pairs as interchangeable.
  Dependence within and between blocks (drift, cache or idle state, ambient)
  is not modelled, so the interval may be too narrow. Per-block ĝ is a
  diagnostic only: it does not remove this limitation and cannot change the
  classification.

## Acceptance criteria (all must hold for ACCEPTED)
| # | Criterion | P = 1 | P = 4 |
|---|---|---|---|
| A1 | `units_done == units_planned`, every job in every valid acceptance block (including warm-up) | 100% | 100% |
| A2 | HOT median realised duty (CPU / (latency × P)) | ≥ 0.95 | ≥ 0.90 |
| A3 | COOL median realised duty within ±0.05 of d × HOT median realised duty | yes | yes |
| A4 | HOT/COOL ratio of median CPU per job: 2.5th percentile over the same paired draws | ≥ 2.0 | ≥ 2.0 |
| A5 | Cold-latency gap class | TARGET MET or WITHIN HARD LIMIT | same |
| A6 | CV of unit time (accepted HOT jobs); CV of HOT latency | ≤ 2%; ≤ 3% | ≤ 3%; ≤ 4% |
| A7 | Every counted block valid (by construction) | yes | yes |
| A8 | Median overhead / latency over the accepted jobs | ≤ 5% | ≤ 5% |
| A9 | Median imbalance (max shard wall − mean shard wall) / max shard wall | – | ≤ 5% |

CV = sample standard deviation / mean. Outcomes are **ACCEPTED** (all hold),
**REJECTED** (any fails), or **INCOMPLETE** (rerun limit or cooldown cap, or V0
failed). Experiment 3 runs on real hardware only with an ACCEPTED configuration.

## V4: telemetry comparison (descriptive only)
Two back-to-back P = 1 blocks of 20 pairs using the frozen P = 1 parameters:
telemetry off, then on. 15 accepted pairs each. Reported: the relative shift in
median HOT and median COOL latency, with an independent bootstrap interval
(10 000 resamples, seed 0).
**DESCRIPTIVE ONLY. Not used in any acceptance decision.** The blocks run one
after the other, so any difference is confounded with block order and time
drift. Without the 5 Hz logger, the live guard reads temperature and frequency
from sysfs after each job, and throttle flags are checked by the before/after
audit.
