from __future__ import annotations

import time
from dataclasses import dataclass, field

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

from .gpu_backends import detect_gpus

_GPUS = detect_gpus()


def _safe_gpu_sample(g) -> dict:
    """A failing GPU backend (driver hiccup, subprocess error) must not take down
    the whole sampler tick. Degrade to an empty dict; GPUPage skips empty samples."""
    try:
        return g.sample()
    except Exception:
        return {}


@dataclass
class Sample:
    ts: float
    cpu_total: float
    cpu_per: list[float]
    cpu_freq_per: list[float]
    mem_used: int
    mem_total: int
    mem_available: int
    mem_cached: int
    mem_buffers: int
    swap_used: int
    swap_total: int
    disk_read_bps: dict[str, float]
    disk_write_bps: dict[str, float]
    net_rx_bps: dict[str, float]
    net_tx_bps: dict[str, float]
    proc_count: int
    thread_count: int
    gpus: list[dict] = field(default_factory=list)


def _meminfo() -> dict[str, int]:
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, _, v = line.partition(":")
            parts = v.strip().split()
            if parts:
                out[k] = int(parts[0]) * 1024
    return out


def gpu_count() -> int:
    return len(_GPUS)


def gpu_names() -> list[str]:
    return [g.name for g in _GPUS]


def gpu_available() -> bool:
    return bool(_GPUS)


def cpu_model() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "Unknown CPU"


class Sampler(QObject):
    sample_ready = Signal(object)  # Sample

    def __init__(self, interval_ms: int = 1000, parent=None) -> None:
        super().__init__(parent)
        self._interval = interval_ms
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

        psutil.cpu_percent(percpu=True)  # prime
        self._prev_disk = psutil.disk_io_counters(perdisk=True)
        self._prev_net = psutil.net_io_counters(pernic=True)
        self._prev_ts = time.monotonic()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = max(now - self._prev_ts, 1e-6)
        self._prev_ts = now

        per = psutil.cpu_percent(percpu=True)
        total = sum(per) / len(per) if per else 0.0
        try:
            freqs = psutil.cpu_freq(percpu=True) or []
            freq_per = [f.current for f in freqs]
        except Exception:
            freq_per = []

        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        mi = _meminfo()

        cur_disk = psutil.disk_io_counters(perdisk=True)
        cur_net = psutil.net_io_counters(pernic=True)
        d_read, d_write = {}, {}
        for name, c in cur_disk.items():
            p = self._prev_disk.get(name)
            if p:
                d_read[name] = (c.read_bytes - p.read_bytes) / dt
                d_write[name] = (c.write_bytes - p.write_bytes) / dt
        n_rx, n_tx = {}, {}
        for name, c in cur_net.items():
            p = self._prev_net.get(name)
            if p:
                n_rx[name] = (c.bytes_recv - p.bytes_recv) / dt
                n_tx[name] = (c.bytes_sent - p.bytes_sent) / dt
        self._prev_disk = cur_disk
        self._prev_net = cur_net

        # Cheap thread/proc count from /proc/loadavg's 4th field, "running/total".
        # Total includes kernel threads. Avoids the previous per-PID Process()
        # creation loop, which was the single biggest cost in the sampler.
        try:
            with open("/proc/loadavg") as f:
                _, _, _, tasks_field, _ = f.read().split()
            _, total_threads_str = tasks_field.split("/")
            thread_count = int(total_threads_str)
        except (OSError, ValueError):
            thread_count = 0
        pids = psutil.pids()

        sample = Sample(
            ts=now,
            cpu_total=total,
            cpu_per=per,
            cpu_freq_per=freq_per,
            mem_used=vm.used,
            mem_total=vm.total,
            mem_available=vm.available,
            mem_cached=mi.get("Cached", 0),
            mem_buffers=mi.get("Buffers", 0),
            swap_used=sw.used,
            swap_total=sw.total,
            disk_read_bps=d_read,
            disk_write_bps=d_write,
            net_rx_bps=n_rx,
            net_tx_bps=n_tx,
            proc_count=len(pids),
            thread_count=thread_count,
            gpus=[_safe_gpu_sample(g) for g in _GPUS],
        )
        self.sample_ready.emit(sample)
