# Instrument check run clockprobe-20261016-01 (Raspberry Pi 4, 2026-10-16)

Record of the instrument check pre-specified in `INSTRUMENT_CHECK_clock_probe.md` (commit `0c996dd`).
**Not an Experiment 1 result and not usable for G1**; `results/clockprobe-20261016-01/LABEL.txt` on the
Pi says so. Generated from the run's own files; the raw data stays on the Pi
(`~/tdas-pilot/results/clockprobe-20261016-01/`) and in the evidence folder listed at the end.

## Result

**Verdict: BLIND_SPOT.** While the soft temperature limit was active, the firmware ARM clock dropped below
98 % of its maximum, and Linux `scaling_cur_freq` never dropped. By the fixed rule, `scaling_cur_freq`
cannot detect the throttle knee on this Pi.

Output of `tools/clock_probe_analyze.py clock.csv` (unchanged since `0c996dd`), verbatim:

```
{
 "rows": 827,
 "valid": 827,
 "missing": 0,
 "arm_max_Hz": 1800457088,
 "arm_min_Hz": 1629439488,
 "sysfs_max_kHz": 1800000,
 "sysfs_min_kHz": 1800000,
 "active_samples": 128,
 "active_with_arm_drop": 33,
 "sysfs_drop_samples": 0,
 "temp_max_mC": 83263,
 "first_active_temp_mC": 82776,
 "arm_min_while_active_Hz": 1629439488,
 "verdict": "BLIND_SPOT",
 "reason": "ARM clock below 98% of max in 33/128 active samples while scaling_cur_freq never dropped"
}
```

Descriptive only (not part of the rule): firmware ARM clock in the 128 active samples, rounded:
1800 MHz ×12, 1775 MHz ×83, 1726 MHz ×23, 1677 MHz ×9, 1629 MHz ×1. The 1775 MHz step (−1.4 %) is above the
98 % threshold and is not counted as a drop. 36 samples in total were below the threshold, 33 of them active,
between 410 s and 747 s after the sampler started.

## Pre-specified consequence

A preregistration amendment defining the knee from the firmware ARM clock (and recording that clock in
Experiment 1) must be written and committed before any new Experiment 1 run. The Experiment 1 run
`exp1-4core-20261016-01` is **not** reclassified; its recorded script verdict (VALID_NO_KNEE) stands as recorded.

## How it ran

- Code on the Pi: the Experiment 1 files of commit `7968af5` (hashes verified by the launcher, 11 files) plus
  `tools/clock_probe_sampler.sh` of `0c996dd` (sha256 `9ce848c7…50b4`, installed with a hash check).
- Launcher: `tools/launch_exp1.sh` with SITE CONFIG changed only in `RUN=clockprobe-20261016-01`,
  `B0=c4e1c05c-55fd-4c79-8849-80e0b07bc08f` and `EXP1_ARGS="--workers 4 --minutes 12 --cool-minutes 2"`
  (copy sha256 `f24c362c…93cf`). Status observer: `tools/status_exp1.sh` with only `RUN` and `B0` changed
  (copy sha256 `c1f99e49…0243`).
- Idle screen 04:20:32–04:35:32 IST: PASS at the 15-min checkpoint (|diff| 0.341 °C, slope −0.0455 °C/min).
- Monitoring established 04:35:34 IST (exp1 pid 2521, watchdog pid 2549, 85.0 °C stop, cap 2700 s).
- Sampler started 04:35:35 IST, detached (`clock_probe_sampler.sh … clock.csv 900 1`); 827 rows in 900 s
  (about 1.09 s per row, as each row also runs two `vcgencmd` calls). It covered the whole heating and cooling phase.
- Heating 720.9 s with 4 workers (3.996 cores busy), cooling ended at its 2-min limit; exp1 exited 04:50:07.

### Deviations from the pre-specified method

1. First launch attempt (03:01:41 IST) refused by the launcher precheck before anything started: throttle flags
   `0x80000` (bit 19, "soft limit has occurred", left over from the Experiment 1 run in the same boot; the
   launcher requires `0x0`). The user rebooted the Pi and set the governor to `performance` again; `B0` in the
   launcher and status copies was updated to the new boot id. No other change.
2. Sampling interval was about 1.09 s instead of 1 s (see above). The rule counts samples, not seconds; the
   missing-row limit was not touched (0 unreadable rows).
3. exp1's own idle window printed `stable=False` (46.251–48.686 °C over 61 readings, warning only). It affects
   only exp1's cooling baseline, which this check does not use.

## exp1's own output (recorded, not used)

exp1 reported peak 83.75 °C, tau_heat 118.4 s (R² 0.979), tau_cool 37.7 s (R² 0.964), throttle knee NOT OBSERVED,
`exp1_outcome` VALID_NO_KNEE, `p4_status` P4_FAIL. Its telemetry (3954 samples) shows `freq_mhz` 1800 in every
sample and throttle bit 3 set in 758. These are outputs of the instrument under test and are not an
Experiment 1 result.

## Safety during the run

- Watchdog readings (every 5 s): 167, max 83.3 °C (stop at 85.0 °C not reached). Flags seen: `0x0` ×72,
  `0x80000` ×63, `0x80008` ×32; no under-voltage bits in the watchdog or exp1 telemetry.
- Watchdog exit: "monitor exit (validator finished)". No signals sent; `unstopped_workers` empty; status
  observer: "run ended, cleanup verified".

## Evidence

Folder `D:\dev\tdas-evidence\exp1-rerun-prep\` (hashes in its `SHA256SUMS`):

| File | sha256 |
|---|---|
| `run_clockprobe-20261016-01.tar` (run dir + stdout + monitor.out) | `4188264fafd14b0c0633a132c1bedd8b7b503c762d4ad0a7643767d26f90a099` |
| `clock.csv` | `63ec4e1ceb25f00fb96c1199edfbbebb47c48166c0ca59b8d3860c84da9e0894` |
| `exp1.meta.json` | `4cdff248383b02d03ab9cd21b8f74591f507b5ddbc002ceaaff4eb53943d6830` |
| `exp1.telemetry.jsonl` | `aca5dde85d540ce1745518cf82ad6ace08f1624a87b3d5eaba94e26f8ce134aa` |
| `monitor.log` | `a258712fe2b9b359162d468d47ba61fc2ed08904483fae306d95eca7caa0144a` |
| `LABEL.txt` | `4a8773c46a4050cdaf38264b8b3f619ff7e2b07a913e6b3aba9627863bc7c27a` |
| `clockprobe_verdict.json` (analyzer output) | `01c838fcb2122716901c7e5eec1027ae9c5d8affcd262b7b49161032609b6a7a` |

Logs alongside: `clockprobe_launch_console.attempt1_refused.log`, `clockprobe_launch_console.log`,
`clockprobe_sampler_start.log`, `clockprobe_status_1.log`, `pi_probe_install_console.log`,
`pi_postreboot_check.log`, and the launcher/status copies `launch_clockprobe.sh`, `status_clockprobe.sh`.
