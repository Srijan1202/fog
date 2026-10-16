# Instrument check: does `scaling_cur_freq` show the Pi's firmware throttling? (pre-specified)

Status: DRAFT, written 2026-10-16 **before** any probe data exists. Not an Experiment 1 run
and not usable for G1. Running it needs separate approval.

## Why
The Experiment 1 rerun `exp1-4core-20261016-01` (2026-10-16, four cores, full 15 min, not
stopped) found **no knee** by the preregistered method: `find_knee()` looks for a drop in
`freq_mhz`, which `sensors.py` reads from Linux `scaling_cur_freq`. That value was 1800 MHz in
all 7164 telemetry samples. In the same run `vcgencmd get_throttled` reported the soft
temperature limit **active** (bit 3) in 1381 samples, first at 81.3 °C. Either the firmware
lowered the ARM clock and `scaling_cur_freq` did not show it (the instrument cannot see the
knee), or the soft limit was flagged without lowering the clock. That run cannot tell the two
apart: it recorded no firmware clock reading. Its result stays recorded as it is (script
verdict VALID_NO_KNEE); this check reinterprets nothing.

## Method
- Load: `tools/launch_exp1.sh` with only its SITE CONFIG changed: `RUN=clockprobe-20261016-01`,
  `EXP1_ARGS="--workers 4 --minutes 12 --cool-minutes 2"`, current `B0`. Same idle screen,
  barrier, watchdog (85.0 °C stop), identity checks and single-SIGTERM rule. Its
  `exp1.meta.json` is labelled by `results/clockprobe-20261016-01/LABEL.txt` as an
  instrument check, not an Experiment 1 result.
- Measurement: right after `MONITORING ESTABLISHED`, `tools/clock_probe_sampler.sh` (read-only)
  starts detached and writes `results/clockprobe-20261016-01/clock.csv` once per second for
  900 s: epoch, temperature, `scaling_cur_freq`, `vcgencmd measure_clock arm`, throttle flags.
- Analysis: `tools/clock_probe_analyze.py clock.csv`, unchanged after data is collected.

## Decision rule (fixed now)
- `active` = samples with throttle bit 3 set; a value "drops" when it is below 98 % of its
  maximum over all valid samples.
- **INCONCLUSIVE** if fewer than 30 active samples, or more than 5 % of rows unreadable.
- **BLIND_SPOT** if, in at least 20 % of active samples, the firmware ARM clock drops while
  `scaling_cur_freq` shows no drop in any sample.
- **NO_CLOCK_DROP** if the ARM clock never drops in an active sample.
- **MIXED** otherwise (reported; no conclusion drawn).

## What follows
- BLIND_SPOT: `scaling_cur_freq` cannot detect the knee on this Pi. A preregistration
  amendment defining the knee from the firmware ARM clock (and recording it in Experiment 1)
  must be written and committed before any new Experiment 1 run. The 2026-10-16 run is not
  reclassified.
- NO_CLOCK_DROP: the frequency-based result stands: no knee in the clock under this load,
  G1 FAIL by the preregistered rule.
- INCONCLUSIVE / MIXED: report and decide; no verdict.

## Result (added 2026-10-16 after the run; the text above is unchanged)
Run `clockprobe-20261016-01`: **BLIND_SPOT** (ARM clock below 98 % of max in 33 of 128 active samples,
minimum 1629 MHz; `scaling_cur_freq` 1800 MHz in every sample). The amendment above is now required
before any new Experiment 1 run. Record: `provenance/INSTRUMENT_CHECK_RUN_clockprobe-20261016-01.md`.
