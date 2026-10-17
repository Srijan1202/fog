# TDAS Experiment 1 (four-core thermal / throttle-knee) launch.
# Derived from tools/launch_validation.sh: same phases, identity checks, barrier and
# signal rules; it starts exp1_thermal.py and tools/exp1_monitor.sh (85.0 C stop).
# Delivered over SSH through stdin (bash -s); this file is never written to the Pi.
#   Phase 1  idle observation, revised criterion (15-30 min, read-only)
#   Phase 2  guards (read-only)                    -> failure: exit 3, nothing launched
#   Phase 3  start exp1 (EXP1_ARGS = --workers 4 --minutes 15, fresh --outdir, --require-monitor);
#            it does NO load until <outdir>/MONITOR_OK names a watchdog it verifies itself
#   Phase 4  verify exp1's identity and that it is waiting at its barrier
#   Phase 5  start the watchdog and verify it completely (incl. the 85000 mC limit)
#   Phase 6  only then write MONITOR_OK; wait for exp1 to acknowledge
# On any failure before MONITOR_OK: no token is written, so exp1 cannot start
# load and exits by itself at its barrier (BARRIER_S). Only if it is still
# alive after that, still identity-verified, still its own session leader, still
# without children, still without a token and without any sign in its stdout that it
# passed the barrier, ONE SIGTERM is sent; otherwise no signal: INTERVENTION REQUIRED.
# CAP (MAX_RUN_S of the watchdog) is a maximum runtime: reaching it, or the 85 C
# stop, ends the run as ABORTED, which is INCONCLUSIVE for G1 (a valid run never
# hits them). exp1 has no --max-temp here; the watchdog is the temperature stop.
# Exit status: 0 monitoring established; 3 nothing launched; 4 launched but
# monitoring not established; 5 token written but not acknowledged.
{
# BEGIN SITE CONFIG
RUN=exp1-4core-a3-20261016-01
B0=3075cea7-571e-4a43-97a4-cf06d6f1beb0
HOST_EXPECTED=pi
PROJECT="$HOME/tdas-pilot"
OUT=results
CAP=2700
LIMIT_MC_EXPECTED=85000
EXP1_ARGS="--workers 4 --minutes 15"
GRACE=30
BARRIER_S=120
EXIT_WAIT_S=30
CPUFREQ=/sys/devices/system/cpu/cpufreq/policy0
CPU_GOV_FILES="/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/cpu1/cpufreq/scaling_governor /sys/devices/system/cpu/cpu2/cpufreq/scaling_governor /sys/devices/system/cpu/cpu3/cpufreq/scaling_governor"
CPUS_EXPECTED="0 1 2 3"
THERMAL=/sys/class/thermal/thermal_zone0/temp
BOOT_ID_FILE=/proc/sys/kernel/random/boot_id
SAMPLE_S=30
CHECKPOINTS="30 40 50 60"
LOAD15_MAX=0.10
LAUNCH_PATH=/usr/sbin:/usr/bin:/sbin:/bin
LAUNCH_LANG=en_GB.UTF-8
ARCHIVES="results/exp1_1core results/exp1_4core"
EXPECTED_HASHES='
6f067d2fe09c856794530057c7218e5694ea58388def2869aecb818047a6ca08 exp1_thermal.py
b5cf90630212ec35490df8c55af40559a561e943ccccc037ee9e888b4561c18e analyze.py
31b6d8f6955798a4395baeaa8733e8a13b16e600f12cfcd071e00354641ad152 workload.py
acc4d28a6f422fb22c307da3574e26efb3129610e73616bb49f7b719f9f454e3 monitor_guard.py
31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285 runner.py
d9df2f74772c9bd0ac0cc8204f59fb97088df3744dbc16048e6eb4e7ae52c9bc sensors.py
3a43a123cb8f4504ebc27864675a2ae0c513643443fd57c6cbe507c92bd8b0c4 tools/exp1_monitor.sh
33fc0af9684cf8723686ca7d4e0a35812e22f23b2557afc1a189ec233adeb90f results/exp1_1core/SHA256SUMS
42d58faf87a17ee703ffbdc502807f366f39d3b2de00cb36f4bad6308a3106f7 results/exp1_1core/LABEL.txt
22dc81d8c9277bcbe8921421909f090c2ceb496035a210ea7da685f860004af6 results/exp1_4core/SHA256SUMS
64b303c4b8ae1ffd0c7f87492f0e073900861407db01e58a572a4e93fa6f92a9 results/exp1_4core/LABEL.txt
'
# END SITE CONFIG
echo "== START $(date '+%F %T %Z') stdin=$(readlink /proc/$$/fd/0) shell=[$(tr '\0' ' ' < /proc/$$/cmdline)] flags=$-"
PAT='validate_workloa[d]|validation_monito[r]|tdas_smok[e]|exp[123]_|prob[e]\.py|analyz[e]\.py|[w]orkload\.py|multiprocessin[g]'
die() { echo "ABORT: $*"; echo "== NOTHING LAUNCHED"; exit 3; }
st() { local n=$1 s; s=$(cat "/proc/$2/stat" 2>/dev/null) || return 1; s=${s##*) }; set -- $s; eval "echo \${$n}"; }   # field after "pid (comm)": 1=state 4=session 20=start
case "$-" in *i*|*m*) die "launching shell is interactive or has job control";; esac
cd "$PROJECT" || die "cannot cd to $PROJECT"

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

# ---------------- Phase 1: idle observation ----------------
r=$(state_problem); [ -z "$r" ] || die "precheck: $r"
echo "== PHASE 1 idle observation $(date '+%F %T %Z'): sample every ${SAMPLE_S}s, checkpoints at samples [$CHECKPOINTS]"
echo "n | time | load1 load5 load15 | temp_C | throttled | governor cur_kHz"
TS=(); T=(); t0=$(date +%s.%N); i=0; passed=0
while :; do
  d=$(awk -v t0="$t0" -v s="$SAMPLE_S" -v i="$i" -v now="$(date +%s.%N)" 'BEGIN { d = t0 + s * i - now; printf "%.3f", (d > 0 ? d : 0) }')
  sleep "$d"
  ts=$(date +%s.%N); read -r l1 l5 l15 rest < /proc/loadavg
  t=$(cat "$THERMAL" 2>&1); th=$(vcgencmd get_throttled 2>&1)
  case "$t" in ''|*[!0-9]*) die "observation stopped at sample $i: temperature unreadable ($t)";; esac
  TS+=("$ts"); T+=("$t")
  echo "$i | $(date -d "@${ts%.*}" '+%T') | $l1 $l5 $l15 | $((t/1000)).$(printf '%03d' $((t%1000))) | $th | $(cat "$CPUFREQ/scaling_governor") $(cat "$CPUFREQ/scaling_cur_freq" 2>&1)"
  r=$(state_problem); [ -z "$r" ] || die "observation stopped at sample $i: $r"
  for c in $CHECKPOINTS; do
    [ "$i" -eq "$c" ] || continue
    n=${#T[@]}; k=$((n - 21)); [ "$k" -ge 0 ] || die "checkpoint at sample $i has only $n samples"
    for ((j = k; j < n; j++)); do echo "${TS[j]} ${T[j]}"; done | thermal_eval "$SAMPLE_S" "sample $i"
    rc=${PIPESTATUS[1]}
    [ "$rc" -eq 0 ] && passed=1
    [ "$rc" -eq 2 ] && die "thermal criterion not evaluable at sample $i (fail closed)"
  done
  [ "$passed" -eq 1 ] && break
  last=${CHECKPOINTS##* }
  [ "$i" -ge "$last" ] && die "temperature NOT STABLE at every checkpoint (fail closed)"
  i=$((i + 1))
done
echo "== PHASE 1 PASSED at $(date '+%F %T %Z') after $((i + 1)) samples (idle screening only; not workload thermal safety)"

# ---------------- Phase 2: guards ----------------
echo "== PHASE 2 guards $(date '+%T')"
r=$(state_problem); [ -z "$r" ] || die "guard: $r"
nh=0
while read -r h f; do
  [ -n "$h" ] || continue
  case "$h" in *[!0-9a-f]*|'') die "malformed expected hash for $f";; esac
  [ "${#h}" -eq 64 ] || die "malformed expected hash for $f"
  [ -f "$f" ] || die "missing: $f"
  [ "$(sha256sum < "$f" | cut -d' ' -f1)" = "$h" ] || die "hash mismatch: $f"
  nh=$((nh + 1))
done <<< "$EXPECTED_HASHES"
[ "$nh" -ge 1 ] || die "no hashes checked"
echo "hashes verified: $nh files"
for d in $ARCHIVES; do (cd "$d" && sha256sum -c --quiet SHA256SUMS) || die "archive content check failed: $d"; done
echo "archive contents (sha256sum -c): OK"
PY=$(readlink -f "$(command -v python3)")
case "$PY" in /usr/bin/python3.*) ;; *) die "unexpected python3: $PY";; esac
echo "python3 -> $PY"
for p in "$OUT/$RUN" "$OUT/$RUN.stdout.log" "$OUT/$RUN.monitor.out"; do
  if [ -e "$p" ] || [ -L "$p" ]; then die "exists: $p"; fi
