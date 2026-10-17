#!/usr/bin/env bash
# tools/validation_monitor.sh - external watchdog for validate_workload.py
#
#   tools/validation_monitor.sh <validator_pid> <run_dir>
#
# A BACKUP to the validator's own guard (65 C, checked after every job). Every
# 5 s it reads the SoC temperature (sysfs) and the under-voltage alarm (the
# kernel's rpi_volt hwmon driver, in0_lcrit_alarm), and stops the validator if
# any of these happens:
#   - temperature >= 67.0 C                       (pre-registered backup limit)
#   - temperature rises > 15 C between two consecutive readings (5 s apart)
#   - under-voltage now (hwmon in0_lcrit_alarm = 1)
#   - 3 consecutive failed temperature or alarm reads
#   - the run lasts longer than MAX_RUN_S (default 7200 s; operational
#     backstop, not a pre-registered threshold)
# NO PROCESS IS STARTED WHILE THE VALIDATOR RUNS (fork-free loop, P4c): every
# reading, identity check, child scan, log line and wait uses bash builtins
# (read, mapfile, printf -v, $EPOCHSECONDS, read -t on a held pipe). The earlier
# loop started ~12 short processes every 5 s (pgrep, cat, tr, vcgencmd, cut,
# date, bc, sleep); with all four cores busy that burst preempted the
# validator's workers and caused the stalls that made P4b fail A6
# (diagnostic diag-stalls-20261017-02). vcgencmd runs only at monitor start and
# after the validator has exited: the start read is the baseline, and at the end
# the monitor logs whether under-voltage occurred (sticky bit 16) during the
# run. The validator itself reads the full throttle flags between jobs and
# audits the sticky bits before and after every block.
# Stop = ONE SIGTERM (the validator then stops its worker pools and writes
# report.json with the interruption recorded). NO SIGKILL: if the validator is
# still alive after GRACE_S, the monitor logs "INTERVENTION REQUIRED" and keeps
# logging readings, without further signals, until it exits. The validator's
# workers exit on their own when their parent dies; a recorded worker still
# alive afterwards gets one SIGTERM, and one still alive 10 s later is logged
# as "INTERVENTION REQUIRED" (never SIGKILLed). The final line says
# "monitor exit (...; INTERVENTION REQUIRED)" whenever that happened.
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
# Launch: start the validator detached with --run-id and --require-monitor,
# wait for its run directory, then start this script detached with the
# validator's PID and that directory. The launcher verifies this monitor and
# only then writes <run_dir>/MONITOR_OK; the validator does no load before
# that (monitor_guard.py). Both run under setsid nohup.

set -u

LIMIT_MC=67000
RISE_MC=15000
INTERVAL_S=5
MAX_FAILS=3
GRACE_S=${GRACE_S:-30}
MAX_RUN_S=${MAX_RUN_S:-7200}
THERMAL=/sys/class/thermal/thermal_zone0/temp
HWMON_ROOT=/sys/class/hwmon

if [ $# -ne 2 ]; then
    echo "usage: $0 <validator_pid> <run_dir>" >&2
    exit 2
fi
VPID=$1
RUN_DIR=$2
RUN_ID=${RUN_DIR%/}; RUN_ID=${RUN_ID##*/}
LOG="$RUN_DIR/monitor.log"

case "$VPID" in ''|*[!0-9]*) echo "validator pid must be a number" >&2; exit 2;; esac
if [ ! -d "$RUN_DIR" ]; then
    echo "run directory $RUN_DIR does not exist" >&2
    exit 2
fi

# ---- fork-free helpers (builtins only; results in globals) -------------------
log() { local ts; printf -v ts '%(%F %T)T' -1; printf '%s %s\n' "$ts" "$*" >> "$LOG"; }

ST=""   # start time (clock ticks since boot) of a pid; returns 1 if it does not exist
start_time() {
    local s
    ST=""
    { read -r s < "/proc/$1/stat"; } 2>/dev/null || return 1
    s=${s##*) }                  # drop "pid (comm) "; comm may contain spaces
    set -- $s
    ST=${20}                     # field 22 overall = 20th after pid and comm
    [ -n "$ST" ]
}
CL=""   # command line of a pid, NULs as spaces
cmdline() {
    local -a a=()
    CL=""
    { mapfile -d '' -t a < "/proc/$1/cmdline"; } 2>/dev/null || return 1
    CL="${a[*]}"
}

# is <pid> still the exact process recorded as <start> whose cmdline has <text>?
same_proc() {
    start_time "$1" || return 1
    [ "$ST" = "$2" ] || return 1
    cmdline "$1" || return 1
    case "$CL" in *"$3"*) return 0;; *) return 1;; esac
}

