"""
sensors.py - platform-adaptive telemetry for the TDAS pilot.

Backends are tried in order and the first that works is used. Each backend
exposes the same read() -> dict interface so every downstream experiment is
hardware-agnostic.

Fields (None where unsupported on this platform):
    t_wall      float   monotonic seconds
    temp_c      float   SoC/CPU/GPU temperature, degrees C
    freq_mhz    float   current core/SM clock, MHz
    throttled   int     platform throttle bitfield/flag (0 = not throttled)
    energy_uj   int     cumulative energy counter, microjoules (for deltas)

Raspberry Pi only, and only when the caller sets record_arm_clock = True
(Experiment 1, Amendment A3):
    arm_clock_mhz float firmware ARM clock (vcgencmd measure_clock arm), MHz.
                        On the Pi 4, scaling_cur_freq (freq_mhz) does not show
                        firmware throttling; this field does.

IMPORTANT: energy_uj is a *cumulative* counter that wraps. Always use
successive differences and handle wraparound.
"""

from __future__ import annotations

import os
import glob
import shutil
import subprocess
import time
import math


class SensorBackend:
    name = "base"
    caps: dict = {}

    def available(self) -> bool:
        raise NotImplementedError

    def read(self) -> dict:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"backend": self.name, "caps": self.caps}


# --------------------------------------------------------------------------
# Raspberry Pi (vcgencmd gives a real throttle bitfield - the best case)
# --------------------------------------------------------------------------
class RaspberryPiBackend(SensorBackend):
    name = "raspberrypi"

    def __init__(self):
        self.vcgencmd = shutil.which("vcgencmd")
        self.thermal = "/sys/class/thermal/thermal_zone0/temp"
        self.freq = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
        # Off by default, so other experiments keep their validated sampling cost.
        self.record_arm_clock = False
        self.caps = {
            "temp": True,
            "freq": os.path.exists(self.freq),
            "throttle_flags": self.vcgencmd is not None,
            "arm_clock": self.vcgencmd is not None,
            "energy": False,
        }

    def available(self) -> bool:
        model = "/proc/device-tree/model"
        if not os.path.exists(model):
            return False
        try:
            with open(model, "rb") as f:
                txt = f.read().decode("utf-8", "ignore").lower()
            return "raspberry pi" in txt
        except Exception:
            return False

    def _vcgencmd(self, *args):
        if not self.vcgencmd:
            return None
        # Not subprocess.run(timeout=...): on timeout that SIGKILLs the child. Here a
        # hung vcgencmd gets ONE SIGTERM and the reading counts as unreadable (None);
        # it is never SIGKILLed.
        try:
            p = subprocess.Popen([self.vcgencmd, *args], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True)
        except Exception:
            return None
        try:
            out, _ = p.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            return None
        return out

    def _throttle(self):
        out = self._vcgencmd("get_throttled")
        try:
            # format: throttled=0x50005
            return int(out.strip().split("=")[1], 16)
        except Exception:
            return None

    def _arm_clock(self):
        out = self._vcgencmd("measure_clock", "arm")
        try:
            # format: frequency(48)=1800457088 (Hz); 0 or garbage = unreadable
            hz = int(out.strip().split("=")[1])
        except Exception:
            return None
        return hz / 1e6 if hz > 0 else None

    def read(self) -> dict:
        temp = freq = None
        try:
            with open(self.thermal) as f:
                temp = int(f.read().strip()) / 1000.0
        except Exception:
            pass
        try:
            with open(self.freq) as f:
                freq = int(f.read().strip()) / 1000.0
        except Exception:
            pass
        r = {
            "t_wall": time.monotonic(),
            "temp_c": temp,
            "freq_mhz": freq,
            "throttled": self._throttle(),
            "energy_uj": None,
        }
        if self.record_arm_clock:
            r["arm_clock_mhz"] = self._arm_clock()
        return r


