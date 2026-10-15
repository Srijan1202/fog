# TDAS idle observation (READ-ONLY), preflight for run wv-20261015-01.
# Delivered over SSH through stdin, like the launcher:  ssh <pi> 'bash -s' < tools/idle_observation.sh
# Observes the idle Pi with EXACTLY the launcher's Phase 1 rule and hard stops: the
# THERMAL_EVAL block and state_problem() below are byte-for-byte copies of the ones in
# tools/launch_validation.sh (the test suite checks this). Rule source: the reviewer's
# 2026-10-15 decision, described in PREREG_workload_validation_ADDENDUM_rev3.md section 2
# (DRAFT); it is not part of PREREG_workload_validation.md.
#   sample every SAMPLE_S (30 s); checkpoints at samples CHECKPOINTS (30 40 50 60 = 15, 20,
#   25, 30 min); at a checkpoint the last 21 samples (10 min) PASS if |mean(12-21) -
#   mean(2-11)| <= 0.5 C and |least-squares slope| <= 0.05 C/min. The first passing
#   checkpoint ends the observation (PASS). Hard stops at every sample: hostname, boot id,
#   governor (policy and every core), temperature unreadable or >= 50 C, any throttle /
#   under-voltage bit, a conflicting process.
# It starts nothing, sends no signal, changes no setting. Its only write is a NEW record file
# <PROJECT>/<RECORD_DIR>/<name>.log (plus <name>.log.sha256); an existing record is never
# overwritten (noclobber) - a failed observation stays on disk.
# PASS here is idle screening only; it is NOT evidence of thermal safety under load.
# Exit status: 0 PASS; 1 NOT STABLE at every checkpoint; 2 NOT EVALUABLE (fail closed);
# 3 hard stop; 4 interrupted (signal / SSH loss); 5 setup error (nothing observed).
{
# BEGIN SITE CONFIG
B0=440df598-3e7c-4f15-9ad4-e383804e6bc6
HOST_EXPECTED=pi
PROJECT="$HOME/tdas-pilot"
RECORD_DIR=observations
RECORD_NAME=
CPUFREQ=/sys/devices/system/cpu/cpufreq/policy0
CPU_GOV_FILES="/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/cpu1/cpufreq/scaling_governor /sys/devices/system/cpu/cpu2/cpufreq/scaling_governor /sys/devices/system/cpu/cpu3/cpufreq/scaling_governor"
CPUS_EXPECTED="0 1 2 3"
THERMAL=/sys/class/thermal/thermal_zone0/temp
BOOT_ID_FILE=/proc/sys/kernel/random/boot_id
SAMPLE_S=30
CHECKPOINTS="30 40 50 60"
# END SITE CONFIG
PAT='validate_workloa[d]|validation_monito[r]|tdas_smok[e]|exp[123]_|prob[e]\.py|analyz[e]\.py|[w]orkload\.py|multiprocessin[g]'
REC=""
say() {   # record first, then the terminal; a broken terminal never stops the record
  [ -n "$REC" ] && printf '%s\n' "$*" >> "$REC"
  printf '%s\n' "$*" 2>/dev/null
}
finish() {   # $1 exit code, $2 result line
  trap '' INT TERM HUP
  say "== RESULT: $2"
  say "== observation end $(date -u '+%F %T UTC'); samples recorded: ${#T[@]}; exit $1"
  say "== IDLE SCREENING ONLY - not evidence of thermal safety under load"
  if [ -n "$REC" ]; then
    ( cd "$(dirname "$REC")" && sha256sum "$(basename "$REC")" > "$(basename "$REC").sha256" ) 2>/dev/null
    printf '%s\n' "record: $REC sha256 $(cut -d' ' -f1 < "$REC.sha256" 2>/dev/null)" 2>/dev/null
  fi
  exit "$1"
}
trap '' PIPE
T=(); TS=()
case "$-" in *i*|*m*) printf '%s\n' "ABORT: interactive shell or job control"; exit 5;; esac
cd "$PROJECT" 2>/dev/null || { printf '%s\n' "ABORT: cannot cd to $PROJECT"; exit 5; }
for tool in awk vcgencmd pgrep hostname date sha256sum; do
  command -v "$tool" > /dev/null || { printf '%s\n' "ABORT: required tool missing: $tool"; exit 5; }