done
if [ -e "$OUT" ] || [ -L "$OUT" ]; then { [ -d "$OUT" ] && [ ! -L "$OUT" ]; } || die "$OUT is not a plain directory"; fi
[ -w "$(dirname "$OUT")" ] || die "$(dirname "$OUT") not writable"
free=$(df --output=avail -k . | tail -1); [ "$free" -gt 1048576 ] || die "only $free kB free"
read -r l1 l5 l15 rest < /proc/loadavg
awk -v a="$l15" -v m="$LOAD15_MAX" 'BEGIN { exit !(a + 0 < m + 0) }' || die "15-min load $l15 >= $LOAD15_MAX"
echo "outputs absent; writable; ${free} kB free; load15 $l15"

# ---------------- Phase 3: start exp1 (barrier: no load yet) ----------------
set -o noclobber
[ -d "$OUT" ] || mkdir "$OUT" || die "cannot create $OUT"          # parent only, never the run directory
env -i HOME="$HOME" PATH="$LAUNCH_PATH" LANG="$LAUNCH_LANG" \
  setsid nohup python3 -u exp1_thermal.py $EXP1_ARGS --outdir "$OUT/$RUN" --require-monitor "$BARRIER_S" \
  > "$OUT/$RUN.stdout.log" 2>&1 < /dev/null &
