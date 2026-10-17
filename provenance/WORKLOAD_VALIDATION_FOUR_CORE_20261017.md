# Four-core workload validation, 2026-10-17: P4b → diagnostics → P4c ACCEPTED

This is a record of every four-core run made on 2026-10-17 on the Raspberry Pi 4. The runs are listed in
order, with their outcomes as computed. Each one was generated from its own files. The raw data stays on the Pi
(`~/tdas-pilot/results/`) and in the evidence folder listed at the end. The design is in
`DESIGN_exp2_exp3_4core.md`, and the pre-registrations are in `PREREG_workload_validation_ADDENDUM_P4b.md`
(with Amendment 1) and `PREREG_workload_validation_ADDENDUM_P4c.md`.

## Result

**Configuration P4c is ACCEPTED** (run `wv-p4c-20261017-01`, commit `00a2be7`). It uses four workers; one
timed unit is 4 GEMMs of 128×128. During jobs, telemetry reads sysfs only, and the throttle flags are read
between jobs. The run was supervised by the fork-free watchdog. Every criterion A1–A9 held, judged on the
unchanged P = 4 limits.

| | value | limit |
|---|---|---|
| A6 unit-time CV | **0.83 %** | ≤ 3 % |
| A6 HOT latency CV | **0.35 %** | ≤ 4 % |
| cold-latency gap ĝ [95 % CI] | **−0.05 % [−0.08 %, −0.00 %]**, TARGET MET | within ±2 % (target) |
| HOT / COOL median latency | 196.32 / 196.22 ms | |
| HOT / COOL median realised duty | 0.994 / 0.385 | A2 ≥ 0.90; A3 0.35 × HOT ± 0.05 |
| CPU contrast (2.5th percentile) | 2.58 | A4 ≥ 2.0 |
| median overhead fraction / imbalance | 0.38 % / 0.06 % | A8 ≤ 5 % / A9 ≤ 5 % |

- **Frozen `P4c-F1`:** P = 4, n_b = 128, 4 GEMMs per unit, u_h = 13, u_c = 5, S = 119.55 ms per shard.
- **V0:** t_u(4) = 15.04 ms per four-GEMM unit, o(4) = 0.71 ms.
- **Run:** idle baseline 43.3 °C. All 8 blocks were valid on the first attempt, with 0 reruns and peaks
  of 51.6–57.0 °C.
- **Supervision:** no signals; the watchdog exited with "validator finished", final flags `0x0`; cleanup
  was verified.
- **Recorded hashes:** `report.json` records the hashes of both addenda and of
  `tools/validation_monitor.sh` (`1a98cdc1…`).

**Consequences.**
- **Experiment 3** may use P4c's frozen parameters. It still refuses four-core configurations on real
  hardware until Amendment A4 specifies the telemetry it runs under: the same scheme, plus how the firmware
  ARM clock is read during load.
- **P=1:** `workload.py` changed (`gemms_per_unit`), so the P=1 acceptance of `wv-20261015-01` stays on record
  but no longer matches the current code.
- **Cosmetic flaw (verdict unaffected):** in P4c's report the V4 "not applicable" note says
  "P4b runs V0b', V0c' and P4b only". That text is hard-coded in `new_report_p4b`; it is left as run.

## How it got there

| run | what | outcome |
|---|---|---|
| `diag-p4-20261017-01` | P=4 telemetry diagnostic | refused before load (sticky flag `0x80000` from the Exp 1 run); Pi rebooted |
| `diag-p4-20261017-02` | same | stopped by its own 70 °C limit in block 2 (four cores heat the die ~15 °C in 10 s); cooldown added |
| `diag-p4-20261017-03` | T0 / T1 / T2 telemetry, interleaved | involuntary context switches per job 3.5 / 8 / **23**; unit CV 3.13 / 3.15 / **4.72 %**; regrouping 4 GEMMs per unit gave about 1.8 % |
| `wv-p4b-20261017-01` | P4b validation (`7aafece`) | first launch refused at idle ≥ 50 °C; then **INCOMPLETE**: V0b′ invalid ×3 on the 65 °C guard, a sizing error in the addendum (50 four-GEMM units per job) → Amendment 1 (`5746b31`, 12 units) |
| `wv-p4b-20261017-02` | P4b validation (Amendment 1) | **REJECTED on A6**: unit CV 4.63 %, HOT latency CV 4.43 %. 38 of 40 HOT jobs had a CV of about 0.1 %; 2 hit one-off 15–50 ms stalls (≈ 67 context switches vs a median of 14), each right after a COOL job |
| `diag-stalls-20261017-01` | P4b jobs, no external watchdog, per-thread / IRQ accounting | 1 stall in 240 HOT jobs; HOT latency CV 1.47 % (with between-job flag read) / 0.97 % (without); no thread explained it |
| `diag-stalls-20261017-02` | W: poller with the watchdog's 5 s commands; N: none | **W: 8 stalls in 120, all 8 on poll-overlapped jobs (0 of 112 otherwise), CV 4.54 %; N: 0 in 120, CV 0.99 %** |
| — | fork-free watchdog (`2db3d44`) | loop starts no process: 0 vs 75 processes created in 20 s (dummy harness W14) |
| `diag-stalls-20261017-03` | W: the fork-free loop body; N: none | excluding warm-up pairs, HOT latency CV 0.84 % vs 0.85 %, unit CV 1.84 % vs 1.87 %; the single W stall was the run's first warm-up job (parent start-up) |
| `wv-p4c-20261017-01` | P4c validation (`00a2be7`) | **ACCEPTED** (above) |

