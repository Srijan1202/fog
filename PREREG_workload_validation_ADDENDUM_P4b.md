# Pre-registration addendum: workload configuration P4b (four cores)

Status: approved by the user on 2026-10-17, before any P4b data. Nothing in it has run.
`PREREG_workload_validation.md` and its acceptance criteria are **unchanged**; this addendum adds one
new configuration and the rules for running it.

## 1. Why a new configuration

In `wv-20261015-01` the P=4 configuration (`P4-F1`) was REJECTED on A6: the unit-time CV was 6.68 %
against a limit of 3 %. That verdict is final and stays on record. Experiment 3 needs four busy cores to
reach the throttle knee: one core levels off at about 65 °C, while the A3 knee is 80.8 °C
(`DESIGN_exp2_exp3_4core.md`). P4b is therefore a **new configuration**, not a rerun of `P4-F1`.

Its design rests on a non-confirmatory diagnostic, `diag-p4-20261017-03` (commit `a9b2512`; evidence
`exp1-rerun-prep/a3/diag/`). That run used `P4-F1`'s HOT job, cooled before every block, and interleaved
three telemetry conditions:

| condition | context switches per job (median) | unit-time CV | units > 1.5× median |
|---|---|---|---|
| T0 no telemetry | 3.5 | 3.13 % | 0.09 % |
| T1 sysfs temperature only, 5 Hz | 8 | 3.15 % | 0.11 % |
| T2 validation telemetry (`vcgencmd` per 5 Hz sample) | 23 | 4.72 % | 0.21 % |

Two causes follow:
1. **Telemetry.** Starting a `vcgencmd` process inside jobs adds most of the interruptions.
2. **Short units.** Even without telemetry, occasional 2.5–3 ms operating-system interruptions put the
   CV of 3.8 ms units near the 3 % limit.

Summing k consecutive GEMMs from the same data gave a CV of about 1.8 % for k = 4 (T0/T1), and 2.7 % for
k = 4 with T2 telemetry.

**Disclosure.** P4b was designed after seeing how A6 behaves on these data. The k = 4 figure comes from
regrouping data already seen, not from a test. The criteria are not changed, and this validation run is
the test.

## 2. Configuration P4b (fixed now)

| item | P4b |
|---|---|
| workers | P = 4, worker i pinned to core i (as P=4) |
| unit | **4 consecutive 128×128 float32 matrix multiplies, timed as one unit** (`gemms_per_unit = 4`) |
| unit size | n_b = 128, fixed; there is no V0a size selection |
| HOT / COOL | same kernel; COOL does fewer units plus a calibrated sleep, as before |
| u_h, u_c, S0 | u_h = max(1, round((200 ms − o(4)) / t_u(4))); u_c = max(1, round(0.35 · u_h)); S0 = (u_h − u_c) · t_u(4) |
| calibration | C1, C2: 10 pairs each, warm-up 5, then sleep adjustment (unchanged) |
| acceptance | blocks of 15, 15, 15, 15 pairs, warm-up 5 per block → 40 accepted pairs (unchanged) |
| reruns | 2 per configuration (unchanged) |

### V0 for P4b
- **V0b′:** 10 P=4 UNIT jobs of 50 units each (four-GEMM units), giving t_u(4), the median unit time.
- **V0c′:** 50 P=4 ZERO jobs, giving o(4), the median coordination overhead.

There is no V0a and no P=1 work. If V0 is incomplete, P4b is INCOMPLETE.

### Telemetry during P4b blocks
- **During jobs:** a 5 Hz logger reads only sysfs (temperature and `scaling_cur_freq`). It starts no
  process.
- **Between jobs:** one `vcgencmd get_throttled` per job, outside every latency measurement. It feeds
  the live-flag guard: any live throttle or under-voltage bit makes the block invalid, as now.
