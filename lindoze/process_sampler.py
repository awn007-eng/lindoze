"""Per-process sampler. Separate from the system Sampler because:
- per-process iteration is heavier (~500+ procs) and might want a different
  cadence later;
- only the Processes page consumes it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import psutil
from PySide6.QtCore import QObject, QTimer, Signal


@dataclass(slots=True)
class ProcSnap:
    pid: int
    ppid: int
    name: str
    exe: str
    cmdline: str
    user: str
    cpu_pct: float
    mem_rss: int
    disk_bps: float
    status: str
    threads: int


_ATTRS = [
    "pid", "ppid", "name", "exe", "username", "memory_info",
    "num_threads", "status", "cmdline", "io_counters",
]


class ProcessSampler(QObject):
    snapshot_ready = Signal(object)  # dict[int, ProcSnap]; Signal(dict) won't marshal through PySide

    def __init__(self, interval_ms: int = 1500, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)
        # Prime cpu_percent so the first real call returns a meaningful delta.
        for p in psutil.process_iter(["pid"]):
            try:
                p.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self._prev_io: dict[int, tuple[int, int, float]] = {}

    def start(self) -> None:
        self._timer.start()

    def set_active(self, active: bool) -> None:
        """Pause sampling when the Processes tab isn't visible — there's no
        point scanning 500 processes per second to update a hidden view."""
        if active:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        now = time.monotonic()
        snaps: dict[int, ProcSnap] = {}
        new_io: dict[int, tuple[int, int, float]] = {}

        for p in psutil.process_iter(_ATTRS):
            try:
                info = p.info
                try:
                    cpu = p.cpu_percent(None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

                io = info.get("io_counters")
                # process_iter stores the exception object as the value when
                # access is denied — guard accordingly.
                if io is None or not hasattr(io, "read_bytes"):
                    disk_bps = 0.0
                else:
                    new_io[info["pid"]] = (io.read_bytes, io.write_bytes, now)
                    prev = self._prev_io.get(info["pid"])
                    if prev:
                        dt = max(now - prev[2], 1e-6)
                        disk_bps = (
                            (io.read_bytes - prev[0]) + (io.write_bytes - prev[1])
                        ) / dt
                    else:
                        disk_bps = 0.0

                mem = info.get("memory_info")
                rss = mem.rss if mem and hasattr(mem, "rss") else 0
                cmd = info.get("cmdline") or []
                cmdline = " ".join(cmd) if cmd else (info.get("name") or "")
                # process_iter stores the exception object as the value on
                # AccessDenied — keep only real string paths.
                exe = info.get("exe")
                exe = exe if isinstance(exe, str) else ""
                username = info.get("username") or "?"
                if not isinstance(username, str):  # AccessDenied marker
                    username = "?"

                snaps[info["pid"]] = ProcSnap(
                    pid=info["pid"],
                    ppid=info.get("ppid") or 0,
                    name=info.get("name") or "?",
                    exe=exe,
                    cmdline=cmdline,
                    user=username,
                    cpu_pct=cpu,
                    mem_rss=rss,
                    disk_bps=disk_bps,
                    status=info.get("status") or "?",
                    threads=info.get("num_threads") or 0,
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self._prev_io = new_io
        self.snapshot_ready.emit(snaps)
