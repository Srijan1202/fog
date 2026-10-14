# Workload validation code, launch plan rev3 (run wv-20261015-01)

Committed on branch `launch-plan-rev3`. **Not deployed to the Pi** and **not run**: the workload
validation has not started. Deploying needs separate approval and a sha256 check of every copied
file against this table. `tools/launch_validation.sh` (EXPECTED_HASHES) refuses any other content.

| File | sha256 (this branch) | Commit c3bd4a9 |
|---|---|---|
| `validate_workload.py` | `9b6172ea58a3a2badfabc9a0164195646e34c4f25d3d29b8a0c86bc71330c8eb` | `85468bf08bbb114708c97be502e49d7bbaf8bccaca0b4f3516d02e4f114b292c` |
| `workload.py` | `31b6d8f6955798a4395baeaa8733e8a13b16e600f12cfcd071e00354641ad152` | `bd92ce896dceda8a4d9ce02d0f746196c9c02d3925d33268280617485abbc9aa` |
| `monitor_guard.py` | `acc4d28a6f422fb22c307da3574e26efb3129610e73616bb49f7b719f9f454e3` | `(new file)` |
| `sensors.py` | `3cdc62dfc784b18733e248c883a0eab763d903389f0e652f4e3478adf408a019` | `a3ce01013d3313e7b98c66f05fee908e0b444ff19bf5d15e6a7f203138fec50f` |
| `runner.py` | `31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285` | `31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285` |
| `PREREG_workload_validation.md` | `05ab24e5ea4c8bad9e356990328a9b1b4529c1f40d95b08f73471b0d28ece864` | `05ab24e5ea4c8bad9e356990328a9b1b4529c1f40d95b08f73471b0d28ece864` |
| `tools/validation_monitor.sh` | `15adfeeb21b6fa44a857e5172066a1adeba92ad69be8376e8d1c7a283ebe4eba` | `5f437c5bbefa267b7a63bc6d5326ee279d1a9daa93e236ead7fb7f969cc80f27` |

Launch-time scripts, delivered over SSH stdin (bash -s), never run from the Pi's copy:
`tools/launch_validation.sh` `c9994ec7e063c74d891ea97da34ba3f7b2e116f87c9b836f59fa410bd34d573b`, `tools/status_validation.sh` `a71f05999fc9011ff41dfca2b73e6d35d0a1583c8322c03d8e14c06540249718`.

Review evidence (dummy-process test harness, logs, diffs, review package) is kept outside the
repository in `D:\dev\tdas-evidence\launch-plan-wv-20261015-01-rev3\` (`REVIEW_PACKAGE_rev3.md`).
Changes and their reasons: that folder's `CHANGELOG_rev3.md`; operational rules:
`PREREG_workload_validation_ADDENDUM_rev3.md`. Experiment 1 provenance (`PROVENANCE.md`) is unchanged.