# --------------------------------------------------------------------------
# Generic Linux: coretemp / thermal_zone + cpufreq + RAPL
# --------------------------------------------------------------------------
class LinuxCPUBackend(SensorBackend):
    name = "linux_cpu"

    def __init__(self):
        self.temp_path = self._pick_temp()
        self.freq_path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
        self.rapl_path = self._pick_rapl()
        self.caps = {
            "temp": self.temp_path is not None,
            "freq": os.path.exists(self.freq_path),
            "throttle_flags": False,   # inferred from freq drop, not a flag
            "energy": self.rapl_path is not None,
        }

    @staticmethod
    def _pick_temp():
        # Prefer an x86 package sensor, else any thermal zone.
        for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            try:
                with open(os.path.join(hw, "name")) as f:
                    nm = f.read().strip()
            except Exception:
                continue
            if nm in ("coretemp", "k10temp", "zenpower", "cpu_thermal"):
                cands = sorted(glob.glob(os.path.join(hw, "temp*_input")))
                if cands:
                    return cands[0]
        zones = sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp"))
        return zones[0] if zones else None

    @staticmethod
    def _pick_rapl():
        for p in sorted(glob.glob("/sys/class/powercap/intel-rapl:*/energy_uj")):
            try:
                with open(p) as f:
                    f.read()
                return p
            except Exception:
                continue
        return None

    def available(self) -> bool:
        return self.temp_path is not None

    def read(self) -> dict:
        temp = freq = energy = None
        try:
            with open(self.temp_path) as f:
                temp = int(f.read().strip()) / 1000.0
        except Exception:
            pass
        try:
            with open(self.freq_path) as f:
                freq = int(f.read().strip()) / 1000.0
        except Exception:
            pass
        if self.rapl_path:
            try:
                with open(self.rapl_path) as f:
                    energy = int(f.read().strip())
            except Exception:
                pass
        return {
            "t_wall": time.monotonic(),
            "temp_c": temp,
            "freq_mhz": freq,
            "throttled": None,
            "energy_uj": energy,
        }


# --------------------------------------------------------------------------
# NVIDIA GPU via NVML (works on Colab for *read-only* telemetry)
# --------------------------------------------------------------------------
class NvidiaBackend(SensorBackend):
    name = "nvidia"

    def __init__(self):
        self.h = None
        self.nvml = None
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            self.nvml = pynvml
            self.h = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            self.nvml = None
        self.caps = {
            "temp": self.nvml is not None,
            "freq": self.nvml is not None,
            "throttle_flags": self.nvml is not None,
            "energy": self.nvml is not None,  # power -> integrate
        }

    def available(self) -> bool:
        return self.nvml is not None and self.h is not None

    def read(self) -> dict:
        n, h = self.nvml, self.h
        temp = freq = thr = pwr = None
        try:
            temp = n.nvmlDeviceGetTemperature(h, n.NVML_TEMPERATURE_GPU)
        except Exception:
            pass
        try:
            freq = n.nvmlDeviceGetClockInfo(h, n.NVML_CLOCK_SM)
        except Exception:
            pass
        try:
            thr = n.nvmlDeviceGetCurrentClocksThrottleReasons(h)
        except Exception:
            pass
        try:
            pwr = n.nvmlDeviceGetPowerUsage(h)  # milliwatts
        except Exception:
            pass
        return {
            "t_wall": time.monotonic(),
            "temp_c": float(temp) if temp is not None else None,
            "freq_mhz": float(freq) if freq is not None else None,
            "throttled": int(thr) if thr is not None else None,
            "energy_uj": None,
            "power_mw": pwr,
        }


# --------------------------------------------------------------------------
# Mock: simulated first-order RC device with a throttle knee.
# For validating the analysis pipeline WITHOUT hardware. Never use for results.
# --------------------------------------------------------------------------
class MockBackend(SensorBackend):
    name = "mock"

    def __init__(self, t_amb=25.0, tau=45.0, r_th=0.45, knee=80.0,
                 hyst=3.0, f_max=1500.0, f_thr=1000.0):
        self.t_amb, self.tau, self.r_th = t_amb, tau, r_th
        self.knee, self.hyst = knee, hyst
        self.f_max, self.f_thr = f_max, f_thr
        self.temp = t_amb
        self.power = 0.0          # set by the workload via set_power()
        self.throttling = False
        self.last = time.monotonic()
        self.caps = {"temp": True, "freq": True,
                     "throttle_flags": True, "energy": False}

    def available(self) -> bool:
        return True

    def set_power(self, watts: float):
        self._advance()
        self.power = watts

    def _advance(self):
        now = time.monotonic()
        dt = max(0.0, now - self.last)
        self.last = now
        if dt <= 0:
            return
        t_inf = self.t_amb + self.r_th * self.power * 100.0
        self.temp = t_inf + (self.temp - t_inf) * math.exp(-dt / self.tau)
        # throttle with hysteresis
        if not self.throttling and self.temp >= self.knee:
            self.throttling = True
        elif self.throttling and self.temp <= self.knee - self.hyst:
            self.throttling = False

    def read(self) -> dict:
        self._advance()
        return {
            "t_wall": time.monotonic(),
            "temp_c": round(self.temp, 3),
            "freq_mhz": self.f_thr if self.throttling else self.f_max,
            "throttled": 4 if self.throttling else 0,
            "energy_uj": None,
        }


