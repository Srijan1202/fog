# TDAS Pi DUMMY-ONLY smoke test of the rev3 watchdog (tools/validation_monitor.sh) and monitor
# guard (monitor_guard.py). NO real validator, NO workload, NO setting changed.
# Delivered over SSH through stdin, like the launcher:  ssh <pi> 'bash -s' < tools/pi_smoke_test.sh
#
# What it does:
#  1. Refuses unless: non-interactive shell, required tools present, `pgrep -A` supported, no
#     experiment/validator/watchdog process running, and EVERY project file matches EXPECTED_HASHES.
#  2. Records device identity, boot id, OS/kernel, Python/NumPy/bash/procps versions, vcgencmd,
#     temperature, throttle flags, governors.
#  3. In a fresh mktemp directory /tmp/tdaspismoke.XXXXXX (its name is the unique TAG carried by
#     every test process) it copies monitor_guard.py and tools/validation_monitor.sh (hash-checked
#     again) and writes a DUMMY validator (sleep processes only, real monitor_guard barrier), then:
#       C1 barrier refusal: no watchdog, no MONITOR_OK -> the dummy exits without "load"
#       C2 happy path: watchdog verified (pid, start time, exe, argv, session, start line, fresh
#          reading from the REAL sensors), MONITOR_OK, dummy verifies it, dummy "load", normal end
#       C3 cap: MAX_RUN_S=6 -> one SIGTERM, graceful exit, leftover dummy workers get one SIGTERM
#       C4 watchdog lost: this script sends ONE SIGTERM to the watchdog IT started (identity
#          re-verified) -> the dummy validator stops itself (MonitorLost)
#       C5 validator ignores SIGTERM: watchdog logs INTERVENTION REQUIRED, no second signal, no
#          SIGKILL; the dummy then exits by itself
#  4. Verifies that no process carrying TAG remains (fail closed: leftovers are reported, never
#     signalled), appends every monitor.log / stdout / report to the record, removes the temp dir
#     only if nothing is left.
# Signals: only SIGTERM, only to processes this script started (pid + start time + argv with TAG
# re-checked first). Never SIGKILL. Never a pre-existing process.
# Record: <PROJECT>/<RECORD_DIR>/smoke-<UTC>-<host>-<pid>.log (+ .sha256); never overwritten.
# A PASS shows the watchdog/guard/barrier mechanics work on this device. It is NOT hardware thermal
# validation and does not run the real validator or GEMM.
# Exit status: 0 SMOKE PASS; 1 SMOKE FAIL; 3 refused (precondition); 4 interrupted; 5 setup error.
{
# BEGIN SITE CONFIG
HOST_EXPECTED=pi
B0=
PROJECT="$HOME/tdas-pilot"
RECORD_DIR=observations
THERMAL=/sys/class/thermal/thermal_zone0/temp
CPU_GOV_FILES="/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/cpu1/cpufreq/scaling_governor /sys/devices/system/cpu/cpu2/cpufreq/scaling_governor /sys/devices/system/cpu/cpu3/cpufreq/scaling_governor"
EXPECTED_HASHES='
0b5ce770e3281f3f2ea98808d6a3084ae1a68f9d450155cad5c3c8c28a9f36ca validate_workload.py
31b6d8f6955798a4395baeaa8733e8a13b16e600f12cfcd071e00354641ad152 workload.py
acc4d28a6f422fb22c307da3574e26efb3129610e73616bb49f7b719f9f454e3 monitor_guard.py
31665b5a42dce04b1d97dc66be2df7fc44cba97a7c72f285cad53beb2f59a285 runner.py
3cdc62dfc784b18733e248c883a0eab763d903389f0e652f4e3478adf408a019 sensors.py
05ab24e5ea4c8bad9e356990328a9b1b4529c1f40d95b08f73471b0d28ece864 PREREG_workload_validation.md
15adfeeb21b6fa44a857e5172066a1adeba92ad69be8376e8d1c7a283ebe4eba tools/validation_monitor.sh
'
TEST_THERMAL_OVERRIDE=     # LOCAL TESTS ONLY (fake temperature file for the watchdog copy); MUST be empty on the Pi
# END SITE CONFIG
PAT='validate_workloa[d]|validation_monito[r]|tdas_smok[e]|exp[123]_|prob[e]\.py|analyz[e]\.py|[w]orkload\.py|multiprocessin[g]|tdaspismok[e]'
REC=""; W=""; TAG=""; NPASS=0; NFAIL=0; INTERRUPTED=0
declare -a SPID SSTART SROLE
say() { [ -n "$REC" ] && printf '%s\n' "$*" >> "$REC"; printf '%s\n' "$*" 2>/dev/null; }
ok()  { NPASS=$((NPASS + 1)); say "PASS  $*"; }
bad() { NFAIL=$((NFAIL + 1)); say "FAIL  $*"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
st() { local n=$1 s; s=$(cat "/proc/$2/stat" 2>/dev/null) || return 1; s=${s##*) }; set -- $s; eval "echo \${$n}"; }   # 1=state 4=session 20=start
alive() { [ -e "/proc/$1" ] && [ "$(st 1 "$1")" != Z ]; }
mine() {   # $1 pid $2 start: still the process this script started (start time + TAG in argv)?
  [ -n "$TAG" ] && [ "$(st 20 "$1")" = "$2" ] && [ "$(st 1 "$1")" != Z ] &&
    case "$(tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null)" in *"$TAG"*) true;; *) false;; esac
}
track() { SPID+=("$1"); SSTART+=("$(st 20 "$1")"); SROLE+=("$2"); }
term_verified() {   # ONE SIGTERM to a process this script started, only if its identity re-verifies
  if mine "$1" "$2"; then kill -TERM "$1" 2>/dev/null && say "   sent ONE SIGTERM to $3 pid $1 (identity verified)"; return 0; fi
  say "   NO SIGNAL to $3 pid $1: identity not verified"; return 1
}
leftovers() { pgrep -af "$TAG" | grep -v -E "pgrep|grep -v" ; }
wait_gone() {   # $1 seconds: wait for every TAG process to end by itself
  local k; for ((k = 0; k < $1 * 2; k++)); do [ -z "$(leftovers)" ] && return 0; sleep 0.5; done; [ -z "$(leftovers)" ]
}
finish() {   # $1 exit code, $2 result
  trap '' INT TERM HUP
  local left=""
  if [ -n "$TAG" ]; then
    wait_gone 60; left=$(leftovers)
    if [ -n "$left" ]; then
      say "== LEFTOVER test processes (NOT signalled) - INTERVENTION REQUIRED:"; say "$left"
      [ "$1" -eq 0 ] && set -- 1 "SMOKE FAIL: leftover test processes"
    else
      say "== cleanup verified: no process carries $TAG"
    fi
    if [ -d "$W/proj/results" ]; then
      for f in $(find "$W/proj/results" -type f | sort); do say "---- ${f#$W/} ----"; [ -n "$REC" ] && sed 's/^/  | /' "$f" >> "$REC"; done
    fi
    if [ -z "$left" ] && [ -n "$W" ] && [ "${W#/tmp/tdaspismoke.}" != "$W" ]; then rm -rf "$W" && say "== removed $W"
    elif [ -n "$W" ]; then say "== kept $W (processes left)"; fi
  fi
  say "== RESULT: $2   (checks: $NPASS passed, $NFAIL failed)"
  [ -n "$TEST_THERMAL_OVERRIDE" ] && say "== LOCAL TEST MODE (THERMAL overridden) - NOT A RESULT FROM THE PI"
  say "== SMOKE TEST ONLY - not hardware thermal validation; the real validator and GEMM were not run"
  say "== end $(date -u '+%F %T UTC'); exit $1"
  if [ -n "$REC" ]; then
    ( cd "$(dirname "$REC")" && sha256sum "$(basename "$REC")" > "$(basename "$REC").sha256" ) 2>/dev/null
    printf '%s\n' "record: $REC sha256 $(cut -d' ' -f1 < "$REC.sha256" 2>/dev/null)" 2>/dev/null
  fi
  exit "$1"
}
on_signal() {   # interruption: one verified SIGTERM to each dummy VALIDATOR still running (they stop gracefully),
                # then one to each of their DUMMY WORKERS still running 10 s later (listed in this run's
                # children.txt AND carrying TAG and "dummy worker" in their argv); never anything else
  trap '' INT TERM HUP
  INTERRUPTED=1
  say "== INTERRUPTED ($1): stopping the dummy validators this script started"
  local k p s; for k in "${!SPID[@]}"; do
    [ "${SROLE[$k]}" = validator ] && mine "${SPID[$k]}" "${SSTART[$k]}" && term_verified "${SPID[$k]}" "${SSTART[$k]}" "dummy validator"
  done
  for k in $(seq 1 20); do [ -z "$(leftovers)" ] && break; sleep 0.5; done
  for p in $(cat "$W"/proj/results/smoke/*/children.txt 2>/dev/null); do
    s=$(st 20 "$p") || continue
    case "$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)" in *"dummy worker $TAG"*) term_verified "$p" "$s" "dummy worker";; esac
  done
  finish 4 "INTERRUPTED ($1) - smoke test incomplete, no verdict"
}
trap '' PIPE
case "$-" in *i*|*m*) printf '%s\n' "REFUSED: interactive shell or job control"; exit 3;; esac
cd "$PROJECT" 2>/dev/null || { printf '%s\n' "SETUP ERROR: cannot cd to $PROJECT"; exit 5; }
for tool in bash python3 bc vcgencmd pgrep setsid nohup sha256sum awk mktemp hostname; do
  command -v "$tool" > /dev/null || { printf '%s\n' "REFUSED: required tool missing: $tool"; exit 3; }
done
pgrep -A -f "tdas-nonexistent-$$-probe" > /dev/null; prc=$?
[ "$prc" -eq 1 ] || { printf '%s\n' "REFUSED: 'pgrep -A' not usable (exit $prc); the launcher's conflict check needs it - design decision required"; exit 3; }
out=$(pgrep -A -af "$PAT"); rc=$?
[ "$rc" -eq 1 ] || { printf '%s\n' "REFUSED: conflicting process(es) running (pgrep exit $rc): $out"; exit 3; }
[ "$(hostname)" = "$HOST_EXPECTED" ] || { printf '%s\n' "REFUSED: hostname $(hostname), expected $HOST_EXPECTED"; exit 3; }
bad_hash=""
while read -r h f; do
  [ -n "$h" ] || continue
  case "$h" in *[!0-9a-f]*) bad_hash="$bad_hash malformed:$f";; esac
  [ "${#h}" -eq 64 ] || bad_hash="$bad_hash malformed:$f"
  [ -f "$f" ] || { bad_hash="$bad_hash missing:$f"; continue; }
  [ "$(sha256sum < "$f" | cut -d' ' -f1)" = "$h" ] || bad_hash="$bad_hash mismatch:$f"
done <<< "$EXPECTED_HASHES"
mkdir -p "$RECORD_DIR" 2>/dev/null || { printf '%s\n' "SETUP ERROR: cannot create $PROJECT/$RECORD_DIR"; exit 5; }
name="smoke-$(date -u +%Y%m%dT%H%M%SZ)-$(hostname)-$$"
set -o noclobber
: > "$RECORD_DIR/$name.log" 2>/dev/null || { printf '%s\n' "SETUP ERROR: record exists or cannot be created"; exit 5; }
set +o noclobber
REC="$PROJECT/$RECORD_DIR/$name.log"
say "== TDAS Pi DUMMY-ONLY SMOKE TEST start $(date -u '+%F %T UTC') record $REC"
say "host $(hostname) model '$(tr -d '\0' < /proc/device-tree/model 2>/dev/null)' boot_id $(cat /proc/sys/kernel/random/boot_id) uptime_s $(cut -d' ' -f1 /proc/uptime)"
[ -n "$B0" ] && { [ "$(cat /proc/sys/kernel/random/boot_id)" = "$B0" ] && say "boot id equals configured B0" || say "NOTE: boot id differs from configured B0 $B0 (reboot since B0 was recorded)"; }
say "os '$(. /etc/os-release 2>/dev/null; echo "$PRETTY_NAME")' kernel $(uname -r) arch $(uname -m)"
say "python3 $(readlink -f "$(command -v python3)") $(python3 --version 2>&1); numpy $(python3 -c 'import numpy; print(numpy.__version__)' 2>&1 | tail -n 1)"
say "bash $BASH_VERSION; $(pgrep -V 2>&1 | head -n 1); bc $(command -v bc); vcgencmd $(command -v vcgencmd)"
say "vcgencmd get_throttled: $(vcgencmd get_throttled 2>&1); temp_mC $(cat "$THERMAL" 2>&1); governors $(for f in $CPU_GOV_FILES; do printf '%s ' "$(cat "$f" 2>&1)"; done)"
say "pgrep -A probe exit $prc (1 = supported); conflict check exit $rc (1 = none)"
while read -r h f; do [ -n "$h" ] && say "sha256 $f $( [ -f "$f" ] && sha256sum < "$f" | cut -d' ' -f1 || echo MISSING) expected $h"; done <<< "$EXPECTED_HASHES"
[ -z "$bad_hash" ] || finish 3 "REFUSED: project files do not match EXPECTED_HASHES:$bad_hash"
trap 'on_signal SIGINT' INT; trap 'on_signal SIGTERM' TERM; trap 'on_signal SIGHUP' HUP

W=$(mktemp -d /tmp/tdaspismoke.XXXXXX) || finish 5 "SETUP ERROR: mktemp failed"
TAG=$(basename "$W")
say "== work dir $W; TAG $TAG (every test process carries it in its argv)"
mkdir -p "$W/proj/tools" "$W/proj/results/smoke"
cp monitor_guard.py "$W/proj/" && cp tools/validation_monitor.sh "$W/proj/tools/" || finish 5 "SETUP ERROR: copy failed"
[ "$(sha256sum < "$W/proj/monitor_guard.py")" = "$(sha256sum < monitor_guard.py)" ] &&
[ "$(sha256sum < "$W/proj/tools/validation_monitor.sh")" = "$(sha256sum < tools/validation_monitor.sh)" ] || finish 5 "SETUP ERROR: copies differ"
if [ -n "$TEST_THERMAL_OVERRIDE" ]; then
  sed -i "s#^THERMAL=/sys/class/thermal/thermal_zone0/temp\$#THERMAL=$TEST_THERMAL_OVERRIDE#" "$W/proj/tools/validation_monitor.sh"
  say "LOCAL TEST MODE: watchdog copy reads $TEST_THERMAL_OVERRIDE"
fi
cat > "$W/proj/validate_workload.py" <<'DUMMY'
#!/usr/bin/env python3
"""DUMMY validator for the Pi smoke test: same CLI and barrier as the real one (it uses the
real monitor_guard.py), but its "workers" are finite sleep processes. No numpy, no workload."""
import argparse, json, os, signal, subprocess, sys, time
import monitor_guard as mg

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True); ap.add_argument("--run-id", required=True)
    ap.add_argument("--require-monitor", type=float, required=True)
    ap.add_argument("--work-s", type=float, default=8); ap.add_argument("--children", type=int, default=2)
    ap.add_argument("--leave-children", action="store_true")
    ap.add_argument("--ignore-term-exit-after", type=float, default=0)
    a = ap.parse_args()
    root = os.path.join(a.outdir, a.run_id)
    os.makedirs(root)
    with open(os.path.join(root, "prereg.json"), "w") as f:
        json.dump({"dummy": True, "pid": os.getpid()}, f)
    report, rc, kids = {"overall": "INCOMPLETE", "dummy": True}, 0, []
    t_start = time.monotonic()
    def on_term(signum, frame):
        raise KeyboardInterrupt("SIGTERM")
    signal.signal(signal.SIGTERM, signal.SIG_IGN if a.ignore_term_exit_after else on_term)
    try:
        guard = mg.MonitorGuard(root, a.run_id)
        print(f"[validate] run {a.run_id}: waiting up to {a.require_monitor:.0f} s for a verified watchdog (no load until then)", flush=True)
        why = guard.wait(a.require_monitor)
        if why:
            report["reason"] = f"refused: monitoring not established ({why})"
            print(f"[validate] {report['reason']}", flush=True)
            return 2
        print(f"[validate] monitoring verified: {guard.describe()}", flush=True)
        for _ in range(a.children):
            kids.append(subprocess.Popen(["bash", "-c", "exec -a 'python3 -c from multiprocessing.spawn "
                                          f"import spawn_main [dummy worker {a.run_id}]' sleep 90"]))
        with open(os.path.join(root, "children.txt"), "w") as f:
            f.write(" ".join(str(p.pid) for p in kids) + "\n")
        print(f"[validate] dummy load started (sleep processes only): {[p.pid for p in kids]}", flush=True)
        end = time.monotonic() + a.work_s
        while time.monotonic() < end:
            guard.raise_if_lost()
            if a.ignore_term_exit_after and time.monotonic() - t_start > a.ignore_term_exit_after:
                report["self_exit"] = "exited by itself after ignoring SIGTERM"; break
            time.sleep(0.5)
        else:
            report["overall"] = "COMPLETE"
    except KeyboardInterrupt as e:
        report["interrupted"] = f"interrupted ({e})"; rc = 130
        print(f"[validate] interrupted: {e}", flush=True)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if not a.leave_children:
            for p in kids:
                p.terminate()
            for p in kids:
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    report.setdefault("children_alive", []).append(p.pid)
        with open(os.path.join(root, "report.json"), "w") as f:
            json.dump(report, f, indent=1)
        print(f"[validate] dummy exit rc={rc} report={report}", flush=True)
    return rc

if __name__ == "__main__":
    sys.exit(main())
DUMMY
say "dummy validator sha256 $(sha256sum < "$W/proj/validate_workload.py" | cut -d' ' -f1)"
cd "$W/proj" || finish 5 "SETUP ERROR: cd $W/proj"
OUT=results/smoke
READING='^[0-9-]+ [0-9:]+ +[0-9]+[.,][0-9]   0x[0-9a-fA-F]+$'
VPID=""; MPID=""; VST=""; MST=""
start_validator() {   # $1 case, $2 barrier s, then dummy options
  local run="$TAG-$1" b=$2; shift 2
  env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 \
    setsid nohup python3 -u validate_workload.py --outdir "$OUT" --run-id "$run" --require-monitor "$b" "$@" \
    > "$OUT/$run.stdout.log" 2>&1 < /dev/null &
  VPID=$!; local k; for k in $(seq 1 40); do [ -f "$OUT/$run/prereg.json" ] && break; sleep 0.25; done
  VST=$(st 20 "$VPID"); track "$VPID" validator
}
start_watchdog() {   # $1 case, $2 MAX_RUN_S, $3 GRACE_S
  local run="$TAG-$1"
  env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 MAX_RUN_S="$2" GRACE_S="$3" \
    setsid nohup bash tools/validation_monitor.sh "$VPID" "$OUT/$run" > "$OUT/$run.monitor.out" 2>&1 < /dev/null &
  MPID=$!; local k; for k in $(seq 1 60); do grep -qE "$READING" "$OUT/$run/monitor.log" 2>/dev/null && break; sleep 0.25; done
  MST=$(st 20 "$MPID"); track "$MPID" watchdog
}
verify_and_token() {   # $1 case: launcher-equivalent identity checks, then MONITOR_OK (atomic, no overwrite)
  local run="$TAG-$1" ml="$OUT/$TAG-$1/monitor.log"
  check "$1 watchdog alive, own session, exe bash" '[ -n "$MST" ] && alive "$MPID" && [ "$(st 4 "$MPID")" = "$MPID" ] && case "$(readlink -f /proc/$MPID/exe)" in */bash) true;; *) false;; esac'
  check "$1 watchdog argv exact" '[ "$(tr "\0" " " < /proc/$MPID/cmdline)" = "bash tools/validation_monitor.sh $VPID $OUT/$run " ]'
  check "$1 watchdog start line names this dummy, start time and run" 'grep -qF "monitor start: pid=$VPID start=$VST run=$run limits:" "$ml"'
  check "$1 fresh reading from the sensors in monitor.log" 'grep -qE "$READING" "$ml" && [ $(( $(date +%s) - $(stat -c %Y "$ml") )) -le 10 ]'
  check "$1 dummy validator alive, own session, no children yet" 'alive "$VPID" && [ "$(st 4 "$VPID")" = "$VPID" ] && ! pgrep -P "$VPID" > /dev/null'
  set -o noclobber
  printf 'vpid=%s vstart=%s mpid=%s mstart=%s\n' "$VPID" "$VST" "$MPID" "$MST" > "$OUT/$run/.MONITOR_OK.tmp" && mv -n "$OUT/$run/.MONITOR_OK.tmp" "$OUT/$run/MONITOR_OK"
  set +o noclobber
}
wait_line() { local k; for k in $(seq 1 $(( $3 * 2 ))); do grep -qE "$2" "$1" 2>/dev/null && return 0; sleep 0.5; done; return 1; }

say "== C1 barrier refusal: no watchdog, no MONITOR_OK -> dummy exits without load"
start_validator C1 4 --work-s 5
wait_line "$OUT/$TAG-C1/report.json" '"reason"' 20
check "C1 dummy refused at its barrier" 'grep -q "refused: monitoring not established" "$OUT/$TAG-C1/report.json"'
check "C1 no dummy load ever started" '[ ! -e "$OUT/$TAG-C1/children.txt" ]'
check "C1 dummy exited by itself" 'wait_gone 10 || ! alive "$VPID"'

say "== C2 happy path: verified watchdog with the real sensors, MONITOR_OK, dummy load, normal end"
start_validator C2 30 --work-s 8 --children 2
start_watchdog C2 300 10
verify_and_token C2
check "C2 dummy acknowledged the verified watchdog" 'wait_line "$OUT/$TAG-C2.stdout.log" "monitoring verified" 15'
check "C2 dummy load only after verification" '[ -e "$OUT/$TAG-C2/children.txt" ] && [ "$(stat -c %Y "$OUT/$TAG-C2/MONITOR_OK")" -le "$(stat -c %Y "$OUT/$TAG-C2/children.txt")" ]'
check "C2 watchdog: validator finished normally" 'wait_line "$OUT/$TAG-C2/monitor.log" "monitor exit \(validator finished\)$" 60'
check "C2 temperature readings plausible (10-90 C) and flags hex" 'awk "/   0x[0-9a-fA-F]+\$/ { n++; if (\$3 < 10 || \$3 > 90) bad = 1 } END { exit !(n >= 1 && !bad) }" "$OUT/$TAG-C2/monitor.log"'
check "C2 report COMPLETE, no watchdog signal" 'grep -q "\"overall\": \"COMPLETE\"" "$OUT/$TAG-C2/report.json" && ! grep -q "SIGTERM" "$OUT/$TAG-C2/monitor.log"'

say "== C3 cap: MAX_RUN_S=6 -> one SIGTERM, graceful exit, leftover dummy workers get one SIGTERM each"
start_validator C3 30 --work-s 120 --children 2 --leave-children
start_watchdog C3 6 10
verify_and_token C3
check "C3 watchdog stopped the run at the cap" 'wait_line "$OUT/$TAG-C3/monitor.log" "monitor exit" 90 && grep -q "STOP: run exceeded 6s" "$OUT/$TAG-C3/monitor.log"'
check "C3 exactly one SIGTERM to the dummy validator; it exited" '[ "$(grep -c "SIGTERM sent to $VPID\$" "$OUT/$TAG-C3/monitor.log")" -eq 1 ] && grep -qE "validator exited [0-9]+s after SIGTERM" "$OUT/$TAG-C3/monitor.log"'
check "C3 leftover workers got one SIGTERM each; clean exit; no SIGKILL" '[ "$(grep -c "still alive after validator exit -> SIGTERM" "$OUT/$TAG-C3/monitor.log")" -eq 2 ] && grep -q "monitor exit (stopped)$" "$OUT/$TAG-C3/monitor.log" && ! grep -q "SIGKILL" "$OUT/$TAG-C3/monitor.log"'
check "C3 dummy report INCOMPLETE (interrupted)" 'grep -q "interrupted (SIGTERM)" "$OUT/$TAG-C3/report.json"'

say "== C4 watchdog lost: ONE SIGTERM to the watchdog this script started -> dummy stops itself"
start_validator C4 30 --work-s 120 --children 2
start_watchdog C4 300 10
verify_and_token C4
wait_line "$OUT/$TAG-C4/children.txt" . 15
term_verified "$MPID" "$MST" "C4 watchdog"
check "C4 dummy validator stopped itself (watchdog lost)" 'wait_line "$OUT/$TAG-C4/report.json" "watchdog lost" 20'
check "C4 watchdog left the validator alone" 'grep -q "monitor received a signal; exiting without touching the validator" "$OUT/$TAG-C4/monitor.log"'

say "== C5 dummy ignores SIGTERM: INTERVENTION REQUIRED, no second signal, no SIGKILL; dummy exits by itself"
start_validator C5 30 --work-s 120 --children 0 --ignore-term-exit-after 25
start_watchdog C5 4 3
verify_and_token C5
check "C5 intervention logged after GRACE_S, validator not killed" 'wait_line "$OUT/$TAG-C5/monitor.log" "no SIGKILL; INTERVENTION REQUIRED" 30 && alive "$VPID"'
check "C5 dummy exited by itself; watchdog saw it; marker in exit line" 'wait_line "$OUT/$TAG-C5/monitor.log" "monitor exit \(stopped; INTERVENTION REQUIRED\)" 60 && grep -q "validator exited (after the intervention wait)" "$OUT/$TAG-C5/monitor.log"'
check "C5 exactly one SIGTERM, no escalation (the dummy ended by itself and wrote its report)" '[ "$(grep -c "SIGTERM sent to" "$OUT/$TAG-C5/monitor.log")" -eq 1 ] && grep -q "self_exit" "$OUT/$TAG-C5/report.json"'

cd "$PROJECT" || true
check "no test process left (waited up to 60 s)" 'wait_gone 60'
if [ "$NFAIL" -eq 0 ]; then finish 0 "SMOKE PASS"; else finish 1 "SMOKE FAIL"; fi
} < /dev/null 2>&1
# end of script
