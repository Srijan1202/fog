#!/usr/bin/env bash
# tools/exp1_monitor.sh - external watchdog for exp1_thermal.py (Experiment 1)
#
#   tools/exp1_monitor.sh <exp1_pid> <outdir>
#
# Derived from tools/validation_monitor.sh: the same stop, signal and cleanup
# logic, with a different process identity (exp1_thermal.py) and temperature
# limit. Experiment 1 must drive the board into firmware throttling (a Pi 4
# throttles from about 80 C), so the stop is a backstop ABOVE that: 85.0 C, the
# SoC's rated maximum, chosen by the reviewer on 2026-10-16. If it is ever hit
# the run is stopped and recorded as ABORTED (INCONCLUSIVE for G1). Every 5 s
# it reads the SoC temperature (sysfs) and the throttle flags (vcgencmd), and
# stops exp1 if any of these happens:
#   - temperature >= 85.0 C                       (backstop above the knee)
#   - temperature rises > 15 C between two consecutive readings (5 s apart)
#   - under-voltage: get_throttled bit 0 now, or sticky bit 16 newly set
#   - 3 consecutive failed temperature or throttle-flag reads
#   - the run lasts longer than MAX_RUN_S (default 2700 s; operational
#     backstop; a 15 min heating + 10 min cooling run needs about 26 min)
# Stop = ONE SIGTERM (exp1 then stops its heat workers and writes exp1.meta.json
# marked ABORTED). NO SIGKILL: if exp1 is
# still alive after GRACE_S, the monitor logs "INTERVENTION REQUIRED" and keeps
# logging readings, without further signals, until it exits. The validator's
# workers exit on their own when their parent dies; a recorded worker still
# alive afterwards gets one SIGTERM, and one still alive 10 s later is logged
# as "INTERVENTION REQUIRED" (never SIGKILLed). The final line says
# "monitor exit (...; INTERVENTION REQUIRED)" whenever that happened.
#
# SAFETY OF PROCESS IDENTIFICATION: the monitor refuses to start unless
# <exp1_pid> is a live process whose command line contains
# "exp1_thermal.py" and the run id (the basename of <outdir>). It
# records that process's start time (/proc/<pid>/stat field 22) and re-checks
# BOTH the start time and the command line before every signal, so a reused
# PID is never signalled. Worker cleanup only touches processes recorded as
# children of the validator while it was running, again re-checked by start
# time and command line.
#
# Events are logged to <run_dir>/monitor.log. The monitor exits when the
# validator exits. It never changes any hardware setting.
#
# Launch: tools/launch_exp1.sh starts exp1 detached with --require-monitor and a
# fresh --outdir, then this script detached with exp1's PID and that directory,
# verifies it and only then writes <outdir>/MONITOR_OK; exp1 does no load
# before that (Exp1Guard in exp1_thermal.py). Both run under setsid nohup.

set -u

LIMIT_MC=85000
RISE_MC=15000
INTERVAL_S=5
MAX_FAILS=3
GRACE_S=${GRACE_S:-30}
MAX_RUN_S=${MAX_RUN_S:-2700}
THERMAL=/sys/class/thermal/thermal_zone0/temp

if [ $# -ne 2 ]; then
    echo "usage: $0 <exp1_pid> <outdir>" >&2
    exit 2
fi
VPID=$1
RUN_DIR=$2
RUN_ID=$(basename "$RUN_DIR")
LOG="$RUN_DIR/monitor.log"

case "$VPID" in ''|*[!0-9]*) echo "validator pid must be a number" >&2; exit 2;; esac
if [ ! -d "$RUN_DIR" ]; then
    echo "run directory $RUN_DIR does not exist" >&2
    exit 2
fi

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# start time (clock ticks since boot) of a pid; empty if it does not exist
start_time() {
    local s
    s=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
    s=${s##*) }                  # drop "pid (comm) "; comm may contain spaces
    set -- $s
    echo "${20}"                 # field 22 overall = 20th after pid and comm
}
cmdline() { tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null; }

# is <pid> still the exact process recorded as <start> whose cmdline has <text>?
same_proc() {
    local st cl
    st=$(start_time "$1") || return 1
    [ "$st" = "$2" ] || return 1
    cl=$(cmdline "$1")
    case "$cl" in *"$3"*) return 0;; *) return 1;; esac
}

VSTART=$(start_time "$VPID") || { echo "pid $VPID not running" >&2; exit 2; }
VCMD=$(cmdline "$VPID")
case "$VCMD" in
    *exp1_thermal.py*"$RUN_ID"*) ;;
    *) echo "pid $VPID is not exp1_thermal.py for run $RUN_ID: $VCMD" >&2; exit 2;;
esac

declare -A CHILD_START       # worker pid -> start time, recorded while validator lives

record_children() {
    local c st
    for c in $(pgrep -P "$VPID" 2>/dev/null); do
        [ -n "${CHILD_START[$c]:-}" ] && continue
        st=$(start_time "$c") || continue
        case "$(cmdline "$c")" in
            *multiprocessing*) CHILD_START[$c]=$st ;;
        esac
    done
}

INTERVENTION=0     # set when a process survives its one SIGTERM (never SIGKILLed)

