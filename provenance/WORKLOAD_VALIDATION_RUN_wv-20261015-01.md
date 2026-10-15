# Workload validation run wv-20261015-01 (Raspberry Pi 4, 2026-10-16)

Record of the first real run of `validate_workload.py`, made with the code of commit `a7641f0`/`961487e`
(identical code; `961487e` changed only documentation). Generated from the run's own files; the raw data
stays on the Pi (`~/tdas-pilot/results/workload_validation/`) and in the evidence folder listed at the end.

## Result

**overall: COMPLETE** (not interrupted; idle baseline 46.251 °C; idle window min/max/n [45.277, 47.712, 60]).

| Configuration | Status | Failed criteria | Key values |
|---|---|---|---|
| V0 | DONE | - | n_b=128; t_u(1)=3.699 ms, t_u(4)=3.759 ms |
| P=1 | **ACCEPTED** | none | gap +0.00007 [-0.00024, +0.00064] TARGET MET; unit CV 0.0076; HOT latency CV 0.0018; HOT/COOL duty 0.997/0.353; reruns 0 |
| P=4 | **REJECTED** | A6 | gap -0.00071 [-0.00353, +0.00439] TARGET MET; unit CV 0.0668; HOT latency CV 0.0308; HOT/COOL duty 0.981/0.357; reruns 0 |
| V4 | DONE | - | descriptive only, never used in a decision |

P=4 fails only A6: the CV of unit time over accepted HOT jobs is above its pre-registered limit of 3 %
(its HOT-latency CV is within 4 %). Per `PREREG_workload_validation.md`, Experiment 3 may run on real
hardware only with an ACCEPTED configuration: P=1 qualifies, P=4 does not. A REJECTED result is final and
is not rerun. The statistics limitation printed in the report (pairs treated as interchangeable) applies.

The validator's own summary (`report.txt`, verbatim):

```
WORKLOAD VALIDATION wv-20261015-01: COMPLETE
V0: DONE  n_b=128  t_u={'1': 3.6991870001656935, '4': 3.7592409998978837}  o={'1': 0.1986050001505646, '4': 0.6406014999811305}
P=1: ACCEPTED  gap +0.0001 [-0.0002, +0.0006] TARGET MET  criteria {'A1': True, 'A2': True, 'A3': True, 'A4': True, 'A5': True, 'A6': True, 'A7': True, 'A8': True}
P=4: REJECTED  gap -0.0007 [-0.0035, +0.0044] TARGET MET  criteria {'A1': True, 'A2': True, 'A3': True, 'A4': True, 'A5': True, 'A6': False, 'A7': True, 'A8': True, 'A9': True}
Statistics limitation: The interval treats the 40 accepted pairs as interchangeable. Dependence within and between blocks (drift, cache or idle state, ambient) is not modelled, so the interval may be too narrow. Per-block gaps are diagnostics only; they do not remove this limitation and cannot change the classification.
V4 (DESCRIPTIVE ONLY. Not used in any acceptance decision. The telemetry-off and telemetry-on blocks run one after the other, so any difference is confounded with block order and time drift.): DONE
```

## Safety during the run

- Runtime 01:02:38-01:08:21 IST (~6 min); watchdog cap 21600 s not approached.
- Temperature: highest validator telemetry sample (5 Hz) 61.35 °C in block P4-A4-e1; watchdog readings (every 5 s): 68, max 59.9 °C.
  Limits: block guard 65 °C, watchdog stop 67 °C. Throttle/under-voltage flags seen by the watchdog: 0x0.
- Blocks: 16 valid, 0 invalid/discarded, 0 reruns. Watchdog exit: "monitor exit (validator finished)".
- Signals: none (status observer `signalled=0`); cleanup verified, no leftover processes.

Per-block summary from the validator's stdout:

```
V0a-e1: valid start 46.3 C, peak 48.199 C, 12 jobs
V0b-e1: valid start 46.7 C, peak 55.504 C, 10 jobs
V0c-e1: valid start 48.2 C, peak 48.199 C, 100 jobs
P1-C1-e1: valid start 48.2 C, peak 50.147 C, 20 jobs
P1-C2-e1: valid start 48.2 C, peak 50.634 C, 20 jobs
P1-A1-e1: valid start 48.2 C, peak 52.095 C, 40 jobs
P1-A2-e1: valid start 47.7 C, peak 51.608 C, 40 jobs
P1-A3-e1: valid start 48.2 C, peak 52.095 C, 30 jobs
P4-C1-e1: valid start 47.2 C, peak 56.965 C, 20 jobs
P4-C2-e1: valid start 48.2 C, peak 56.965 C, 20 jobs
P4-A1-e1: valid start 48.2 C, peak 60.861 C, 30 jobs
P4-A2-e1: valid start 47.7 C, peak 59.4 C, 30 jobs
P4-A3-e1: valid start 48.2 C, peak 60.861 C, 30 jobs
P4-A4-e1: valid start 48.2 C, peak 61.348 C, 30 jobs
V4off-e1: valid start 48.2 C, peak - C, 40 jobs
V4on-e1: valid start 48.2 C, peak 53.069 C, 40 jobs
```