VPID=$!
LAUNCH_EPOCH=$(date +%s)
echo "== PHASE 3 exp1 started: pid $VPID at $(date '+%F %T %Z'); it waits up to ${BARRIER_S}s for MONITOR_OK"

# ---------------- helpers (identity; no PID is ever trusted alone) ----------------
VCMD="python3 -u exp1_thermal.py $EXP1_ARGS --outdir $OUT/$RUN --require-monitor $BARRIER_S "
VST=""
vok() {   # the exact exp1 we started: start time, not zombie, executable, full argv
  [ -n "$VST" ] && [ "$(st 20 "$VPID")" = "$VST" ] && [ "$(st 1 "$VPID")" != Z ] &&
  case "$(readlink "/proc/$VPID/exe" 2>/dev/null)" in /usr/bin/python3*) true;; *) false;; esac &&
  [ "$(tr '\0' ' ' < "/proc/$VPID/cmdline" 2>/dev/null)" = "$VCMD" ]
}
vstate() {   # RUNNING | EXITED | MISMATCH | UNKNOWN
  if [ -z "$VST" ]; then
    if [ ! -e "/proc/$VPID" ] || [ "$(st 1 "$VPID")" = Z ]; then echo EXITED; else echo UNKNOWN; fi
  elif vok; then echo RUNNING
  elif [ ! -e "/proc/$VPID" ] || [ "$(st 1 "$VPID")" = Z ]; then echo EXITED
  else echo MISMATCH; fi
}
nochild() { pgrep -P "$1" > /dev/null; [ $? -eq 1 ]; }
monitoring_failed() {   # no MONITOR_OK has been (or will be) written
  echo "== MONITORING NOT ESTABLISHED: $1"
  if [ -e "$OUT/$RUN/MONITOR_OK" ]; then
    echo "UNEXPECTED: MONITOR_OK exists - NO SIGNAL; INTERVENTION REQUIRED"; exit 4
  fi
  echo "no MONITOR_OK written: exp1 cannot start load and exits by itself at its ${BARRIER_S}s barrier"
  local limit=$((LAUNCH_EPOCH + BARRIER_S + EXIT_WAIT_S)) s
  while [ "$(date +%s)" -lt "$limit" ]; do s=$(vstate); [ "$s" = RUNNING ] || break; sleep 1; done
  s=$(vstate)
  case "$s" in
    EXITED)
      echo "exp1 exited without load: $(grep -m1 -E '"abort_reason"' "$OUT/$RUN/exp1.meta.json" 2>/dev/null | tr -s ' ')"
      echo "stdout: $(tail -n 2 "$OUT/$RUN.stdout.log" 2>/dev/null | tr '\n' ' ')"; exit 4;;
    RUNNING)
      if [ "$(st 4 "$VPID")" = "$VPID" ] && nochild "$VPID" && [ ! -e "$OUT/$RUN/MONITOR_OK" ] &&
         ! grep -qE '^\[exp1\] (monitoring verified|idle baseline|starting [0-9]+ HOT worker|1a heating)' "$OUT/$RUN.stdout.log" &&
         vok; then
        kill -TERM "$VPID" && echo "== sent exactly ONE SIGTERM to verified exp1 $VPID (no children, no MONITOR_OK, stdout still at the barrier: no load possible)"
        local k; for k in $(seq 1 45); do [ "$(vstate)" = RUNNING ] || break; sleep 1; done
        if [ "$(vstate)" = RUNNING ]; then echo "exp1 still running 45 s after SIGTERM - NO FURTHER SIGNAL; INTERVENTION REQUIRED"
        else echo "exp1 exited after SIGTERM: $(grep -m1 -E '"abort_reason"' "$OUT/$RUN/exp1.meta.json" 2>/dev/null | tr -s ' ')"; fi
      else
        echo "== NO SIGNAL: preconditions not all met (session=$(st 4 "$VPID") children=$(nochild "$VPID" && echo none || echo present) token=$([ -e "$OUT/$RUN/MONITOR_OK" ] && echo present || echo absent) stdout_past_barrier=$(grep -qE '^\[exp1\] (monitoring verified|idle baseline|starting [0-9]+ HOT worker|1a heating)' "$OUT/$RUN.stdout.log" && echo yes || echo no)) - INTERVENTION REQUIRED"
      fi
      exit 4;;
    *) echo "== NO SIGNAL: exp1 identity $s - INTERVENTION REQUIRED"; exit 4;;
  esac
}

