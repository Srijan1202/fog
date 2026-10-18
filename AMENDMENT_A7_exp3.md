# Amendment A7: Experiment 3 under four-core load

**Status: approved by the user on 2026-10-18, before any Experiment 3 data.** Drafted after G1 PASS (knee
80.8 °C) and G2 PASS (`exp2-a4-20261018-03`). The user chose all four open decisions as recommended:

- `k = 600`;
- a single start band at 77 °C;
- one long session;
- a Holm correction for G5.

A7 builds on A1–A6. It replaces Experiment 3's earlier defaults (k = 60, no pre-heat, the full telemetry
scheme). Those cannot test TDAS on this Pi at four cores.

## 1. What the earlier experiments say about the design

| fact | value | source |
|---|---|---|
| HOT job (P4c-F1): 4 cores × 13 four-GEMM units, no sleep | ~196 ms when cold, 4 cores busy | P4c, Exp 2 |
| COOL job: 4 cores × 5 units + 119.55 ms sleep per shard | ~196 ms when cold (gap −0.05 %), duty ≈ 38 % | P4c |
| knee (G1) | 80.828 °C (ARM clock below 98 %) | Exp 1 A3 |
| s(T) | flat ~196.3 ms below the knee; ~208 ms at 83 °C, ~212 ms at 84 °C | Exp 2 run -03 |
| back-to-back HOT heating | 46 → 70 °C in 12–23 s; 76 °C after ~40–60 s; knee after ~90–120 s; plateau ~83 °C | Exp 2 |
| 85.0 °C safety stop | single 85.2 °C readings appear ≥ 5 min into back-to-back HOT load | Exp 2 runs -01/-02 |
| cooling | τ ≈ 107 s; 80 → 47 °C took 250–330 s | Exp 1, Exp 2 |

Consequences for the design:

- k = 60 (12 s) never reaches the knee from a cold start, so G4 would fail by construction.
- The protocol asks for starts near the knee, so each sequence needs a pre-heat.
- HOT blocks must stay well short of 5 min to avoid the 85.0 °C stop.

## 2. Design

- **Jobs.** P4c's HOT and COOL jobs (ACCEPTED validation `wv-p4c-20261017-01`, frozen P4c-F1), as A2
  requires: the same multiset in every ordering. Achieved units, CPU time and sleep are recorded per sequence.
- **Sequence.** `k = 600` jobs, half HOT: 300 H + 300 C, about 120 s.
  - τ/horizon = 107 s / 120 s ≈ 0.9, in P3's usable range.
  - A hot-first sequence runs 60 s of continuous HOT.
- **Orderings.** `hot_first`, `cool_first`, `alternating`, `random`: the protocol's A, C and B, plus random.
  `random` draws a new permutation per sequence from the session generator (seed 0).
- **Start of each sequence:**
  1. Cooldown with no load to idle + 3 °C (A4's rule; cap 20 min; if not reached, the sequence is invalid).
  2. **Pre-heat** with back-to-back HOT jobs, not part of the sequence. It ends as soon as the sysfs
     temperature read before a job is at least **77.0 °C** (knee − 3.8 °C, rounded down: "just below the
     knee"). Cap 5 min; if not reached, the sequence is invalid.
  3. The sequence starts at once, with no gap.
- **Repetitions.** 10 per ordering, 40 sequences. The order is interleaved and counterbalanced by the
  existing schedule: alternate blocks reversed, shuffled within each block, seed 0.
- **Telemetry** (P4c's scheme, A4):
  - the 5 Hz sysfs logger during sequences and pre-heats;
  - `vcgencmd get_throttled` between jobs;
  - **no live ARM-clock reads.**
- **Mediator.** `time_above_knee_s` = (5 Hz samples within the sequence with sysfs temperature ≥ 80.828 °C)
  × 0.2 s, as `exp3_ordering.py` already defines it.
- **Supervision** (as Experiment 2):
  - a fork-free 85.0 °C watchdog, `tools/exp3_monitor.sh`, derived from `tools/exp2_monitor.sh`;
  - `--require-monitor` with a fresh outdir; launch and status scripts;
  - one SIGTERM at most, never SIGKILL.
- **One session.** 40 sequences of about 8 min each (cooldown ~5 + pre-heat ~1 + sequence ~2): about 5.5 h.
  The watchdog cap is 7 h (25200 s).

## 3. Validity (as A1 for G1 and A4 for G2)

- **A sequence is valid if all of these hold:**
  - its cooldown and pre-heat met their targets within their caps;
  - all 600 jobs completed their units;
  - no under-voltage (live flags between jobs; sticky audit before and after the sequence);
  - it was not interrupted.
- **Invalid sequences** (other than an interrupted run) are rerun once, at the end of the session, with the
  same (rep, ordering).
- **G3–G6 are decided only if:**
  - the session completed (not aborted, safety-stopped or interrupted);
  - it was supervised;
  - P4c jobs were used;
  - G1 passed;
  - every ordering has **at least 8 valid sequences**.

  Otherwise G3–G6 are INCONCLUSIVE, never FAIL. Invalid sequences never enter the analysis.

## 4. Analysis (`analyze.py`)

- **G3** (cold-latency match): from the P4c validation, as before. Gap −0.05 %, TARGET MET.
- **G4** (mediator), as written before any Experiment 3 data:
  - the largest minus the smallest median `time_above_knee_s` across orderings;
  - percentile-bootstrap CI (10000 resamples, seed 0);
  - PASS if it excludes 0.
- **G5** (outcome), as written, **plus a Holm correction:**
  - For each of the 6 ordering pairs, the difference in median per-sequence P99, with the existing bootstrap.
  - Each pair also gets a two-sided bootstrap p-value from the same resamples:
    `p = min(1, 2 · min(P*(Δ ≤ 0), P*(Δ ≥ 0)))`.
  - The 6 p-values are Holm-adjusted at α = 0.05.
  - **PASS iff at least one pair survives Holm.**
  - Mean and throughput stay secondary (G5b), also Holm-adjusted within each metric.
- **G6** (mediation): corr(`time_above_knee_s`, P99) > 0.3 over the valid sequences, tested only if G4 and G5
  pass. Unchanged.
- **Verdict:** `analyze.overall_verdict`, unchanged.

## 5. Work after approval (each Pi step asks first, as before)

- `exp3_ordering.py` (four-core path for configuration 4c):
  - P4c telemetry;
  - cooldown, pre-heat and validity per sequence;
  - one rerun of invalid sequences;
  - the barrier (`Exp3Guard`) and the aborted record.
- Supervision scripts: `tools/exp3_monitor.sh`, `tools/launch_exp3.sh`, `tools/status_exp3.sh`.
- `analyze.py`: the A7 validity rule for G3–G6 and the Holm correction.
- Tests, the integration harness, an install on the Pi, then the run.

## 6. README text

> ### Amendment A7 (2026-10-18, before any Experiment 3 data)
> Experiment 3 runs under four-core load: P4c's HOT and COOL jobs, `k = 600` (300 + 300, about 120 s), in
> four orderings × 10 interleaved repetitions. Each sequence starts after a cooldown to idle + 3 °C and a HOT
> pre-heat to 77 °C, just below the 80.8 °C knee. Telemetry is P4c's scheme with no live clock reads, under
> a fork-free 85 °C watchdog, in one supervised session of about 5.5 h. G3–G6 count only if every ordering
> has ≥ 8 valid sequences. G5's six pairwise P99 tests are Holm-corrected. Details: `AMENDMENT_A7_exp3.md`.