# --------------------------------------------------------------------------
# Windows CPU via LibreHardwareMonitor's WMI namespace.
#
# Windows exposes almost nothing useful natively:
#   - psutil.sensors_temperatures() returns {} on Windows (documented)
#   - MSAcpi_ThermalZoneTemperature is absent or fake on most laptops
#   - wmic CurrentClockSpeed reports a cached/base value, not real clock
#
# So we require LibreHardwareMonitor (free, open source) running AS ADMIN
# with its WMI provider enabled. It publishes real core temps and clocks to
# root\LibreHardwareMonitor.
#
# SETUP:
#   1. Download LibreHardwareMonitor, run LibreHardwareMonitor.exe as admin
#   2. Options -> "WMI Provider" (tick it), and leave the app running
#   3. pip install wmi pywin32
# --------------------------------------------------------------------------
class WindowsBackend(SensorBackend):
    name = "windows_lhm"

    def __init__(self):
        self.conn = None
        self.mode = None
        try:
            import wmi  # type: ignore
            self.conn = wmi.WMI(namespace="root\\LibreHardwareMonitor")
            self.conn.Sensor()          # probe the namespace
            self.mode = "wmi"
        except Exception:
            self.conn = None
        if self.conn is None and os.name == "nt":
            # PowerShell fallback: works, but each call costs ~200-400 ms,
            # so cap telemetry at ~2 Hz if you end up here.
            if shutil.which("powershell"):
                self.mode = "powershell"
        self.caps = {
            "temp": self.mode is not None,
            "freq": self.mode is not None,
            "throttle_flags": False,     # inferred from clock drop
            "energy": False,
        }

    def available(self) -> bool:
        return os.name == "nt" and self.mode is not None

    # ---- WMI path ----
    def _read_wmi(self):
        temp = freq = None
        temps, clocks = [], []
        for s in self.conn.Sensor():
            try:
                if s.SensorType == "Temperature" and "CPU" in (s.Name or ""):
                    temps.append(float(s.Value))
                elif s.SensorType == "Clock" and "CPU Core" in (s.Name or ""):
                    clocks.append(float(s.Value))
            except Exception:
                continue
        if temps:
            temp = max(temps)            # hottest core = the one that throttles
        if clocks:
            freq = max(clocks)
        return temp, freq

    # ---- PowerShell fallback ----
    def _read_ps(self):
        ps = (
            "$s = Get-CimInstance -Namespace root/LibreHardwareMonitor "
            "-ClassName Sensor -ErrorAction SilentlyContinue; "
            "$t = ($s | Where-Object {$_.SensorType -eq 'Temperature' -and "
            "$_.Name -like '*CPU*'} | Measure-Object Value -Maximum).Maximum; "
            "$c = ($s | Where-Object {$_.SensorType -eq 'Clock' -and "
            "$_.Name -like '*CPU Core*'} | Measure-Object Value -Maximum).Maximum; "
            "Write-Output \"$t,$c\""
        )
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True,
                                 timeout=5).stdout.strip()
            a, b = (out.split(",") + ["", ""])[:2]
            return (float(a) if a else None), (float(b) if b else None)
        except Exception:
            return None, None

    def read(self) -> dict:
        temp = freq = None
        try:
            if self.mode == "wmi":
                temp, freq = self._read_wmi()
            elif self.mode == "powershell":
                temp, freq = self._read_ps()
        except Exception:
            pass
        return {
            "t_wall": time.monotonic(),
            "temp_c": temp,
            "freq_mhz": freq,
            "throttled": None,
            "energy_uj": None,
        }


BACKENDS = [RaspberryPiBackend, LinuxCPUBackend, WindowsBackend, NvidiaBackend]


def get_backend(force: str | None = None, **mock_kw) -> SensorBackend:
    """Return the first working backend, or the named one.

    mock_kw is forwarded to MockBackend (tau, knee, t_amb, ...) so the
    analysis pipeline can be exercised in both pass and fail regimes.
    """
    if force == "mock":
        return MockBackend(**mock_kw)
    if force:
        for cls in BACKENDS + [MockBackend]:
            b = cls()
            if b.name == force:
                return b
        raise SystemExit(f"unknown backend: {force}")
    for cls in BACKENDS:
        try:
            b = cls()
            if b.available():
                return b
        except Exception:
            continue
    raise SystemExit(
        "No hardware sensor backend available.\n"
        "Run with --backend mock to validate the pipeline, but be aware "
        "mock data is SIMULATED and must never be reported as a result."
    )