wait_s() { read -r -t "$1" -u "$SLEEPFD" _ 2>/dev/null; return 0; }   # builtin sleep

start_time "$VPID" || { echo "pid $VPID not running" >&2; exit 2; }
VSTART=$ST
cmdline "$VPID"; VCMD=$CL
case "$VCMD" in
    *validate_workload.py*"$RUN_ID"*) ;;
    *) echo "pid $VPID is not validate_workload.py for run $RUN_ID: $VCMD" >&2; exit 2;;
esac

UV_ALARM=""                  # rpi_volt in0_lcrit_alarm: under-voltage now
for h in "$HWMON_ROOT"/hwmon*; do
    n=""; { read -r n < "$h/name"; } 2>/dev/null
    [ "$n" = rpi_volt ] && [ -r "$h/in0_lcrit_alarm" ] && UV_ALARM="$h/in0_lcrit_alarm" && break
done
[ -n "$UV_ALARM" ] || { echo "no rpi_volt hwmon under-voltage alarm under $HWMON_ROOT; refusing to start" >&2; exit 2; }

exec {SLEEPFD}<> <(:)        # a pipe nobody writes: read -t on it is a builtin sleep

declare -A CHILD_START       # worker pid -> start time, recorded while validator lives

CHILD_SRC=children           # per-task children files (CONFIG_PROC_CHILDREN, as on the Pi)
[ -r "/proc/$VPID/task/$VPID/children" ] || CHILD_SRC=scan     # else a builtin /proc scan
record_children() {
    local f c s kids=()
    if [ "$CHILD_SRC" = children ]; then
        for f in "/proc/$VPID/task/"*/children; do
            kids=()
            { read -r -a kids < "$f"; } 2>/dev/null   # no trailing newline: read returns 1, kids is set
            for c in "${kids[@]}"; do
                [ -n "${CHILD_START[$c]:-}" ] && continue
                start_time "$c" || continue
                cmdline "$c" || continue
                case "$CL" in *multiprocessing*) CHILD_START[$c]=$ST ;; esac
            done
        done
        return 0
    fi
    for f in /proc/[0-9]*/stat; do          # fallback: parent pid from every stat line
        c=${f#/proc/}; c=${c%/stat}
        [ -n "${CHILD_START[$c]:-}" ] && continue
        { read -r s < "$f"; } 2>/dev/null || continue
        s=${s##*) }
        set -- $s                          # 1 = state, 2 = ppid, 20 = start time
        [ "${2:-}" = "$VPID" ] || continue
        cmdline "$c" || continue
        case "$CL" in *multiprocessing*) CHILD_START[$c]=${20} ;; esac
    done
}

INTERVENTION=0     # set when a process survives its one SIGTERM (never SIGKILLed)

READ_T=""; READ_UV=""; READING=""
take_reading() {     # sets READ_T, READ_UV and READING; returns 1 on a failed read
    local tenths
    READ_T=""; READ_UV=""; READING=""
    { read -r READ_T < "$THERMAL"; } 2>/dev/null
    { read -r READ_UV < "$UV_ALARM"; } 2>/dev/null
    [[ "$READ_T" =~ ^[0-9]+$ ]] && [[ "$READ_UV" =~ ^[01]$ ]] || return 1
    tenths=$(( (READ_T + 50) / 100 ))
    printf -v READING '%4d.%d   uv=%d' $((tenths / 10)) $((tenths % 10)) "$READ_UV"
}

# one reading line, no limit checks (used while waiting without signals)
log_reading() {
    if take_reading; then log "$READING"
    else log "READ FAILURE while waiting (temp='$READ_T' uv='$READ_UV')"; fi
}

