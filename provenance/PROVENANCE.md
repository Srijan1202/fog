# Experiment 1 provenance

Exact code that produced each archived Experiment 1 dataset. These copies are
historical records: do not edit or import them. The current code lives in the
repository root. Nothing here is committed yet.

| Dataset (on the Pi, `~/tdas-pilot/results/`) | Run (2026-10-14) | `exp1_thermal.py` used | sha256 |
|---|---|---|---|
| `exp1_1core/` | 00:12–00:37 | `exp1_1core/exp1_thermal.py` = git `HEAD` 3e222e4 ("initial commit") | `63a4e223742fd806670fe13a3c89a4e33f10a2b63f938955eef1a58ada56b02d` |
| `exp1_4core/` | 01:20–01:24 | `exp1_4core/exp1_thermal.py` = HEAD + `--workers`/`--max-temp` (uncommitted) | `9fd02fc457b31224c41e674111e1f54144efec7aedae734d3b8bc46290863e47` |

Both runs used the unmodified git `HEAD` versions of the imported modules:

| Module | sha256 |
|---|---|
| `sensors.py` | `a3ce01013d3313e7b98c66f05fee908e0b444ff19bf5d15e6a7f203138fec50f` |
| `runner.py` | `31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285` |
| `workload.py` | `90889063ef78ff6ee3debe7ff83ecea858247c732aeea0f574ab05a62a84a730` |

## Evidence
- **exp1_1core:** the HEAD file was copied to the Pi at 00:04:52 and stayed in place until 00:56. The run ended at 00:37. The run's `exp1.meta.json` has exactly the original key set, with no `workers` or `abort_reason`, which matches HEAD.
- **exp1_4core:** `results/exp1_4core/NOTES.txt` records `exp1_thermal.py sha256: 9fd02fc4…`, hashed at launch. The metadata has `workers` and `abort_reason` but no `p4_status`; `p4_status` was added later.
- On the Pi, these two versions are now in `~/tdas-pilot/provenance/` with the same hashes. They were previously named `exp1_thermal.py.orig` and `exp1_thermal.py.run-4core-9fd02fc4`.

## Runtime environment (both runs)
Raspberry Pi 4 Model B Rev 1.4, bare board, governor `performance` ×4.
Kernel 6.18.50+rpt-rpi-v8; firmware a86983925695 (Sep 14 2026); Python 3.13.5;
python3-numpy 1:2.2.4+ds-1 linked to Debian reference BLAS (libblas3 3.12.1-6,
single-threaded); no firmware temperature overrides.
