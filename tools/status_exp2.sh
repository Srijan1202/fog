# TDAS Experiment 2 status (derived from tools/status_exp1.sh; same rules, Amendment A4).
# Its 60 s observation (vcgencmd, pgrep, cat) is the same observer that ran during the
# accepted P4c validation wv-p4c-20261017-01.
# Delivered over SSH through stdin (bash -s). Re-attachable at any time: the identities
# come from <run>/MONITOR_OK (written by launch.sh only after full verification) and are
# re-verified on every read; no PID is trusted alone. It runs only while its SSH session
# lasts: losing SSH ends THIS observer, not the detached run (the watchdog and the
# exp2's own monitor guard keep running on the Pi). Re-run it to re-attach.
# Read-only, with ONE exception (approved mid-run policy, MIDRUN_SIGTERM=1): if the
# watchdog is confirmed absent on two reads 5 s apart, the boot id is unchanged, the
# exp2's identity is verified, and exp2 is STILL running LOSS_GRACE_S after
# that confirmation (it should stop by itself: monitor_guard.MonitorLost), it sends at
# most ONE SIGTERM to exp2. Never SIGKILL, never to an unverified PID, never a
# second signal; a exp2 still running SURVIVE_S after that SIGTERM is reported as
# INTERVENTION REQUIRED. It writes nothing and restarts nothing.
# Exit status: 0 exp2 exited and cleanup verified; 3 no valid MONITOR_OK;
# 6 INTERVENTION REQUIRED / leftovers / identity, boot or window problem.
{
# BEGIN SITE CONFIG
RUN=exp2-a4-20261018-03
B0=cb8c1378-4b03-4f65-9183-6f3caa6dc943
PROJECT="$HOME/tdas-pilot"
OUT=results
CAP=5400
INTERVAL_S=60
STALE_S=30
LOSS_GRACE_S=60
SURVIVE_S=60
MIDRUN_SIGTERM=1
FINAL_WAIT_S=60
THERMAL=/sys/class/thermal/thermal_zone0/temp
CPUFREQ=/sys/devices/system/cpu/cpufreq/policy0
BOOT_ID_FILE=/proc/sys/kernel/random/boot_id
# END SITE CONFIG
cd "$PROJECT" || { echo "ABORT: cannot cd to $PROJECT"; exit 3; }
R="$OUT/$RUN"; MLOG="$R/monitor.log"
echo "== STATUS START $(date '+%F %T %Z') stdin=$(readlink /proc/$$/fd/0) run=$RUN"
tok=$(cat "$R/MONITOR_OK" 2>/dev/null)
VPID=""; VST=""; MPID=""; MST=""
for kv in $tok; do
  case "$kv" in vpid=*) VPID=${kv#vpid=};; vstart=*) VST=${kv#vstart=};; mpid=*) MPID=${kv#mpid=};; mstart=*) MST=${kv#mstart=};; esac
done
case "$VPID:$VST:$MPID:$MST" in
  *::*|:*|*:|*[!0-9:]*)
    echo "== NO VALID MONITOR_OK: monitoring was never established for $RUN; nothing to verify"
    echo "   (without MONITOR_OK exp2 cannot start load; it exits at its barrier)"
    echo "   stdout: $(tail -n 3 "$OUT/$RUN.stdout.log" 2>/dev/null | tr '\n' ' ')"
    exit 3;;
