"""System stats for the monitor widget: CPU, RAM, GPU, VRAM, network, disk, uptime, pending
Windows updates, device batteries and (when LibreHardwareMonitor is running) temperatures."""
import ctypes
import re
import subprocess
import threading
import time
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

from .system import IS_WINDOWS, NO_WINDOW, ROOT_DISK, gpu_adapters, run_output

if IS_WINDOWS:
    import winreg

    import pywintypes
    import win32pdh


def fmt_bytes(n, per_second=False):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            text = f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
            return text + ("/s" if per_second else "")
        n /= 1024


def fmt_duration(seconds):
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    return f"{days} d {hours} h" if days else f"{hours} h {rest // 60} min"


def row(label, value, fraction=None, group=None, invert=False, action=None):
    """One line in the monitor. `group` is the Settings checkbox that shows/hides it; `invert`
    colours the bar for levels where low is bad (batteries); `action` makes the row clickable."""
    return {"label": label, "value": value, "fraction": fraction, "group": group or label, "invert": invert,
            "action": action}


class GpuCounters:
    """GPU load and VRAM use from Windows' own performance counters, the same numbers Task
    Manager shows. Works for any vendor without admin rights."""

    def __init__(self):
        adapters = gpu_adapters()
        best = max(adapters, key=lambda a: a[1]) if adapters else None  # the card with the most VRAM
        self.name, self.vram_total, self.luid = best or ("", 0, None)
        self.ok = False
        if not IS_WINDOWS:
            return
        try:
            self.query = win32pdh.OpenQuery()
            self.util = win32pdh.AddEnglishCounter(self.query, r"\GPU Engine(*)\Utilization Percentage")
            self.mem = win32pdh.AddEnglishCounter(self.query, r"\GPU Adapter Memory(*)\Dedicated Usage")
            win32pdh.CollectQueryData(self.query)
            self.ok = bool(self.luid)
        except pywintypes.error:
            self.ok = False

    def sample(self):
        """(load percent, VRAM bytes used), or (None, None)."""
        if not self.ok:
            return None, None
        try:
            win32pdh.CollectQueryData(self.query)
            util = win32pdh.GetFormattedCounterArray(self.util, win32pdh.PDH_FMT_DOUBLE)
            mem = win32pdh.GetFormattedCounterArray(self.mem, win32pdh.PDH_FMT_LARGE)
        except pywintypes.error:
            return None, None
        # Like Task Manager: add up each engine across processes, and report the busiest engine.
        engines = {}
        for inst, value in util.items():
            if self.luid in inst.lower():
                match = re.search(r"phys_(\d+)_eng_(\d+)", inst)
                key = match.groups() if match else inst
                engines[key] = engines.get(key, 0) + value
        load = min(100.0, max(engines.values(), default=0.0))
        used = sum(v for inst, v in mem.items() if self.luid in inst.lower())
        return load, used


class HardwareTemps:
    """CPU/GPU temperatures from LibreHardwareMonitor (or OpenHardwareMonitor) when it's running.
    Windows doesn't expose temperatures to normal apps, so without it there are none."""

    def __init__(self):
        self.wmi = None
        self.next_try = 0.0

    def sample(self):
        if not IS_WINDOWS:
            return linux_temperatures()
        if self.wmi is None:
            if time.monotonic() < self.next_try:
                return None, None
            self.next_try = time.monotonic() + 60
            import win32com.client
            for namespace in ("LibreHardwareMonitor", "OpenHardwareMonitor"):
                try:
                    self.wmi = win32com.client.GetObject(rf"winmgmts:root\{namespace}")
                    break
                except Exception:  # namespace missing: the program isn't running
                    continue
            if self.wmi is None:
                return None, None
        try:
            sensors = list(self.wmi.ExecQuery(
                "SELECT Name, Value, Identifier FROM Sensor WHERE SensorType='Temperature'"))
        except Exception:
            self.wmi = None
            return None, None
        cpu = gpu = None
        for s in sensors:
            ident, name = str(s.Identifier).lower(), str(s.Name).lower()
            if "cpu" in ident and (cpu is None or "package" in name or "tctl" in name):
                cpu = float(s.Value)
            elif "gpu" in ident and (gpu is None or "core" in name):
                gpu = float(s.Value)
        return cpu, gpu