- **Around blocks:** the sticky-bit audit before and after each block is unchanged.
- Guards (65 °C, sysfs frequency ≥ 1800 MHz), cooldown to idle + 2 °C, the external 67 °C watchdog,
  supervision and signal rules are all unchanged.

## 3. Decision (unchanged criteria)

Criteria A1–A9 with the **P = 4 column** of `PREREG_workload_validation.md`: A2 ≥ 0.90; A6 ≤ 3 % unit-time
CV and ≤ 4 % HOT latency CV; A9 ≤ 5 %. The gap classes, paired bootstrap (10 000 resamples, seed 0)
and statistics limitation are the same.

- **ACCEPTED:** all criteria hold. **REJECTED:** any criterion fails. **INCOMPLETE:** rerun limit,
  cooldown cap or V0 incomplete.
- A REJECTED P4b is final. No further four-core variant is run without a new written pre-registration,
  and every rejection is reported.

## 4. Consequences

- **If ACCEPTED:** Experiments 2 and 3 use P4b's frozen parameters and the same telemetry scheme during
  jobs. Exp 3 already requires an ACCEPTED validation made with the current `workload.py`.
- **Recording the firmware clock (open item, not part of this validation).** The firmware releases
  throttling within 0.15 s once load stops. A3's ARM-clock record therefore has to be taken **during**
  load in Experiments 2 and 3, without starting a process per sample. How is decided in Amendment A4,
  before any Experiment 2 data. `DESIGN_exp2_exp3_4core.md` 3c ("read between jobs") is superseded on
  this point.
- **P=1:** `workload.py` changes (next section), so the P=1 acceptance in `wv-20261015-01` no longer
  matches the current `workload.py`. It stays on record, but Experiment 3 could only run at P=1 after
  revalidation.

## 5. Implementation (to be written after this addendum is approved)

- `workload.py`: `JobSpec` gains `gemms_per_unit` (default 1, so P=1 and `P4-F1` jobs are unchanged).
  The worker runs that many GEMMs per timed unit.
- `validate_workload.py`: `--config 4b` runs V0b′, V0c′ and P4b only, under the telemetry scheme above.
  The report key is `"4b"`. The existing PREREG constants are unchanged, with P4b's own constants added.
- `exp3_ordering.resolve_workload`: accepts configuration `"4b"`.
- Tests for each change; the launcher gets new hash pins; a separate approval is needed before any
  run.

## Amendment 1 (2026-10-17, after run `wv-p4b-20261017-01`, before any P4b decision data)

**What happened.** Run `wv-p4b-20261017-01` (launcher `e8b67f9`, code `7aafece`) ended **INCOMPLETE**.
P4b was never tested; this is not a REJECTED result.
- V0b′ was invalid three times on the pre-registered 65 °C block guard: peaks of 66.2, 65.2 and 66.2 °C,
  each block starting at 50.1 °C. That used the rerun limit, so V0 was INCOMPLETE and P4b was not run.
- No calibration or acceptance block ran.
- The supervision worked: no signal was sent and cleanup was verified. The evidence is in
  `exp1-rerun-prep/a3/diag/p4b_impl/` (run tar sha256 `350e8e2a…`).

**Cause: a sizing error in section 2 of this addendum.** V0b′ kept the original V0b counts, 10 jobs of 50
units. A P4b unit is 4 GEMMs, so each V0b′ job was about 750 ms of full four-core load instead of the
original 190 ms. Ten such jobs back to back heat the die by about 16 °C.

**Change (the only one).** V0b′ becomes **10 P=4 UNIT jobs of 12 units each**, that is 48 GEMMs per shard
per job, about the original V0b's 50. The V0b load profile then matches the original validation.

Unchanged:
- V0c′, the derivation of u_h, u_c and S0, calibration, acceptance and the rerun limits;
- criteria A1–A9 (P = 4 column), the 65 °C guard and the external 67 °C watchdog;
- the telemetry scheme.

The next run is a new run id. `wv-p4b-20261017-01` stays on record as INCOMPLETE.