done
case "$SAMPLE_S" in ''|*[!0-9]*|0) printf '%s\n' "ABORT: bad SAMPLE_S"; exit 5;; esac
mkdir -p "$RECORD_DIR" 2>/dev/null || { printf '%s\n' "ABORT: cannot create $PROJECT/$RECORD_DIR"; exit 5; }
name=${RECORD_NAME:-idle-$(date -u +%Y%m%dT%H%M%SZ)-$(hostname)-$$}
set -o noclobber
: > "$RECORD_DIR/$name.log" 2>/dev/null || { printf '%s\n' "ABORT: record $PROJECT/$RECORD_DIR/$name.log exists or cannot be created (never overwritten)"; exit 5; }
set +o noclobber
REC="$PROJECT/$RECORD_DIR/$name.log"
trap 'finish 4 "INTERRUPTED (SIGINT) at sample ${i:-?} - observation incomplete, no verdict"' INT
trap 'finish 4 "INTERRUPTED (SIGTERM) at sample ${i:-?} - observation incomplete, no verdict"' TERM
trap 'finish 4 "INTERRUPTED (SIGHUP, e.g. SSH lost) at sample ${i:-?} - observation incomplete, no verdict"' HUP
say "== IDLE OBSERVATION (read-only) start $(date -u '+%F %T UTC') record $REC"
say "host $(hostname) boot_id $(cat "$BOOT_ID_FILE" 2>&1) expected_B0 $B0 kernel $(uname -r) uptime_s $(cut -d' ' -f1 /proc/uptime)"
say "rule: sample every ${SAMPLE_S}s, checkpoints at samples [$CHECKPOINTS]; PASS = |diff of 5-min means| <= 0.5 C and |10-min LS slope| <= 0.05 C/min"
say "script sha256 n/a (delivered on stdin; record its sha256 locally)"