def linux_temperatures():
    """(CPU, GPU) in °C from the kernel's sensors; either may be None."""
    sensors = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
    cpu = next((s.current for name in ("coretemp", "k10temp", "zenpower", "cpu_thermal") for s in sensors.get(name, [])),
               None)
    gpu = next((s.current for name in ("amdgpu", "nouveau", "radeon") for s in sensors.get(name, [])), None)
    return cpu, gpu


class Background:
    """Runs a slow check on a worker thread every `every` seconds; `value` holds the latest result."""

    def __init__(self, func, every, initial=None):
        self.func, self.every, self.value = func, every, initial
        self.next_run = 0.0
        self.busy = False

    def poll(self):
        if self.busy or time.monotonic() < self.next_run:
            return
        self.busy = True
        self.next_run = time.monotonic() + self.every

        def run():
            try:
                self.value = self.func()
            except Exception:
                pass
            finally:
                self.busy = False
        threading.Thread(target=run, daemon=True).start()


def pending_updates():
    """Number of Windows updates waiting to install, from Windows Update's last scan (no new scan)."""
    if not IS_WINDOWS:
        return sum("/" in line for line in run_output("apt", "list", "--upgradable", timeout=60).splitlines())
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        searcher = win32com.client.Dispatch("Microsoft.Update.Session").CreateUpdateSearcher()
        searcher.Online = False
        return searcher.Search("IsInstalled=0 and IsHidden=0 and Type='Software'").Updates.Count
    finally:
        pythoncom.CoUninitialize()


def restart_needed():
    if not IS_WINDOWS:
        return Path("/var/run/reboot-required").exists()
    for path in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
                 r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"):
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
            return True
        except OSError:
            continue
    return False


BLUETOOTH_BATTERY = r"""
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'BTH*' } | ForEach-Object {
  $p = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName '{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2' -ErrorAction SilentlyContinue
  if ($p -and $p.Data -ne $null) { "$($_.FriendlyName)|$($p.Data)" } }
"""


def bluetooth_batteries():
    """{device name: percent} for Bluetooth devices that report their battery to Windows."""
    if not IS_WINDOWS:
        return linux_device_batteries()
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", BLUETOOTH_BATTERY],
                         capture_output=True, text=True, timeout=90, creationflags=NO_WINDOW).stdout
    levels = {}
    for line in out.splitlines():
        name, _, value = line.rpartition("|")
        if name and value.strip().isdigit():
            # One headset shows up as several services ("... Hands-Free AG", "... Avrcp Transport").
            name = re.sub(r"\s+(Hands-Free( AG)?|Avrcp Transport|Stereo|A2DP.*|LE)$", "", name.strip())
            levels[name] = max(levels.get(name, 0), int(value))
    return levels


def linux_device_batteries():
    levels = {}
    for device in run_output("upower", "-e").splitlines():
        if "DisplayDevice" in device or "/battery_" in device or "/line_power" in device:
            continue  # the PC's own battery has its own row
        info = run_output("upower", "-i", device)
        model = re.search(r"model:\s*(.+)", info)
        percent = re.search(r"percentage:\s*(\d+)%", info)
        if model and percent:
            levels[model.group(1).strip()] = int(percent.group(1))
    return levels


def device_icon(name):
    n = name.lower()
    if "mouse" in n or "mx " in n:
        return "🖱"
    if "keyboard" in n or "keys" in n:
        return "⌨"
    if "controller" in n or "gamepad" in n:
        return "🎮"
    return "🎧"


class XINPUT_BATTERY(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte), ("BatteryLevel", ctypes.c_ubyte)]


