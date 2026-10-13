# Experiment 1 provenance

Exact code that produced each archived Experiment 1 dataset. These copies are
historical records: do not edit or import them. The current code lives in the
repository root. Neither dataset can be reproduced with the current
`exp1_thermal.py`; use the copy listed for that dataset.

Git references below use commit hashes, not `HEAD`, because `HEAD` has moved
since the runs.

| Dataset (on the Pi, `~/tdas-pilot/results/`) | Run (2026-10-14) | `exp1_thermal.py` used | sha256 |
|---|---|---|---|
| `exp1_1core/` | 00:12–00:37 | `exp1_1core/exp1_thermal.py` = commit 3e222e4 ("initial commit") | `63a4e223742fd806670fe13a3c89a4e33f10a2b63f938955eef1a58ada56b02d` |
| `exp1_4core/` | 01:20–01:24 | `exp1_4core/exp1_thermal.py` = 3e222e4 + `--workers`/`--max-temp`. Never committed as `exp1_thermal.py`; it exists in git only as this provenance copy (commit 4bd0aed) | `9fd02fc457b31224c41e674111e1f54144efec7aedae734d3b8bc46290863e47` |

Both runs used the versions of the imported modules committed in 3e222e4. These
are still unchanged in 4bd0aed and in the working tree:

| Module | sha256 |
|---|---|
| `sensors.py` | `a3ce01013d3313e7b98c66f05fee908e0b444ff19bf5d15e6a7f203138fec50f` |
| `runner.py` | `31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285` |
| `workload.py` | `90889063ef78ff6ee3debe7ff83ecea858247c732aeea0f574ab05a62a84a730` |

## Evidence
- **exp1_1core:** the 3e222e4 file was copied to the Pi at 00:04:52 and stayed in place until 00:56. The run ended at 00:37. The run's `exp1.meta.json` has exactly the original key set, with no `workers` or `abort_reason`, which matches 3e222e4.
- **exp1_4core:** `results/exp1_4core/NOTES.txt` records `exp1_thermal.py sha256: 9fd02fc4…`, hashed at launch. The metadata has `workers` and `abort_reason` but no `p4_status`; `p4_status` was added later.
- On the Pi, these two versions are now in `~/tdas-pilot/provenance/` with the same hashes. They were previously named `exp1_thermal.py.orig` and `exp1_thermal.py.run-4core-9fd02fc4`.

## Dataset checksums (original, unchanged)
Each dataset folder on the Pi has a `SHA256SUMS` covering its raw files, plus a
`LABEL.txt` that states what the run does and does not show. These files are
recorded here so the record does not depend on the Pi.

**`exp1_1core/`**, labelled "ONE-CORE THERMAL CHARACTERIZATION … NOT a valid test of sustained max load":

| File | sha256 |
|---|---|
| `exp1.jobs.jsonl` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `exp1.log` | `9f545c33bd7c8213e89214795610b532488742e4f9c4a5718f54851b0bf2d525` |
| `exp1.meta.json` | `cdef1268248d8c5a5d40d36a2def83b768ad274bfb085e4323dd36b626d52e25` |
| `exp1.telemetry.jsonl` | `81ee2803ef11eefb7ab024530cb2f7191a9fb46fb7bd661aec596d5ea247e62e` |
| `LABEL.txt` | `42d58faf87a17ee703ffbdc502807f366f39d3b2de00cb36f4bad6308a3106f7` |
| `SHA256SUMS` | `33fc0af9684cf8723686ca7d4e0a35812e22f23b2557afc1a189ec233adeb90f` |

**`exp1_4core/`**, labelled "SAFETY-STOPPED: INCONCLUSIVE FOR KNEE DETECTION":

| File | sha256 |
|---|---|
| `exp1.jobs.jsonl` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `exp1.meta.json` | `6a8f35a6c494e37237785309c183d051670543aa60ede40ecf41b8a8bcb038dc` |
| `exp1.pid` | `ab2960c17501f12f13b4b5a93589e2047b09ea56becd71514e3c1c7f480f5fbc` |
| `exp1.telemetry.jsonl` | `6be1aaa31141ac7ee4953a890fb3092060bbb45c2bbc49ce972061b1601768a4` |
| `monitor.log` | `a1bcd06c8bdd3ac4cc6abd5d740cdc8562f98b0181cc8871742a60b194ed17cf` |
| `run.log` | `336dd571e6db5412ac818a6e9ae05c1481d95b2b2a1f9bf9259f13afa94c0b81` |
| `NOTES.txt` | `af5d248f9ba3b55ec897f4c2d31c8f232610237ac4d4fc570e5fcb820da6c2e2` |
| `LABEL.txt` | `64b303c4b8ae1ffd0c7f87492f0e073900861407db01e58a572a4e93fa6f92a9` |
| `SHA256SUMS` | `22dc81d8c9277bcbe8921421909f090c2ceb496035a210ea7da685f860004af6` |

## Runtime environment (both runs)
Raspberry Pi 4 Model B Rev 1.4, bare board, governor `performance` ×4.
Kernel 6.18.50+rpt-rpi-v8; firmware a86983925695 (Sep 14 2026); Python 3.13.5;
python3-numpy 1:2.2.4+ds-1 linked to Debian reference BLAS (libblas3 3.12.1-6,
single-threaded); no firmware temperature overrides.

## Git history since the runs

### Commit 4bd0aed4 ("changes in exp", 2026-10-14 02:37:55 +0530, Srijan Srivastava)
Parent: 3e222e4. Made after both runs. It captured:

| File | sha256 in 4bd0aed | Content |
|---|---|---|
| `provenance/PROVENANCE.md` | `38c561f73b24dceebf6e2f3b69673940941459160f65d991753e52cf2b6461c3` | First version of this file |
| `provenance/exp1_1core/exp1_thermal.py` | `63a4e223742fd806670fe13a3c89a4e33f10a2b63f938955eef1a58ada56b02d` | Code that produced `exp1_1core` |
| `provenance/exp1_4core/exp1_thermal.py` | `9fd02fc457b31224c41e674111e1f54144efec7aedae734d3b8bc46290863e47` | Code that produced `exp1_4core` |
| `exp1_thermal.py` | `acc32806bc95da3c2be373b94f66ba4bc7b03093ccf282177e85ec20b64b51c4` | Later than both runs: `--workers`, `--max-temp`, safety stop reported as inconclusive, cooling ends only after consecutive readings at a measured idle baseline |
| `test_exp1_thermal.py` | `dc9347b2cee749ceb0674deba228974d0814af3842ba25843ca104710d777ff5` | 16 tests |
| `analyze.py` | `15f816ea20c03b0253450ddc1affdc2cf49345b67a68f045b609276577a8fce8` | G1 is PASS / FAIL / INCONCLUSIVE / NOT TESTED; FURTHER EVIDENCE REQUIRED verdict; Exp 2 knee is supporting only. A knee seen before a safety stop or under partial load still counted as PASS here |
| `test_analyze.py` | `d29069467787f0d68c23537d7e3f927e8e3241eb6ef1adb7064964cdcf22e367` | 32 tests |
| `README.md` | `8b0c6f1ae4e88d1e9e5f6744dfc8ef721e4313c9898caa487e16b453b300e58f` | Last line re-encoded from UTF-16 to UTF-8 (removed 7 NUL bytes) |

### Uncommitted changes on top of 4bd0aed
Recorded on 2026-10-14. Local and Pi copies match.

| File | Status | sha256 now | Change |
|---|---|---|---|
| `analyze.py` | modified | `a2525fed6cb4d15be8b31aa71d77ca12cd929a6f000b86f83e98bdf23a5bd6ae` | G1 PASS also requires a completed, valid Exp 1. A knee recorded in a safety-stopped, aborted, partial-load or otherwise invalid run is INCONCLUSIVE |
| `test_analyze.py` | modified | `7215ba1e182dc7e0c9228d0af7b39ca067cd896941dc8a912c83d6b64b3074be` | 35 tests |
| `exp3_ordering.py` | modified | `7359bcc0564fd117682a123f94479a952a5b43fb6e67e6955e27e773bca95fcb` | Threshold only from a G1-PASS Exp 1 (`--exp1-meta`); Exp 2's knee is never substituted; `--knee` is mock-only. Fixed-work jobs (A2): real runs require `--parallel` and an ACCEPTED workload validation for that P with the same `workload.py` (`--workload-validation`), and refuse otherwise; the old `calibrate`/`verify_match` step is removed. Also refuses a validation report that is not `overall: COMPLETE` or was interrupted |
| `test_exp3_ordering.py` | new | `b993aef367d7114bdd266c8062f00c4ab94b95d60d06afb533d826c50272afbb` | 15 tests |
| `exp1_thermal.py` | modified | `e9bc61e54073b57f03ca2732c1e7610806cf90305d34b23d31b3bbbdf9cd5d02` | End-of-run message decided by `analyze.classify_g1`; `exp1_outcome` saved in metadata; `p4_status` is `PARTIAL_LOAD` even when a knee was seen. Execution unchanged |
| `test_exp1_thermal.py` | modified | `b8a4681f90e303f017ecfe3c47da269850a3ac101e6cf01f425402a2500b2f23` | 25 tests |
| `workload.py` | modified | `bd92ce896dceda8a4d9ce02d0f746196c9c02d3925d33268280617485abbc9aa` | A2 fixed-work jobs appended (`JobSpec`, `FixedWorkPool`, `aggregate_job`, calibration arithmetic). The deadline `Workload` class used by Exp 1/2 is unchanged. Worker CPU is measured with `process_time` (all threads) |
| `validate_workload.py` | new | `85468bf08bbb114708c97be502e49d7bbaf8bccaca0b4f3516d02e4f114b292c` | V0/V1/V2/V4 workload validation (V3 excluded). Pools start before each block's cooldown; results recorded incrementally; interrupted runs write report.json; refuses to start without idle readings. **Not yet run** |
| `PREREG_workload_validation.md` | new | `05ab24e5ea4c8bad9e356990328a9b1b4529c1f40d95b08f73471b0d28ece864` | Pre-registered thresholds, schedule and statistics for the validation |
| `test_workload.py` | new | `e2428a91ea83ea1d5b1cb924582c8b647f932f52e58cea5f2c15ce137f5e4a9d` | 18 tests |
| `test_validate_workload.py` | new | `65ae5221bdb7487c45c615c73e973f24002c17864037f24ed808a0a97932f07c` | 49 tests |
| `README.md` | modified | `3167c3a065c6ce9d59827c0b63f14abb826472bcbfcce5077c6bd9d00b849ad1` | Amendments A1 and A2; workload-design section marked superseded; launch procedure for the workload validation |
| `tools/validation_monitor.sh` | new | `5f437c5bbefa267b7a63bc6d5326ee279d1a9daa93e236ead7fb7f969cc80f27` | External watchdog for the workload validation (67 °C, rise, under-voltage, read failures, run cap). **Not yet run** |
| `tdas-pilot-protocol.md` | modified | `d0191614a469fa3276c99d5a8be23e24bbf5e6bf8291a2012c8ca973975d34b2` | Amendment A1 note under Phase 9; A2 notes on the multiset and energy wording |
| `provenance/PROVENANCE.md` | modified | (this file) | This update |

Neither archived dataset depends on any of these changes. Both were produced by the versions listed at the top of this file.