# ---------------- Phase 4: verify exp1 and its barrier ----------------
for k in $(seq 1 120); do [ -f "$OUT/$RUN/exp1.start.json" ] && break; sleep 0.25; done
[ -f "$OUT/$RUN/exp1.start.json" ] || monitoring_failed "exp1.start.json not written within 30 s"
VST=$(st 20 "$VPID")
vok || monitoring_failed "exp1 identity not verified (pid $VPID, start '$VST')"
[ "$(st 4 "$VPID")" = "$VPID" ] || monitoring_failed "exp1 is not its own session leader"
nochild "$VPID" || monitoring_failed "exp1 already has children"
for k in $(seq 1 40); do grep -qF "[exp1] run $RUN: waiting up to" "$OUT/$RUN.stdout.log" && break; sleep 0.25; done
grep -qF "[exp1] run $RUN: waiting up to" "$OUT/$RUN.stdout.log" || monitoring_failed "exp1 did not report waiting at its barrier"
echo "== PHASE 4 exp1 verified: pid $VPID start $VST session $VPID exe $(readlink /proc/$VPID/exe); no children; waiting at its barrier"

# ---------------- Phase 5: start and verify the watchdog ----------------
env -i HOME="$HOME" PATH="$LAUNCH_PATH" LANG="$LAUNCH_LANG" MAX_RUN_S="$CAP" GRACE_S="$GRACE" \
  setsid nohup bash tools/exp1_monitor.sh "$VPID" "$OUT/$RUN" \
  > "$OUT/$RUN.monitor.out" 2>&1 < /dev/null &
