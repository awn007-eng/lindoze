"""GPU backend abstraction. Detects all GPUs on the system and provides a
uniform sample() interface per device.

Supported vendors:
- NVIDIA via NVML (pynvml)
- AMD via sysfs (/sys/class/drm/cardN/device/...)

Intel is intentionally out of scope for v1 — its integrated-GPU sysfs surface
is sparse and the live-util counters require shelling out to intel_gpu_top.
"""
from __future__ import annotations

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


# ---- Detection

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
    return _detect_nvidia() + _detect_amd()
