# Workload validation code, launch plan rev3 (run wv-20261015-01)

State: the code is committed in `a7641f0` (`a7641f0dcfa3ff06ee9fc4fdcb0a3b9aa3c7348f`) on branch `launch-plan-rev3`, pushed to
`origin/launch-plan-rev3`; `main` is still at `c3bd4a9`. Installed on the Pi on 2026-10-15 (all hashes below
verified there) and used for the workload validation run `wv-20261015-01` on 2026-10-16: see
`WORKLOAD_VALIDATION_RUN_wv-20261015-01.md`. The preregistration addendum is a DRAFT for review (not
formally approved).
Deploying needs separate approval and a sha256 check of every copied file against the first column;
`tools/launch_validation.sh` (EXPECTED_HASHES) refuses any other content.

All values below are sha256 of the Git blobs at each commit (`git show <commit>:<path> | sha256sum`);
files are LF-only (`.gitattributes`), so a checkout has the same bytes.

| File (Pi path relative to ~/tdas-pilot) | commit a7641f0 (current) | commit 7fc3877 | commit c3bd4a9 (original) |
|---|---|---|---|
| `validate_workload.py` | `0b5ce770e3281f3f2ea98808d6a3084ae1a68f9d450155cad5c3c8c28a9f36ca` | `9b6172ea58a3a2badfabc9a0164195646e34c4f25d3d29b8a0c86bc71330c8eb` | `85468bf08bbb114708c97be502e49d7bbaf8bccaca0b4f3516d02e4f114b292c` |
| `workload.py` | `31b6d8f6955798a4395baeaa8733e8a13b16e600f12cfcd071e00354641ad152` | `31b6d8f6955798a4395baeaa8733e8a13b16e600f12cfcd071e00354641ad152` | `bd92ce896dceda8a4d9ce02d0f746196c9c02d3925d33268280617485abbc9aa` |
| `monitor_guard.py` | `acc4d28a6f422fb22c307da3574e26efb3129610e73616bb49f7b719f9f454e3` | `acc4d28a6f422fb22c307da3574e26efb3129610e73616bb49f7b719f9f454e3` | `(absent)` |
| `sensors.py` | `3cdc62dfc784b18733e248c883a0eab763d903389f0e652f4e3478adf408a019` | `3cdc62dfc784b18733e248c883a0eab763d903389f0e652f4e3478adf408a019` | `a3ce01013d3313e7b98c66f05fee908e0b444ff19bf5d15e6a7f203138fec50f` |
| `runner.py` | `31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285` | `31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285` | `31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285` |
| `PREREG_workload_validation.md` | `05ab24e5ea4c8bad9e356990328a9b1b4529c1f40d95b08f73471b0d28ece864` | `05ab24e5ea4c8bad9e356990328a9b1b4529c1f40d95b08f73471b0d28ece864` | `05ab24e5ea4c8bad9e356990328a9b1b4529c1f40d95b08f73471b0d28ece864` |
| `tools/validation_monitor.sh` | `15adfeeb21b6fa44a857e5172066a1adeba92ad69be8376e8d1c7a283ebe4eba` | `15adfeeb21b6fa44a857e5172066a1adeba92ad69be8376e8d1c7a283ebe4eba` | `5f437c5bbefa267b7a63bc6d5326ee279d1a9daa93e236ead7fb7f969cc80f27` |

Scripts delivered over SSH stdin (`bash -s`), never run from the Pi's copy. Before use only their
`# BEGIN SITE CONFIG` blocks are edited (RUN, B0, EXPECTED_HASHES); record the edited scripts' hashes with the run.

| Script | commit a7641f0 (current) | commit 7fc3877 | commit c3bd4a9 |
|---|---|---|---|
| `tools/launch_validation.sh` | `d85225f6868a05eb9cfbdbb804c7fe48c537789f01ca966b1ebfdbacfda2c9f6` | `c9994ec7e063c74d891ea97da34ba3f7b2e116f87c9b836f59fa410bd34d573b` | `(absent)` |
| `tools/status_validation.sh` | `a71f05999fc9011ff41dfca2b73e6d35d0a1583c8322c03d8e14c06540249718` | `a71f05999fc9011ff41dfca2b73e6d35d0a1583c8322c03d8e14c06540249718` | `(absent)` |
| `tools/pi_smoke_test.sh` | `f402e23910968db3b95803c3c34928e4876b57851a5ee7535c64c91fbdb5c9d1` | `(absent)` | `(absent)` |
| `tools/idle_observation.sh` | `736257e3e437c767e5aa0c09f51b6cb58baad4a6e9f67aa392be5ac53cb40c27` | `(absent)` | `(absent)` |

`tools/pi_smoke_test.sh` is the dummy-only smoke test (it pins the same file hashes as the launcher);
`tools/idle_observation.sh` is the read-only idle observation (the launcher's Phase 1 rule, verbatim).

Evidence (kept outside the repository, not modified by later work):
- rev3 review: `D:\dev\tdas-evidence\launch-plan-wv-20261015-01-rev3\` (`REVIEW_PACKAGE_rev3.md`, sha256
  `4ec8b21caeb2db351a06f57916e53113e7802f178e21d3966788bb0c94a8f97d`, inventory 63/63).
- integration (mandatory barrier, tests against these repository files, baseline from `git archive c3bd4a9`):
  `D:\dev\tdas-evidence\launch-plan-wv-20261015-01-rev3-integration\` (`logs/<run>/summary.txt`; final runs
  `run3_final` and `run6_final`). Those tests ran locally (WSL) on the same bytes as this commit; nothing was run on the Pi.

`PREREG_workload_validation.md` and Experiment 1 provenance (`PROVENANCE.md`, `exp1_*`) are unchanged.