**Cause of P4b's failure.** The external watchdog `tools/validation_monitor.sh` started about 12 short
processes every 5 s: `pgrep`, `cat`, `tr`, `vcgencmd`, `cut`, `date`, `bc`, `sleep`. With all four cores
busy, each burst preempted the validator's workers. The same mechanism probably explains P4-F1's A6 failure
in `wv-20261015-01`, which had 21 context switches per job, against 23 in condition T2 above.

**The fork-free watchdog** (`2db3d44`) keeps the same stop rules, with one exception: the sticky
"under-voltage occurred" bit is now read with `vcgencmd` only at watchdog start and exit, not in the loop.
Otherwise:
- **Stops on:** 67 °C, a rise of more than 15 °C per 5 s, under-voltage now (from the kernel `rpi_volt`
  hwmon alarm), 3 failed reads, and the run cap.
- **Signals:** one SIGTERM, never SIGKILL.
- **Mid-run under-voltage checks:** these remain the validator's per-job live-flag guard and its per-block
  sticky audit, both unchanged.

**Disclosure** (also in the addenda). P4b's unit size and P4c's watchdog were both designed after seeing
the earlier failures and the diagnostics. The acceptance criteria were never changed. Each REJECTED or
INCOMPLETE run stays on record.

## Evidence

Folder `D:\dev\tdas-evidence\exp1-rerun-prep\a3\diag\`. Hashes are in `a3/SHA256SUMS`.

| run | archive | sha256 |
|---|---|---|
| `diag-p4-20261017-02` | `run_diag-p4-20261017-02.tar` | `44131cdeab544218fc9e3cd7c4a2dd511af2b91535772c046eba9776dacf3b91` |
| `diag-p4-20261017-03` | `run_diag-p4-20261017-03.tar` | `ad74492545d551053984f2be53b8730e5ec9034d15161029ebbb1677197d7da4` |
| `wv-p4b-20261017-01` | `p4b_impl/run_wv-p4b-20261017-01.tar` | `350e8e2ad3747a63796297abd2ccace2fbca00be78dc8a9fce0036412179dee6` |
| `wv-p4b-20261017-02` | `p4b_impl/run_wv-p4b-20261017-02.tar` | `72224abdb882eb4d7235d0724bd5b6c3030425ffe1208d2184b492a4ebc82c81` |
| `diag-stalls-20261017-01` | `stalls/run_diag-stalls-20261017-01.tar` | `935927fdd1ac089d731cd8989b7410fa08348ee776a92c778b9f991e4bf38351` |
| `diag-stalls-20261017-02` | `stalls/run_diag-stalls-20261017-02.tar` | `dc06291b38a8f53ece64333a2b330de825ceef59231c478f679c87812ece3dc5` |
| `diag-stalls-20261017-03` | `stalls/run_diag-stalls-20261017-03.tar` | `75e6e90cc2db33633c72e576891a80a4fd867f43dbf5127edddbf6efe82272cb` |
| `wv-p4c-20261017-01` | `p4c/run_wv-p4c-20261017-01.tar` | `3f4723e130684dec0c300716f2b8d5d08db7b3801bea384aee250c6f1683fc6e` |

Launch, status, install and wait logs are alongside each archive. So are the dummy-harness logs for the
fork-free watchdog (`p4c/harness/`).