esac
st() { local n=$1 s; s=$(cat "/proc/$2/stat" 2>/dev/null) || return 1; s=${s##*) }; set -- $s; eval "echo \${$n}"; }   # 1=state 4=session 20=start
VCMD_TAIL=" --outdir $OUT/$RUN --require-monitor "
MCMD="bash tools/exp2_monitor.sh $VPID $OUT/$RUN "
boot_ok() { [ "$(cat "$BOOT_ID_FILE" 2>/dev/null)" = "$B0" ]; }
pstate() {   # $1 pid $2 start $3 kind -> RUNNING | EXITED | MISMATCH | UNREADABLE
  local s cl ex
  if [ ! -e "/proc/$1" ]; then echo EXITED; return; fi
  s=$(st 20 "$1") || { [ -e "/proc/$1" ] && echo UNREADABLE || echo EXITED; return; }
  [ "$(st 1 "$1")" = Z ] && { echo EXITED; return; }
  [ "$s" = "$2" ] || { echo MISMATCH; return; }
  cl=$(tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null); ex=$(readlink "/proc/$1/exe" 2>/dev/null)
  if [ "$3" = exp2 ]; then
    case "$ex" in /usr/bin/python3*) ;; *) echo MISMATCH; return;; esac
    case "$cl" in "python3 -u exp2_curve.py "*"$VCMD_TAIL"[0-9]*) echo RUNNING;; *) echo MISMATCH;; esac
  else
    [ "$ex" = /usr/bin/bash ] && [ "$cl" = "$MCMD" ] && echo RUNNING || echo MISMATCH
  fi
}
progress() {
  local ph ov
  ph=$(grep -a -o -E '^\[exp2\] ramp [0-9]+: (cooling|start|done|STOPPED|[ 0-9]+ s, [0-9]+ jobs)' "$OUT/$RUN.stdout.log" 2>/dev/null | tail -n 1 | sed 's/^\[exp2\] //')
  if [ -f "$R/exp2.meta.json" ]; then
    ov=$(grep -o -m1 '"exp2_outcome": *"[A-Z_0-9]*"' "$R/exp2.meta.json" 2>/dev/null | tr -d ' "' | sed 's/exp2_outcome://')
    [ -n "$ov" ] || ov="unreadable"
  else ov="none-yet"; fi
  echo "phase=${ph:-barrier/idle} exp2_outcome=$ov"
}
echo "token: VPID=$VPID VST=$VST MPID=$MPID MST=$MST"
signalled=0; sig_at=""; loss_at=""; intervention=0; reboot=0; t0=$(date +%s); end=$((t0 + CAP + 900)); i=0
if ! boot_ok; then
  reboot=1; intervention=1
  echo "== BOOT ID CHANGED since launch (now $(cat "$BOOT_ID_FILE" 2>&1)): the processes named in MONITOR_OK cannot exist;"
  echo "   any matching PID is a different process. NO SIGNAL; INTERVENTION REQUIRED (inspect $R by hand)"
fi
while [ "$reboot" -eq 0 ]; do
  target=$((t0 + INTERVAL_S * i)); now=$(date +%s); [ "$now" -lt "$target" ] && sleep $((target - now))
  now=$(date +%s)
  if ! boot_ok; then
    reboot=1; intervention=1
    echo "== BOOT ID CHANGED during observation: NO SIGNAL; INTERVENTION REQUIRED"; break
  fi
  v=$(pstate "$VPID" "$VST" exp2); w=$(pstate "$MPID" "$MST" watchdog)
  kids=$(pgrep -P "$VPID" 2>/dev/null | tr '\n' ' '); nk=$(echo $kids | wc -w)
  nmp=0; for k in $kids; do case "$(tr '\0' ' ' < /proc/$k/cmdline 2>/dev/null)" in *multiprocessing*) nmp=$((nmp + 1));; esac; done
  if [ -f "$MLOG" ]; then mage=$(( now - $(stat -c %Y "$MLOG") )); mlast=$(tail -n 1 "$MLOG"); else mage=NA; mlast="(no monitor.log)"; fi
  t=$(cat "$THERMAL" 2>&1); th=$(vcgencmd get_throttled 2>&1); gov=$(cat "$CPUFREQ/scaling_governor" 2>&1)
  case "$t" in ''|*[!0-9]*) tc="unreadable($t)";; *) tc="$((t/1000)).$(printf '%03d' $((t%1000)))C";; esac
  last=$(grep -v '^$' "$OUT/$RUN.stdout.log" 2>/dev/null | tail -n 1 | cut -c1-200)
  echo "[$i $(date '+%T')] exp2=$v children=$nk (multiprocessing=$nmp) [$kids] watchdog=$w log_age=${mage}s temp=$tc $th gov=$gov $(progress)"
  echo "    monitor.log: $mlast"
  echo "    stdout:      $last"
  if [ "$mage" != NA ] && [ "$mage" -gt "$STALE_S" ] && [ "$w" = RUNNING ]; then
    echo "    ANOMALY: monitor.log not updated for ${mage}s while the watchdog is alive (no action)"
  fi
  [ "$gov" = performance ] || echo "    NOTE: governor is '$gov', not performance (no action; the run's frequency record will show it)"
  case "$v" in
    EXITED) echo "== VALIDATOR EXITED (observed $(date '+%F %T %Z'))"; break;;
    MISMATCH|UNREADABLE) echo "== VALIDATOR IDENTITY $v - NO SIGNAL; INTERVENTION REQUIRED"; intervention=1; break;;
  esac
  if [ "$w" != RUNNING ] && [ -z "$loss_at" ]; then
    sleep 5
    w2=$(pstate "$MPID" "$MST" watchdog)
    if [ "$w2" = RUNNING ]; then echo "    watchdog read $w then RUNNING 5 s later: transient, no action"
    else loss_at=$(date +%s); echo "== WATCHDOG CONFIRMED ABSENT ($w, then $w2 5 s later) at $(date '+%T'); exp2 should stop itself (MonitorLost)"; fi
  fi
  if [ -n "$loss_at" ] && [ "$signalled" -eq 0 ]; then
    v=$(pstate "$VPID" "$VST" exp2)
    if [ "$v" = RUNNING ] && [ $(( $(date +%s) - loss_at )) -ge "$LOSS_GRACE_S" ] && boot_ok; then
      if [ "$MIDRUN_SIGTERM" = 1 ]; then
        signalled=1; sig_at=$(date +%s)
        if kill -TERM "$VPID" 2>/dev/null; then
          echo "== MID-RUN POLICY: watchdog absent >= ${LOSS_GRACE_S}s and exp2 still running and verified: sent exactly ONE SIGTERM to $VPID at $(date '+%T')"
        else echo "== MID-RUN POLICY: SIGTERM to $VPID not delivered (it exited meanwhile); no further signal"; fi
      else echo "== MID-RUN POLICY disabled: NO SIGNAL; INTERVENTION REQUIRED"; signalled=2; intervention=1; fi
    fi
  fi
  if [ "$signalled" -eq 1 ] && [ "$intervention" -eq 0 ] && [ "$(pstate "$VPID" "$VST" exp2)" = RUNNING ] &&
     [ $(( $(date +%s) - sig_at )) -ge "$SURVIVE_S" ]; then
    intervention=1
    echo "== VALIDATOR STILL RUNNING ${SURVIVE_S}s AFTER THE ONE SIGTERM - NO FURTHER SIGNAL; INTERVENTION REQUIRED (observation continues, read-only)"
  fi
  [ "$now" -ge "$end" ] && { echo "== STATUS WINDOW ENDED (cap + 15 min) with exp2 $v"; [ "$v" = EXITED ] || intervention=1; break; }
  i=$((i + 1))