## How the run was made

- Pi: hostname `pi`, boot id `354990cf-dfb4-4bfc-83c4-75f3f09b5937`, Debian 13 (trixie), kernel 6.18.50+rpt-rpi-v8,
  Python 3.13.5, NumPy 2.2.4, backend `raspberrypi`; governor `performance` on all cores (set by hand).
- Code installed on 2026-10-15 from commit `961487e` (backup of the previous files in
  `~/tdas-pilot/provenance/pre_rev3_20261015/`); hashes recorded by the run itself in `prereg.json`:

  - `validate_workload.py` `0b5ce770e3281f3f2ea98808d6a3084ae1a68f9d450155cad5c3c8c28a9f36ca`
  - `workload.py` `31b6d8f6955798a4395baeaa8733e8a13b16e600f12cfcd071e00354641ad152`
  - `PREREG_workload_validation.md` `05ab24e5ea4c8bad9e356990328a9b1b4529c1f40d95b08f73471b0d28ece864`
  - `runner.py` `31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285`
  - `sensors.py` `3cdc62dfc784b18733e248c883a0eab763d903389f0e652f4e3478adf408a019`
  - `monitor_guard.py` `acc4d28a6f422fb22c307da3574e26efb3129610e73616bb49f7b719f9f454e3`

- Dummy-only smoke test (`tools/pi_smoke_test.sh`, unchanged): `SMOKE PASS   (checks: 38 passed, 0 failed)`.
- Read-only idle observation (`tools/idle_observation.sh`, only `B0` edited):
  - `CHECKPOINT sample 30: n=21 window=600.0 s; meanA(2-11)=45.0822 C meanB(12-21)=45.8614 C |diff|=0.7792 C (limit 0.5); slope=+0.19101 C/min (limit 0.05) -> NOT STABLE`
  - `CHECKPOINT sample 40: n=21 window=600.0 s; meanA(2-11)=45.8614 C meanB(12-21)=46.3484 C |diff|=0.4870 C (limit 0.5); slope=+0.07969 C/min (limit 0.05) -> NOT STABLE`
  - `CHECKPOINT sample 50: n=21 window=600.0 s; meanA(2-11)=46.3484 C meanB(12-21)=46.4458 C |diff|=0.0974 C (limit 0.5); slope=-0.01138 C/min (limit 0.05) -> PASS`
  - `RESULT: PASS at sample 50 (51 samples)`
- Launch (`tools/launch_validation.sh`, only `B0` edited; idle screen repeated by the launcher):
  - `CHECKPOINT sample 30: n=21 window=600.0 s; meanA(2-11)=46.0075 C meanB(12-21)=46.2023 C |diff|=0.1948 C (limit 0.5); slope=+0.03542 C/min (limit 0.05) -> PASS`
  - `PHASE 1 PASSED at 2026-10-16 01:02:36 IST after 31 samples (idle screening only; not workload thermal safety)`
  - `MONITORING ESTABLISHED 2026-10-16 01:02:38 IST (validator acknowledged; idle window and load start only now)`
  - `VPID=7088 VST=721932 MPID=7115 MST=721988`
- A first launch attempt failed before reaching the Pi (`pi.local` did not resolve); nothing ran.

Script copies used (only the `B0` line differs from the committed scripts):

- `launch_validation_B0-354990cf.sh` `dc1bdcc19945d643d1836daaf1b6d6081f316de0bf08fdffbbb56425efb80a30`
- `status_validation_B0-354990cf.sh` `8a558dd728ba61034ad1138c6f659112ec6bbfc6b38c6d6f79e0ea90c67fd3a8`
- `idle_observation_B0-354990cf.sh` `e8712a3d7a76a365009002082f176c1d09f6cbe7fd74e8c4e84ef8a25ca80e94`

## Evidence

`D:\dev\tdas-evidence\pi-validation-20261015\` (index: `SHA256SUMS`):

- `run_wv-20261015-01.tar` (whole run directory + stdout + monitor.out) `1bbc74fb1d31005fe2473788d1307c44ed426e5abc6674d580518e18feb56c77`
- `wv-20261015-01/report.json` `5de9d24a7c4001b8e6d81d05797de94f018506b09618b43cc9368e0acfe0ffc8`
- `wv-20261015-01/report.txt` `39b4eb04590a92055eef1c68280409d38c1cbe167777ffd6b80ceb58e841daf7`
- `wv-20261015-01/prereg.json` `01810abbd9854e49402fb284451004e42be46c5c194c6eadf8f2abbc68c682e3`
- `wv-20261015-01/monitor.log` `e684844912b1298a47742cb44cef792c0cf86da700f546c07e56449c55cade20`
- `wv-20261015-01.stdout.log` `507848d290a618f151537aac0703ced396026badd73ac3f283a8046cfe36c3df`
- smoke-test record, idle-observation record, launch/status console logs, preflight log.

`PREREG_workload_validation.md` was not changed; the preregistration addendum remains a DRAFT.
