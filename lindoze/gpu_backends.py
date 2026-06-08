"""GPU backend abstraction. Detects all GPUs on the system and provides a
uniform sample() interface per device.

Supported vendors:
- NVIDIA via NVML (pynvml)
- AMD via sysfs (/sys/class/drm/cardN/device/...)
- Intel (experimental, i915 driver) via sysfs/hwmon for static stats and the
  i915 perf PMU for live engine utilization. Xe-driver GPUs degrade to
  temp/freq only until a v0.3 tester reports back.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
from pathlib import Path
from typing import Optional


_SAMPLE_KEYS = (
    "name", "util", "mem_used", "mem_total", "temp", "power",
    "clk_core", "clk_mem", "enc", "dec",
)


def _read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


# ---- NVIDIA backend

try:
    import pynvml
    pynvml.nvmlInit()
    _NVML_OK = True
except Exception:
    _NVML_OK = False


class NVIDIABackend:
    vendor = "nvidia"

    def __init__(self, index: int) -> None:
        self.index = index
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(index)
        name = pynvml.nvmlDeviceGetName(self._handle)
        self.name = name.decode() if isinstance(name, bytes) else name

    def sample(self) -> dict:
        h = self._handle
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
        except pynvml.NVMLError:
            util = 0
        try:
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            mem_used, mem_total = mem.used, mem.total
        except pynvml.NVMLError:
            mem_used = mem_total = 0
        try:
            temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
        except pynvml.NVMLError:
            temp = None
        try:
            power = pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0
        except pynvml.NVMLError:
            power = None
        try:
            clk_core = pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_GRAPHICS)
            clk_mem = pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_MEM)
        except pynvml.NVMLError:
            clk_core = clk_mem = None
        try:
            enc = pynvml.nvmlDeviceGetEncoderUtilization(h)[0]
            dec = pynvml.nvmlDeviceGetDecoderUtilization(h)[0]
        except pynvml.NVMLError:
            enc = dec = None
        return dict(
            name=self.name, util=util, mem_used=mem_used, mem_total=mem_total,
            temp=temp, power=power, clk_core=clk_core, clk_mem=clk_mem,
            enc=enc, dec=dec,
        )


# ---- AMD backend (sysfs)

def _lspci_name(pci_vendor: str, pci_device: str) -> str:
    """Friendly name from lspci output, e.g. 'Raphael' for 1002:164e."""
    try:
        out = subprocess.check_output(
            ["lspci", "-d", f"{pci_vendor.removeprefix('0x')}:{pci_device.removeprefix('0x')}"],
            text=True, timeout=2,
        )
        # "04:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Raphael (rev d8)"
        if ":" in out:
            after_class = out.split(":", 2)[-1].strip()
            # Drop "(rev ..)" tail
            if "(" in after_class:
                after_class = after_class[: after_class.rindex("(")].strip()
            # Strip the leading "Advanced Micro Devices, Inc. [AMD/ATI] " noise
            for prefix in ("Advanced Micro Devices, Inc. [AMD/ATI] ",
                           "Advanced Micro Devices, Inc. ",
                           "[AMD/ATI] "):
                if after_class.startswith(prefix):
                    after_class = after_class[len(prefix):]
                    break
            return f"AMD {after_class}"
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        pass
    return "AMD GPU"


class AMDBackend:
    vendor = "amd"

    def __init__(self, card_path: Path) -> None:
        self.card_path = card_path
        self.device_path = card_path / "device"
        vendor = _read_text(self.device_path / "vendor") or ""
        device = _read_text(self.device_path / "device") or ""
        self.name = _lspci_name(vendor, device)
        hwmons = list((self.device_path / "hwmon").glob("hwmon*"))
        self.hwmon = hwmons[0] if hwmons else None

    def sample(self) -> dict:
        util = _read_int(self.device_path / "gpu_busy_percent") or 0
        mem_total = _read_int(self.device_path / "mem_info_vram_total") or 0
        mem_used = _read_int(self.device_path / "mem_info_vram_used") or 0
        # AMD's vcn_busy_percent covers both encode and decode together —
        # report it under both fields since the GPU page treats them separately.
        vcn = _read_int(self.device_path / "vcn_busy_percent")

        temp = power = clk_core = None
        if self.hwmon is not None:
            t = _read_int(self.hwmon / "temp1_input")
            if t is not None:
                temp = t // 1000  # milli-°C to °C
            # Some amdgpu builds expose power1_average, others only power1_input.
            for power_field in ("power1_average", "power1_input"):
                p = _read_int(self.hwmon / power_field)
                if p is not None:
                    power = p / 1_000_000  # microwatts to watts
                    break
            f = _read_int(self.hwmon / "freq1_input")
            if f is not None:
                clk_core = f // 1_000_000  # Hz to MHz

        # AMD APUs share a power budget across the whole socket — the hwmon
        # power reading covers CPU+iGPU+uncore, not just the iGPU. Modern
        # discrete AMD cards all ship with >=8GB VRAM, so <4GB total reliably
        # identifies an integrated GPU for our labeling purposes.
        is_integrated = mem_total > 0 and mem_total < 4 * 1024**3
        return dict(
            name=self.name, util=util, mem_used=mem_used, mem_total=mem_total,
            temp=temp, power=power, clk_core=clk_core, clk_mem=None,
            enc=vcn, dec=vcn, is_integrated=is_integrated,
        )


# ---- Intel backend (sysfs + hwmon + i915 perf PMU)
#
# i915 doesn't expose AMD-style aggregate gpu_busy_percent. For live util we
# open the i915 perf PMU (one fd per engine) via perf_event_open(). The PMU
# returns a monotonically-increasing busy-ns counter per engine; sampling the
# delta against wall time gives a per-engine busy %. We average across engines
# for an overall figure, matching how intel_gpu_top labels "overall".
#
# Static stats (temp/power/freq) come from sysfs+hwmon and work even if the
# PMU path fails (sandboxed env, perf_event_paranoid sysctl, etc.). In that
# case util reports None and the page draws "—".

# x86_64 syscall number for perf_event_open. ARM64 is 241; we'd add a lookup
# table when someone runs Lindoze on aarch64 Intel hw (basically never).
_SYS_PERF_EVENT_OPEN = 298


class _PerfEventAttr(ctypes.Structure):
    # Subset of struct perf_event_attr — we only need to set type/size/config.
    # The trailing pad reaches PERF_ATTR_SIZE_VER7 (128 bytes) so the kernel
    # accepts the size field across reasonably current kernels.
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("config", ctypes.c_uint64),
        ("sample_period_or_freq", ctypes.c_uint64),
        ("sample_type", ctypes.c_uint64),
        ("read_format", ctypes.c_uint64),
        ("flags", ctypes.c_uint64),
        ("_pad", ctypes.c_uint8 * 96),
    ]


def _open_perf_event(pmu_type: int, config: int, cpu: int) -> Optional[int]:
    """perf_event_open for a single i915 PMU event. Returns fd or None."""
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    attr = _PerfEventAttr()
    attr.type = pmu_type
    attr.size = ctypes.sizeof(_PerfEventAttr)
    attr.config = config
    # pid=-1, cpu=N → system-wide counter on that CPU. Group fd=-1, flags=0.
    fd = libc.syscall(
        ctypes.c_long(_SYS_PERF_EVENT_OPEN),
        ctypes.byref(attr), ctypes.c_int(-1), ctypes.c_int(cpu),
        ctypes.c_int(-1), ctypes.c_ulong(0),
    )
    return fd if fd >= 0 else None


def _read_pmu_counter(fd: int) -> Optional[int]:
    try:
        data = os.read(fd, 8)
        if len(data) != 8:
            return None
        return int.from_bytes(data, "little", signed=False)
    except OSError:
        return None


def _lspci_name_intel(pci_vendor: str, pci_device: str) -> str:
    # Same shape as _lspci_name but stripping Intel marketing prefixes.
    try:
        out = subprocess.check_output(
            ["lspci", "-d", f"{pci_vendor.removeprefix('0x')}:{pci_device.removeprefix('0x')}"],
            text=True, timeout=2,
        )
        if ":" in out:
            after_class = out.split(":", 2)[-1].strip()
            if "(" in after_class:
                after_class = after_class[: after_class.rindex("(")].strip()
            for prefix in ("Intel Corporation ", "Intel Corp. ", "Intel "):
                if after_class.startswith(prefix):
                    after_class = after_class[len(prefix):]
                    break
            return f"Intel {after_class}"
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        pass
    return "Intel GPU"


class IntelBackend:
    vendor = "intel"

    def __init__(self, card_path: Path) -> None:
        self.card_path = card_path
        self.device_path = card_path / "device"
        vendor = _read_text(self.device_path / "vendor") or ""
        device = _read_text(self.device_path / "device") or ""
        self.name = _lspci_name_intel(vendor, device)

        # hwmon: i915 driver registers a hwmon device under device/hwmon/.
        hwmons = list((self.device_path / "hwmon").glob("hwmon*")) \
            if (self.device_path / "hwmon").exists() else []
        self.hwmon = hwmons[0] if hwmons else None

        # i915 frequency sysfs nodes (mhz). Modern kernels expose these under
        # card_path/ directly; older Gen7 (Ivy Bridge) instead places them at
        # device_path/gt_*_freq_mhz. Xe uses different paths again; if none of
        # the candidates exist we report clk_core=None.
        self._freq_cur = self._pick_existing(
            self.card_path / "gt_cur_freq_mhz",
            self.device_path / "gt_cur_freq_mhz",
        )
        self._freq_max = self._pick_existing(
            self.card_path / "gt_max_freq_mhz",
            self.device_path / "gt_max_freq_mhz",
        )

        # PMU setup — best-effort. Any failure → no util reading.
        self._pmu_fds: list[int] = []
        self._pmu_prev_ns: list[int] = []
        self._pmu_prev_wall: Optional[int] = None
        self._setup_pmu()

    def _setup_pmu(self) -> None:
        pmu_dir = Path("/sys/bus/event_source/devices/i915")
        if not pmu_dir.exists():
            return
        pmu_type = _read_int(pmu_dir / "type")
        if pmu_type is None:
            return
        # Pick a CPU from the PMU's cpumask (i915 PMU is bound to one CPU).
        cpumask_text = _read_text(pmu_dir / "cpumask") or "0"
        try:
            cpu = int(cpumask_text.split("-")[0].split(",")[0])
        except ValueError:
            cpu = 0
        # Each events/*-busy file holds e.g. "event=0x0" or "event=0x1000".
        events_dir = pmu_dir / "events"
        if not events_dir.exists():
            return
        for ev_file in sorted(events_dir.glob("*-busy")):
            config = self._parse_event_config(ev_file)
            if config is None:
                continue
            fd = _open_perf_event(pmu_type, config, cpu)
            if fd is None:
                continue
            self._pmu_fds.append(fd)
            self._pmu_prev_ns.append(0)
        # Prime initial reads so the first sample() reports a real delta.
        if self._pmu_fds:
            for i, fd in enumerate(self._pmu_fds):
                v = _read_pmu_counter(fd)
                if v is not None:
                    self._pmu_prev_ns[i] = v
            self._pmu_prev_wall = self._monotonic_ns()

    @staticmethod
    def _pick_existing(*candidates: Path) -> Optional[Path]:
        for p in candidates:
            if p.exists():
                return p
        return None

    @staticmethod
    def _parse_event_config(ev_file: Path) -> Optional[int]:
        text = _read_text(ev_file)
        if not text:
            return None
        # Format is "event=0xN" or "event=0xN,umask=..." — we only need event=.
        for part in text.split(","):
            kv = part.strip().split("=", 1)
            if len(kv) == 2 and kv[0] == "event":
                try:
                    return int(kv[1], 0)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _monotonic_ns() -> int:
        # time.monotonic_ns wall-clock proxy; matches PMU's CLOCK_MONOTONIC.
        import time
        return time.monotonic_ns()

    def _sample_util(self) -> Optional[int]:
        if not self._pmu_fds or self._pmu_prev_wall is None:
            return None
        now_wall = self._monotonic_ns()
        elapsed = now_wall - self._pmu_prev_wall
        if elapsed <= 0:
            return None
        busy_sum = 0
        n = 0
        for i, fd in enumerate(self._pmu_fds):
            v = _read_pmu_counter(fd)
            if v is None:
                continue
            delta = v - self._pmu_prev_ns[i]
            self._pmu_prev_ns[i] = v
            if delta < 0:
                delta = 0  # counter rolled or reset
            busy_sum += delta
            n += 1
        self._pmu_prev_wall = now_wall
        if n == 0:
            return None
        # Average engine busy % across the engines we managed to open.
        avg = (busy_sum * 100) // (elapsed * n)
        return max(0, min(100, int(avg)))

    def sample(self) -> dict:
        util = self._sample_util()

        temp = power = None
        if self.hwmon is not None:
            t = _read_int(self.hwmon / "temp1_input")
            if t is not None:
                temp = t // 1000
            # i915 hwmon publishes energy1_input (microjoules cumulative), not
            # instantaneous power. We'd need to delta it; for v0.2 we just
            # check for a direct power1_average if it exists (some platforms).
            p = _read_int(self.hwmon / "power1_average")
            if p is not None:
                power = p / 1_000_000

        clk_core = _read_int(self._freq_cur) if self._freq_cur else None

        # Intel iGPUs share system RAM; no dedicated VRAM sysfs surface
        # without debugfs/root. Mark as integrated so the page labels power
        # consistently with AMD APUs.
        return dict(
            name=self.name, util=util, mem_used=0, mem_total=0,
            temp=temp, power=power, clk_core=clk_core, clk_mem=None,
            enc=None, dec=None, is_integrated=True,
        )


# ---- Detection

def _detect_intel() -> list[IntelBackend]:
    backends: list[IntelBackend] = []
    drm = Path("/sys/class/drm")
    if not drm.exists():
        return backends
    for card in sorted(drm.glob("card*")):
        if "-" in card.name:
            continue
        if _read_text(card / "device" / "vendor") != "0x8086":
            continue
        driver_link = card / "device" / "driver"
        driver = driver_link.resolve().name if driver_link.exists() else ""
        if driver not in ("i915", "xe"):
            continue
        backends.append(IntelBackend(card))
    return backends


def _detect_amd() -> list[AMDBackend]:
    backends: list[AMDBackend] = []
    drm = Path("/sys/class/drm")
    if not drm.exists():
        return backends
    for card in sorted(drm.glob("card*")):
        if "-" in card.name:  # skip card0-DP-1, etc. (connectors)
            continue
        vendor = _read_text(card / "device" / "vendor")
        if vendor == "0x1002":
            # Make sure it has the bare minimum sysfs surface we need.
            if (card / "device" / "gpu_busy_percent").exists():
                backends.append(AMDBackend(card))
    return backends


def _detect_nvidia() -> list[NVIDIABackend]:
    if not _NVML_OK:
        return []
    try:
        n = pynvml.nvmlDeviceGetCount()
    except Exception:
        return []
    return [NVIDIABackend(i) for i in range(n)]


def detect_gpus() -> list:
    """All detected GPUs across vendors. Order is stable across runs (NVIDIA
    by NVML index, AMD by sysfs cardN order)."""
    return _detect_nvidia() + _detect_amd() + _detect_intel()


# ---- Diagnostics

def dump_gpus(stream=None) -> None:
    """Verbose GPU-detection dump for bug reports. Writes raw sysfs/PMU state
    so a user can paste a single block of output and have everything we need
    to debug an Intel/AMD/NVIDIA detection or sampling failure."""
    import sys
    import time
    if stream is None:
        stream = sys.stderr

    def p(msg: str = "") -> None:
        print(msg, file=stream)

    p("=== Lindoze GPU diagnostic dump ===")
    drm = Path("/sys/class/drm")
    p(f"/sys/class/drm exists: {drm.exists()}")
    if drm.exists():
        for card in sorted(drm.glob("card*")):
            if "-" in card.name:
                continue
            vendor = _read_text(card / "device" / "vendor")
            device = _read_text(card / "device" / "device")
            driver_link = card / "device" / "driver"
            driver = driver_link.resolve().name if driver_link.exists() else "(none)"
            p(f"  {card.name}: vendor={vendor} device={device} driver={driver}")

    pmu_dir = Path("/sys/bus/event_source/devices/i915")
    p(f"\ni915 PMU dir exists: {pmu_dir.exists()}")
    if pmu_dir.exists():
        p(f"  type      = {_read_text(pmu_dir / 'type')}")
        p(f"  cpumask   = {_read_text(pmu_dir / 'cpumask')}")
        events_dir = pmu_dir / "events"
        if events_dir.exists():
            for ev in sorted(events_dir.glob("*")):
                p(f"  events/{ev.name} = {_read_text(ev)}")

    paranoid = _read_text(Path("/proc/sys/kernel/perf_event_paranoid"))
    p(f"\nperf_event_paranoid = {paranoid}")

    backends = detect_gpus()
    p(f"\nDetected backends: {len(backends)}")
    for b in backends:
        p(f"\n--- {b.vendor}: {b.name}")
        if isinstance(b, IntelBackend):
            p(f"  card_path   = {b.card_path}")
            p(f"  device_path = {b.device_path}")
            p(f"  freq_cur    = {b._freq_cur}  ->  {_read_int(b._freq_cur) if b._freq_cur else None}")
            p(f"  freq_max    = {b._freq_max}  ->  {_read_int(b._freq_max) if b._freq_max else None}")
            p(f"  hwmon       = {b.hwmon}")
            p(f"  pmu_fds     = {b._pmu_fds}")
            p(f"  pmu_prev_ns = {b._pmu_prev_ns}")
            # Take two samples ~250ms apart so the user sees a real util read.
            s1 = b.sample()
            time.sleep(0.25)
            s2 = b.sample()
            p(f"  sample[0]   = {s1}")
            p(f"  sample[1]   = {s2}")
        else:
            p(f"  sample      = {b.sample()}")
    p("\n=== end dump ===")