done
pat="${RUN%?}[${RUN: -1}]"
if [ "$reboot" -eq 0 ]; then
  echo "-- waiting up to ${FINAL_WAIT_S}s (read-only) for the watchdog and any workers to finish their own exit"
  for ((k = 0; k < FINAL_WAIT_S; k += 2)); do
    [ "$(pstate "$MPID" "$MST" watchdog)" != RUNNING ] && ! pgrep -P "$VPID" > /dev/null &&
      ! pgrep -A -f "$pat" > /dev/null && ! pgrep -A -f 'multiprocessin[g]' > /dev/null && break
    sleep 2
  done
fi
echo "== FINAL $(date '+%F %T %Z') signalled=$signalled"
echo "-- exp2 result lines:"; grep -a -A80 '^EXPERIMENT 2 RESULT' "$OUT/$RUN.stdout.log" 2>/dev/null | sed 's/^/  /'
echo "-- exp2.meta.json flags: $(grep -o -E '"(exp2_outcome|abort_reason|interrupted|undervoltage|jobs_complete|ramps_completed)": *("[^"]*"|null|true|false|[0-9]+)' "$R/exp2.meta.json" 2>/dev/null | tr '\n' ' ')"
echo "-- monitor.log (last 12 lines):"; tail -n 12 "$MLOG" 2>&1 | sed 's/^/  /'
echo "-- monitor.out bytes: $(stat -c %s "$OUT/$RUN.monitor.out" 2>&1)"
grep -q "INTERVENTION REQUIRED" "$MLOG" 2>/dev/null && { intervention=1; echo "-- monitor.log reports INTERVENTION REQUIRED"; }
tr -d ' \n' < "$R/exp2.meta.json" 2>/dev/null | grep -q '"unstopped_workers":\[[0-9]' && { intervention=1; echo "-- exp2.meta.json reports unstopped workers"; }
echo "-- watchdog: $(pstate "$MPID" "$MST" watchdog)"
left=$(pgrep -P "$VPID" 2>/dev/null | tr '\n' ' ')
runp=$(pgrep -A -af "$pat"); rrc=$?
mp=$(pgrep -A -af 'multiprocessin[g]'); mrc=$?
echo "-- children of exp2 pid: [${left}]"
echo "-- processes naming the run id: exit $rrc ${runp}"
echo "-- multiprocessing processes: exit $mrc ${mp}"
if [ -z "$left" ] && [ "$rrc" -eq 1 ] && [ "$mrc" -eq 1 ]; then echo "-- CLEANUP VERIFIED: no leftover processes"
else intervention=1; echo "-- LEFTOVERS OR UNKNOWN STATE (nothing signalled by this script for cleanup) - INTERVENTION REQUIRED if they persist"; fi
echo "-- temp $(cat "$THERMAL" 2>&1) mC  $(vcgencmd get_throttled 2>&1)  gov $(cat "$CPUFREQ/scaling_governor" 2>&1)"
if [ "$intervention" -eq 1 ]; then echo "== STATUS RESULT: INTERVENTION REQUIRED"; exit 6; fi
echo "== STATUS RESULT: run ended, cleanup verified"; exit 0
} < /dev/null 2>&1
# end of script