cleanup_children() {
    local c left=0 i
    for i in 1 2 3 4 5; do          # workers exit by themselves within ~0.5 s
        left=0
        for c in "${!CHILD_START[@]}"; do
            same_proc "$c" "${CHILD_START[$c]}" multiprocessing && left=1
        done
        [ $left -eq 0 ] && break
        wait_s 1
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
        wait_s 1
    done
    for c in "${!CHILD_START[@]}"; do      # no SIGKILL: report and leave for a person
        if same_proc "$c" "${CHILD_START[$c]}" multiprocessing; then
            log "worker $c (start ${CHILD_START[$c]}) still alive 10s after SIGTERM -> no SIGKILL; INTERVENTION REQUIRED"
            INTERVENTION=1
        fi
    done
}

final_flags() {      # after the validator has exited: vcgencmd is allowed again
    local th
    th=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
    case "$th" in
        0x*) if [ $(( th & 0x10000 )) -ne 0 ] && [ $(( TH0 & 0x10000 )) -eq 0 ]; then
                 log "final flags $th: under-voltage occurred during the run (sticky bit 16 newly set)"
             else
                 log "final flags $th"
             fi;;
        *) log "final flag read failed";;
    esac
}

finish_monitor() {   # $1 = how the run ended
    final_flags
    if [ "$INTERVENTION" = 1 ]; then
        log "monitor exit ($1; INTERVENTION REQUIRED)"
    else
        log "monitor exit ($1)"
    fi
    exit 0
}

stop_validator() {
    log "STOP: $1"
    if same_proc "$VPID" "$VSTART" validate_workload.py; then
        kill -TERM "$VPID" 2>/dev/null && log "SIGTERM sent to $VPID"
        local waited=0
        while same_proc "$VPID" "$VSTART" validate_workload.py && [ $waited -lt "$GRACE_S" ]; do
            wait_s 1
            waited=$((waited + 1))
        done
        if same_proc "$VPID" "$VSTART" validate_workload.py; then
            log "validator still alive after ${GRACE_S}s -> no SIGKILL; INTERVENTION REQUIRED"
            INTERVENTION=1
            local n=0
            while same_proc "$VPID" "$VSTART" validate_workload.py; do   # keep monitoring, no signals
                record_children
                log_reading
                n=$((n + 1))
                [ $((n % 12)) -eq 0 ] && log "validator $VPID still alive, no further signal; INTERVENTION REQUIRED"
                wait_s $INTERVAL_S
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
log "monitor start: pid=$VPID start=$VSTART run=$RUN_ID limits: ${LIMIT_MC} mC, rise >${RISE_MC} mC/${INTERVAL_S}s, under-voltage now (hwmon alarm), ${MAX_FAILS} read fails, max ${MAX_RUN_S}s; initial flags $TH0; fork-free loop; children via $CHILD_SRC"
log "# time       temp_C  under-voltage"

T0=$EPOCHSECONDS
prev=""
fails=0
while same_proc "$VPID" "$VSTART" validate_workload.py; do
    record_children
    if ! take_reading; then
        fails=$((fails + 1))
        log "READ FAILURE #$fails (temp='$READ_T' uv='$READ_UV')"
        [ $fails -ge $MAX_FAILS ] && stop_validator "$MAX_FAILS consecutive failed reads"
    else
        fails=0
        T=$READ_T
        log "$READING"
        [ "$T" -ge $LIMIT_MC ] && stop_validator "temperature $T mC >= $LIMIT_MC mC"
        if [ -n "$prev" ] && [ $((T - prev)) -gt $RISE_MC ]; then
            stop_validator "temperature rose $((T - prev)) mC in ${INTERVAL_S}s"
        fi
        [ "$READ_UV" -ne 0 ] && stop_validator "under-voltage now (hwmon alarm)"
        prev=$T
    fi
    [ $(( EPOCHSECONDS - T0 )) -ge "$MAX_RUN_S" ] && stop_validator "run exceeded ${MAX_RUN_S}s"
    wait_s $INTERVAL_S
done
log "validator $VPID exited"
cleanup_children
finish_monitor "validator finished"
