#!/usr/bin/env bash
# tools/clock_probe_sampler.sh - READ-ONLY instrument check (see INSTRUMENT_CHECK_clock_probe.md)
#
#   bash tools/clock_probe_sampler.sh <out.csv> <duration_s> [interval_s]
#
# Every interval_s (default 1 s) for duration_s seconds, appends one CSV row:
#   epoch, temp_mC (sysfs), scaling_cur_kHz (Linux cpufreq), arm_clock_Hz
#   (firmware: vcgencmd measure_clock arm), throttled (vcgencmd get_throttled)
# It starts nothing, signals nothing and changes no setting. The output file must not
# exist (noclobber): an earlier record is never overwritten. Unreadable values are written
# as NA, never invented. Exit: 0 done, 3 cannot create the output file, 2 bad arguments.
set -u
THERMAL=/sys/class/thermal/thermal_zone0/temp
SCALING=/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq
[ $# -ge 2 ] || { echo "usage: $0 <out.csv> <duration_s> [interval_s]" >&2; exit 2; }
out=$1; dur=$2; iv=${3:-1}
case "$dur$iv" in *[!0-9.]*|'') echo "bad duration/interval" >&2; exit 2;; esac
set -o noclobber
: > "$out" 2>/dev/null || { echo "cannot create $out (exists or not writable; never overwritten)" >&2; exit 3; }
set +o noclobber
num() { case "$1" in ''|*[!0-9]*) echo NA;; *) echo "$1";; esac; }
echo "epoch,temp_mC,scaling_cur_kHz,arm_clock_Hz,throttled" >> "$out"
end=$(awk -v n="$(date +%s.%N)" -v d="$dur" 'BEGIN { printf "%.3f", n + d }')
while awk -v n="$(date +%s.%N)" -v e="$end" 'BEGIN { exit !(n < e) }'; do
  ts=$(date +%s.%N)
  t=$(num "$(cat "$THERMAL" 2>/dev/null)")
  f=$(num "$(cat "$SCALING" 2>/dev/null)")
  c=$(vcgencmd measure_clock arm 2>/dev/null); c=$(num "${c#*=}")
  th=$(vcgencmd get_throttled 2>/dev/null); th=${th#*=}
  case "$th" in 0x[0-9a-fA-F]*) ;; *) th=NA;; esac
  echo "$ts,$t,$f,$c,$th" >> "$out"
  sleep "$iv"
done