MPID=$!
echo "== PHASE 5 watchdog started: pid $MPID"
MCMD="bash tools/exp1_monitor.sh $VPID $OUT/$RUN "
MLOG="$OUT/$RUN/monitor.log"
READING='^[0-9-]+ [0-9:]+ +[0-9]+[.,][0-9]   0x[0-9a-fA-F]+$'
for k in $(seq 1 48); do grep -qE "$READING" "$MLOG" 2>/dev/null && break; sleep 0.25; done
MST=$(st 20 "$MPID")
{ [ -n "$MST" ] && [ "$(st 1 "$MPID")" != Z ]; } || monitoring_failed "watchdog not running (output: $(tr '\n' ' ' < "$OUT/$RUN.monitor.out" 2>/dev/null))"
[ "$(readlink "/proc/$MPID/exe" 2>/dev/null)" = /usr/bin/bash ] || monitoring_failed "watchdog executable $(readlink /proc/$MPID/exe 2>/dev/null)"
[ "$(tr '\0' ' ' < "/proc/$MPID/cmdline" 2>/dev/null)" = "$MCMD" ] || monitoring_failed "watchdog command line mismatch"
[ "$(st 4 "$MPID")" = "$MPID" ] || monitoring_failed "watchdog is not its own session leader"
[ "$(st 20 "$MPID")" = "$MST" ] || monitoring_failed "watchdog start time changed"
grep -qF "monitor start: pid=$VPID start=$VST run=$RUN limits:" "$MLOG" 2>/dev/null || monitoring_failed "monitor.log start line missing"
grep -qF "max ${CAP}s;" "$MLOG" || monitoring_failed "monitor.log does not show the ${CAP}s cap"
grep -qF "limits: ${LIMIT_MC_EXPECTED} mC," "$MLOG" || monitoring_failed "monitor.log does not show the ${LIMIT_MC_EXPECTED} mC limit"
grep -qE "$READING" "$MLOG" || monitoring_failed "no temperature reading in monitor.log"
age=$(( $(date +%s) - $(stat -c %Y "$MLOG") )); [ "$age" -le 10 ] || monitoring_failed "monitor.log reading is ${age}s old"
[ ! -s "$OUT/$RUN.monitor.out" ] || monitoring_failed "watchdog wrote errors: $(tr '\n' ' ' < "$OUT/$RUN.monitor.out")"
vok || monitoring_failed "exp1 identity lost before monitoring was established"
nochild "$VPID" || monitoring_failed "exp1 has children before MONITOR_OK"
echo "== PHASE 5 watchdog verified: pid $MPID start $MST session $MPID exe /usr/bin/bash; monitor.log start line, cap ${CAP}s, reading ${age}s old"

# ---------------- Phase 6: release the barrier ----------------
TOK="vpid=$VPID vstart=$VST mpid=$MPID mstart=$MST"
printf '%s\n' "$TOK" > "$OUT/$RUN/.MONITOR_OK.tmp" || monitoring_failed "cannot write token"
mv -n "$OUT/$RUN/.MONITOR_OK.tmp" "$OUT/$RUN/MONITOR_OK"
[ "$(cat "$OUT/$RUN/MONITOR_OK" 2>/dev/null)" = "$TOK" ] || { echo "== MONITOR_OK content unexpected - NO SIGNAL; INTERVENTION REQUIRED"; exit 5; }
echo "== PHASE 6 MONITOR_OK written: $TOK"
for k in $(seq 1 60); do grep -qF "[exp1] monitoring verified:" "$OUT/$RUN.stdout.log" && break; sleep 0.25; done
if grep -qF "[exp1] monitoring verified:" "$OUT/$RUN.stdout.log"; then
  echo "== MONITORING ESTABLISHED $(date '+%F %T %Z') (exp1 acknowledged; idle window and heating start only now)"
  echo "VPID=$VPID VST=$VST MPID=$MPID MST=$MST"
  echo "monitor.log:"; sed 's/^/  /' "$MLOG"
  exit 0
fi
echo "== MONITOR_OK not acknowledged within 15 s ($(vstate)); exp1 stdout: $(tail -n 2 "$OUT/$RUN.stdout.log" | tr '\n' ' ')"
echo "== NO SIGNAL; INTERVENTION REQUIRED (start status.sh to observe)"
exit 5
} < /dev/null 2>&1
# end of script