state_problem() {   # prints a reason if the host is not in the required state; nothing if OK
  [ "$(hostname)" = "$HOST_EXPECTED" ] || { echo "hostname $(hostname)"; return; }
  [ "$(cat "$BOOT_ID_FILE" 2>&1)" = "$B0" ] || { echo "boot id changed (reboot?)"; return; }
  local g f t th out rc
  g=$(cat "$CPUFREQ/scaling_governor" 2>&1); [ "$g" = performance ] || { echo "governor $g"; return; }
  set -- $(cat "$CPUFREQ/related_cpus" 2>/dev/null); [ "$*" = "$CPUS_EXPECTED" ] || { echo "policy cpus [$*]"; return; }
  for f in $CPU_GOV_FILES; do g=$(cat "$f" 2>&1); [ "$g" = performance ] || { echo "$f: $g"; return; }; done
  t=$(cat "$THERMAL" 2>&1)
  case "$t" in ''|*[!0-9]*) echo "temperature unreadable ($t)"; return;; esac
  [ "$t" -lt 50000 ] || { echo "temperature $t mC >= 50 C"; return; }
  th=$(vcgencmd get_throttled 2>&1)
  case "${th#throttled=}" in 0x[0-9a-fA-F]*) [ $(( ${th#throttled=} )) -eq 0 ] || { echo "throttle/under-voltage bits ($th)"; return; };; *) echo "throttle flags unreadable ($th)"; return;; esac
  out=$(pgrep -A -af "$PAT"); rc=$?
  [ "$rc" -eq 1 ] || { echo "conflict check pgrep exit $rc ${out}"; return; }
}

# BEGIN THERMAL_EVAL
# stdin: the last 21 samples as "<epoch seconds> <temp mC>", oldest first.
# $1 = nominal sample interval S (s), $2 = label. Definitions (fixed before data):
#   window   = these 21 samples, nominally t-10 min .. t;
#   A        = samples 2-11 (nominally t-9.5 .. t-5.0 min), B = samples 12-21 (t-4.5 .. t);
#   diff     = mean(B) - mean(A), from integer mC sums: |diff| <= 0.5 C  <=>  |sum(B) - sum(A)| <= 5000;
#   slope    = least-squares slope over all 21 samples, x = actual timestamps in minutes;
#   PASS     = |diff| <= 0.5 C and |slope| <= 0.05 C/min (inclusive);
#   NOT EVALUABLE (exit 2) = not exactly 21 samples, a non-integer temperature, or any
#     gap between consecutive timestamps outside [5S/6, 7S/6] (missed, delayed or duplicate).
# Exit 0 PASS, 1 NOT STABLE, 2 NOT EVALUABLE. The 50 C stop is checked separately per sample.
thermal_eval() {
  awk -v S="$1" -v lbl="$2" '
    { n++; t[n] = $1 + 0; y[n] = $2; if ($2 !~ /^[0-9]+$/) bad = 1 }
    END {
      if (n != 21) { printf "== CHECKPOINT %s: NOT EVALUABLE (need 21 samples, have %d)\n", lbl, n; exit 2 }
      if (bad) { printf "== CHECKPOINT %s: NOT EVALUABLE (non-integer temperature)\n", lbl; exit 2 }
      lo = S * 5 / 6; hi = S * 7 / 6
      for (i = 2; i <= n; i++) { g = t[i] - t[i-1]
        if (g < lo || g > hi) { printf "== CHECKPOINT %s: NOT EVALUABLE (gap %.3f s between samples %d and %d, allowed [%.1f, %.1f] s)\n", lbl, g, i-1, i, lo, hi; exit 2 } }
      for (i = 2; i <= 11; i++) sa += y[i]
      for (i = 12; i <= 21; i++) sb += y[i]
      d = sb - sa; ad = (d < 0 ? -d : d)
      for (i = 1; i <= n; i++) { x = (t[i] - t[1]) / 60; sx += x; sy += y[i]; sxx += x * x; sxy += x * y[i] }
      den = n * sxx - sx * sx
      if (den <= 0) { printf "== CHECKPOINT %s: NOT EVALUABLE (degenerate timestamps)\n", lbl; exit 2 }
      s = (n * sxy - sx * sy) / den; as = (s < 0 ? -s : s)
      ok = (ad <= 5000 && as <= 50 + 1e-6)
      printf "== CHECKPOINT %s: n=%d window=%.1f s; meanA(2-11)=%.4f C meanB(12-21)=%.4f C |diff|=%.4f C (limit 0.5); slope=%+.5f C/min (limit 0.05) -> %s\n", lbl, n, t[n] - t[1], sa / 10000, sb / 10000, ad / 10000, s / 1000, (ok ? "PASS" : "NOT STABLE")
      exit (ok ? 0 : 1)
    }'
}
# END THERMAL_EVAL

r=$(state_problem); [ -z "$r" ] || finish 3 "HARD STOP before the first sample: $r"
say "n | epoch | time_utc | gap_s | load1 load5 load15 | temp_mC | throttled | gov_policy gov_cpu0-3 | cur_kHz"
t0=$(date +%s.%N); i=0; passed=0; prev=""
while :; do
  d=$(awk -v t0="$t0" -v s="$SAMPLE_S" -v i="$i" -v now="$(date +%s.%N)" 'BEGIN { d = t0 + s * i - now; printf "%.3f", (d > 0 ? d : 0) }')
  sleep "$d"
  ts=$(date +%s.%N); read -r l1 l5 l15 rest < /proc/loadavg
  t=$(cat "$THERMAL" 2>&1); th=$(vcgencmd get_throttled 2>&1)
  govs=""; for f in $CPU_GOV_FILES; do govs="$govs$(cat "$f" 2>&1),"; done
  gap=$([ -n "$prev" ] && awk -v a="$prev" -v b="$ts" 'BEGIN { printf "%.3f", b - a }' || echo "-")
  say "$i | $ts | $(date -u -d "@${ts%.*}" '+%T') | $gap | $l1 $l5 $l15 | $t | $th | $(cat "$CPUFREQ/scaling_governor" 2>&1) ${govs%,} | $(cat "$CPUFREQ/scaling_cur_freq" 2>&1)"
  prev=$ts
  case "$t" in ''|*[!0-9]*) finish 3 "HARD STOP at sample $i: temperature unreadable ($t)";; esac
  TS+=("$ts"); T+=("$t")
  r=$(state_problem); [ -z "$r" ] || finish 3 "HARD STOP at sample $i: $r"
  for c in $CHECKPOINTS; do
    [ "$i" -eq "$c" ] || continue
    n=${#T[@]}; k=$((n - 21)); [ "$k" -ge 0 ] || finish 2 "NOT EVALUABLE: checkpoint at sample $i has only $n samples"
    line=$(for ((j = k; j < n; j++)); do echo "${TS[j]} ${T[j]}"; done | thermal_eval "$SAMPLE_S" "sample $i")
    rc=$?
    say "$line"
    [ "$rc" -eq 0 ] && passed=1
    [ "$rc" -eq 2 ] && finish 2 "NOT EVALUABLE at sample $i (fail closed)"
  done
  [ "$passed" -eq 1 ] && finish 0 "PASS at sample $i ($((i + 1)) samples)"
  last=${CHECKPOINTS##* }
  [ "$i" -ge "$last" ] && finish 1 "NOT STABLE at every checkpoint [$CHECKPOINTS] (fail closed)"
  i=$((i + 1))
done
} < /dev/null 2>&1
# end of script