# one temperature reading line, no limit checks (used while waiting without signals)
log_reading() {
    local t th
    t=$(cat "$THERMAL" 2>/dev/null); th=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
    if [[ "$t" =~ ^[0-9]+$ ]] && [[ "$th" =~ ^0x[0-9a-fA-F]+$ ]]; then
        log "$(printf '%6.1f' "$(echo "$t/1000" | bc -l)")   $th"
    else
        log "READ FAILURE while waiting (temp='$t' flags='$th')"
    fi
}

cleanup_children() {
    local c left=0 i
    for i in 1 2 3 4 5; do          # workers exit by themselves within ~0.5 s
        left=0
        for c in "${!CHILD_START[@]}"; do
            same_proc "$c" "${CHILD_START[$c]}" multiprocessing && left=1
        done
        [ $left -eq 0 ] && break
        sleep 1
    done
    [ $left -eq 0 ] && return 0
    for c in "${!CHILD_START[@]}"; do      # one SIGTERM per verified leftover worker
        if same_proc "$c" "${CHILD_START[$c]}" multiprocessing; then
            log "worker $c still alive after validator exit -> SIGTERM"
            kill -TERM "$c" 2>/dev/null
        fi
    done
    for i in 1 2 3 4 5 6 7 8 9 10; do
        left=0
        for c in "${!CHILD_START[@]}"; do
            same_proc "$c" "${CHILD_START[$c]}" multiprocessing && left=1
        done
        [ $left -eq 0 ] && return 0
        sleep 1
    done
    for c in "${!CHILD_START[@]}"; do      # no SIGKILL: report and leave for a person
        if same_proc "$c" "${CHILD_START[$c]}" multiprocessing; then
            log "worker $c (start ${CHILD_START[$c]}) still alive 10s after SIGTERM -> no SIGKILL; INTERVENTION REQUIRED"
            INTERVENTION=1
        fi
    done
}

finish_monitor() {   # $1 = how the run ended
    if [ "$INTERVENTION" = 1 ]; then
        log "monitor exit ($1; INTERVENTION REQUIRED)"
    else
        log "monitor exit ($1)"
    fi
    exit 0
}

stop_validator() {
    log "STOP: $1"
    if same_proc "$VPID" "$VSTART" exp1_thermal.py; then
        kill -TERM "$VPID" 2>/dev/null && log "SIGTERM sent to $VPID"
        local waited=0
        while same_proc "$VPID" "$VSTART" exp1_thermal.py && [ $waited -lt "$GRACE_S" ]; do
            sleep 1
            waited=$((waited + 1))
        done
        if same_proc "$VPID" "$VSTART" exp1_thermal.py; then
            log "validator still alive after ${GRACE_S}s -> no SIGKILL; INTERVENTION REQUIRED"
            INTERVENTION=1
            local n=0
            while same_proc "$VPID" "$VSTART" exp1_thermal.py; do   # keep monitoring, no signals
                record_children
                log_reading
                n=$((n + 1))
                [ $((n % 12)) -eq 0 ] && log "validator $VPID still alive, no further signal; INTERVENTION REQUIRED"
                sleep $INTERVAL_S
            done
            log "validator exited (after the intervention wait)"
        else
            log "validator exited ${waited}s after SIGTERM"
        fi
    else
        log "validator already gone; nothing signalled"
    fi
    cleanup_children
    finish_monitor stopped
}

trap 'log "monitor received a signal; exiting without touching the validator"; exit 0' TERM INT HUP

TH0=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
case "$TH0" in 0x*) ;; *) TH0=0x0; log "initial throttle-flag read failed; using 0x0 as baseline";; esac
log "monitor start: pid=$VPID start=$VSTART run=$RUN_ID limits: ${LIMIT_MC} mC, rise >${RISE_MC} mC/${INTERVAL_S}s, under-voltage (bit 0 / new bit 16), ${MAX_FAILS} read fails, max ${MAX_RUN_S}s; initial flags $TH0"
log "# time       temp_C  throttled"

T0=$(date +%s)
prev=""
fails=0
while same_proc "$VPID" "$VSTART" exp1_thermal.py; do
    record_children
    T=$(cat "$THERMAL" 2>/dev/null)
    TH=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
    if ! [[ "$T" =~ ^[0-9]+$ ]] || ! [[ "$TH" =~ ^0x[0-9a-fA-F]+$ ]]; then
        fails=$((fails + 1))
        log "READ FAILURE #$fails (temp='$T' flags='$TH')"
        [ $fails -ge $MAX_FAILS ] && stop_validator "$MAX_FAILS consecutive failed reads"
    else
        fails=0
        log "$(printf '%6.1f' "$(echo "$T/1000" | bc -l)")   $TH"
        [ "$T" -ge $LIMIT_MC ] && stop_validator "temperature $T mC >= $LIMIT_MC mC"
        if [ -n "$prev" ] && [ $((T - prev)) -gt $RISE_MC ]; then
            stop_validator "temperature rose $((T - prev)) mC in ${INTERVAL_S}s"
        fi
        [ $(( TH & 0x1 )) -ne 0 ] && stop_validator "under-voltage flag set now ($TH)"
        if [ $(( TH & 0x10000 )) -ne 0 ] && [ $(( TH0 & 0x10000 )) -eq 0 ]; then
            stop_validator "under-voltage occurred since monitor start ($TH)"
        fi
        prev=$T
    fi
    [ $(( $(date +%s) - T0 )) -ge "$MAX_RUN_S" ] && stop_validator "run exceeded ${MAX_RUN_S}s"
    sleep $INTERVAL_S
done
log "validator $VPID exited"
cleanup_children
finish_monitor "validator finished"
