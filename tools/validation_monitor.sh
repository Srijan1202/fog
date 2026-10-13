#!/usr/bin/env bash
# tools/validation_monitor.sh - external watchdog for validate_workload.py
#
#   tools/validation_monitor.sh <validator_pid> <run_dir>
#
# A BACKUP to the validator's own guard (65 C, checked after every job). Every
# 5 s it reads the SoC temperature (sysfs) and the throttle flags (vcgencmd),
# and stops the validator if any of these happens:
#   - temperature >= 67.0 C                       (pre-registered backup limit)
#   - temperature rises > 15 C between two consecutive readings (5 s apart)
#   - under-voltage: get_throttled bit 0 now, or sticky bit 16 newly set
#   - 3 consecutive failed temperature or throttle-flag reads
#   - the run lasts longer than MAX_RUN_S (default 7200 s; operational
#     backstop, not a pre-registered threshold; worst case run is ~65 min)
# Stop = SIGTERM (the validator then stops its worker pools and writes
# report.json with the interruption recorded); SIGKILL only if it is still
# alive after GRACE_S. The validator's worker processes exit on their own
# when their parent dies; any recorded worker still alive afterwards is
# terminated too.
#
# SAFETY OF PROCESS IDENTIFICATION: the monitor refuses to start unless
# <validator_pid> is a live process whose command line contains
# "validate_workload.py" and the run id (the basename of <run_dir>). It
# records that process's start time (/proc/<pid>/stat field 22) and re-checks
# BOTH the start time and the command line before every signal, so a reused
# PID is never signalled. Worker cleanup only touches processes recorded as
# children of the validator while it was running, again re-checked by start
# time and command line.
#
# Events are logged to <run_dir>/monitor.log. The monitor exits when the
# validator exits. It never changes any hardware setting.
#
# Launch (see README "Launching the workload validation"): start the validator
# detached with --run-id, wait for its run directory, then start this script
# detached with the validator's PID and that directory. Both are under
# setsid nohup, so an SSH disconnection affects neither.

set -u

LIMIT_MC=67000
RISE_MC=15000
INTERVAL_S=5
MAX_FAILS=3
GRACE_S=${GRACE_S:-30}
MAX_RUN_S=${MAX_RUN_S:-7200}
THERMAL=/sys/class/thermal/thermal_zone0/temp

if [ $# -ne 2 ]; then
    echo "usage: $0 <validator_pid> <run_dir>" >&2
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
    *validate_workload.py*"$RUN_ID"*) ;;
    *) echo "pid $VPID is not validate_workload.py for run $RUN_ID: $VCMD" >&2; exit 2;;
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
    for c in "${!CHILD_START[@]}"; do
        if same_proc "$c" "${CHILD_START[$c]}" multiprocessing; then
            log "worker $c still alive after validator exit -> SIGTERM"
            kill -TERM "$c" 2>/dev/null
            sleep 2
            if same_proc "$c" "${CHILD_START[$c]}" multiprocessing; then
                log "worker $c still alive -> SIGKILL"
                kill -KILL "$c" 2>/dev/null
            fi
        fi
    done
}

stop_validator() {
    log "STOP: $1"
    if same_proc "$VPID" "$VSTART" validate_workload.py; then
        kill -TERM "$VPID" 2>/dev/null && log "SIGTERM sent to $VPID"
        local waited=0
        while same_proc "$VPID" "$VSTART" validate_workload.py && [ $waited -lt "$GRACE_S" ]; do
            sleep 1
            waited=$((waited + 1))
        done
        if same_proc "$VPID" "$VSTART" validate_workload.py; then
            log "validator still alive after ${GRACE_S}s -> SIGKILL"
            kill -KILL "$VPID" 2>/dev/null
        else
            log "validator exited ${waited}s after SIGTERM"
        fi
    else
        log "validator already gone; nothing signalled"
    fi
    cleanup_children
    log "monitor exit (stopped)"
    exit 0
}

trap 'log "monitor received a signal; exiting without touching the validator"; exit 0' TERM INT HUP

TH0=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
case "$TH0" in 0x*) ;; *) TH0=0x0; log "initial throttle-flag read failed; using 0x0 as baseline";; esac
log "monitor start: pid=$VPID start=$VSTART run=$RUN_ID limits: ${LIMIT_MC} mC, rise >${RISE_MC} mC/${INTERVAL_S}s, under-voltage (bit 0 / new bit 16), ${MAX_FAILS} read fails, max ${MAX_RUN_S}s; initial flags $TH0"
log "# time       temp_C  throttled"

T0=$(date +%s)
prev=""
fails=0
while same_proc "$VPID" "$VSTART" validate_workload.py; do
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
log "monitor exit (validator finished)"
exit 0