def controller_batteries():
    """Xbox-style controllers: {name: approximate percent}. XInput only reports four levels."""
    if not IS_WINDOWS:
        return {}  # upower lists them with the other devices
    try:
        xinput = ctypes.windll.xinput1_4
    except OSError:
        return {}
    levels = {}
    for i in range(4):
        info = XINPUT_BATTERY()
        if xinput.XInputGetBatteryInformation(i, 0, ctypes.byref(info)) == 0 and info.BatteryType in (2, 3):
            levels[f"Controller {i + 1}"] = (5, 30, 65, 100)[min(info.BatteryLevel, 3)]
    return levels


class SystemStats(QObject):
    """Samples every 2 seconds while at least one monitor widget is open."""
    updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self, interval=2000, timeout=self.sample)
        self.gpu = None
        self.temps = HardwareTemps()
        self.updates = Background(pending_updates, 6 * 3600)
        self.bluetooth = Background(bluetooth_batteries, 10 * 60, {})
        self.rows = []
        self.ticks = 0
        self.controllers = {}

    def start(self):
        if self.timer.isActive():
            return
        if self.gpu is None:
            self.gpu = GpuCounters()
        psutil.cpu_percent(None)  # prime: the first call has nothing to compare against
        io = psutil.net_io_counters()
        self.net_last = (time.monotonic(), io.bytes_recv, io.bytes_sent)
        self.timer.start()
        QTimer.singleShot(500, self.sample)

    def stop(self):
        self.timer.stop()

    def sample(self):
        self.ticks += 1
        rows = []
        cpu_temp, gpu_temp = self.temps.sample()
        cpu = psutil.cpu_percent(None)
        rows.append(row("CPU", f"{cpu:.0f} %" + (f"   {cpu_temp:.0f} °C" if cpu_temp else ""), cpu / 100))
        vm = psutil.virtual_memory()
        rows.append(row("RAM", f"{fmt_bytes(vm.used)} / {fmt_bytes(vm.total)}", vm.percent / 100))
        load, vram = self.gpu.sample()
        if load is not None:
            rows.append(row("GPU", f"{load:.0f} %" + (f"   {gpu_temp:.0f} °C" if gpu_temp else ""), load / 100))
            if self.gpu.vram_total:
                rows.append(row("VRAM", f"{fmt_bytes(vram)} / {fmt_bytes(self.gpu.vram_total)}",
                                vram / self.gpu.vram_total))
        now, io = time.monotonic(), psutil.net_io_counters()
        then, recv, sent = self.net_last
        elapsed = max(now - then, 0.001)
        self.net_last = (now, io.bytes_recv, io.bytes_sent)
        rows.append(row("Network", f"↓ {fmt_bytes((io.bytes_recv - recv) / elapsed, True)}   "
                                   f"↑ {fmt_bytes((io.bytes_sent - sent) / elapsed, True)}"))
        disk = psutil.disk_usage(ROOT_DISK)
        rows.append(row("Disk C:" if IS_WINDOWS else "Disk", f"{fmt_bytes(disk.free)} free", disk.percent / 100,
                        group="Disk C:"))

        rows.append(row("Uptime", fmt_duration(time.time() - psutil.boot_time())))
        self.updates.poll()
        if restart_needed():
            updates = "Restart needed"
        elif self.updates.value is None:
            updates = "Checking…"
        else:
            updates = "Up to date" if self.updates.value == 0 else f"{self.updates.value} waiting"
        if IS_WINDOWS:
            rows.append(row("Updates", updates + "  ›", action="ms-settings:windowsupdate"))
        else:
            rows.append(row("Updates", updates))

        battery = psutil.sensors_battery()
        if battery:
            rows.append(row("🔋 Battery", f"{battery.percent:.0f} %" + (" ⚡" if battery.power_plugged else ""),
                            battery.percent / 100, group="Devices", invert=True))
        self.bluetooth.poll()
        if self.ticks % 15 == 1:
            self.controllers = controller_batteries()
        for name, level in sorted(self.bluetooth.value.items()):
            rows.append(row(f"{device_icon(name)} {name}", f"{level} %", level / 100, group="Devices", invert=True))
        for name, level in self.controllers.items():
            rows.append(row(f"🎮 {name}", f"~{level} %", level / 100, group="Devices", invert=True))
        self.rows = rows
        self.updated.emit()
