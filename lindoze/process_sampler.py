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


# Fetched every tick — dynamic values plus the cheap fields that all come from
# a single /proc/<pid>/stat read. exe, username, and cmdline are deliberately
# absent: they're immutable per process and the priciest to read (separate
# /proc files + uid->name lookup), so they're cached per-PID and read lazily
# (see _static / _need_detail) rather than re-read for every process each tick.
_ATTRS = [
    "pid", "ppid", "name", "memory_info",
    "num_threads", "status", "io_counters",
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
        # pid -> [username, exe|None, cmdline|None]. None means "not fetched
        # yet"; populated lazily once we've seen the PID (and, for exe/cmdline,
        # once the UI needs them). Pruned as PIDs exit.
        self._static: dict[int, list] = {}
        self._need_detail = False

    def start(self) -> None:
        self._timer.start()

    def set_detail_needed(self, needed: bool) -> None:
        """Gate the expensive exe/cmdline reads. The Processes page calls this
        when the Path/Command-line column is shown or a search is active."""
        self._need_detail = needed

    def refresh_now(self) -> None:
        """Sample immediately (only while actively sampling) so newly-enabled
        detail columns or searches populate without waiting for the next tick."""
        if self._timer.isActive():
            self._tick()

    @staticmethod
    def _read_username(p) -> str:
        try:
            u = p.username()
        except (psutil.Error, OSError):
            return "?"
        return u if isinstance(u, str) else "?"

    @staticmethod
    def _read_exe(p) -> str:
        try:
            exe = p.exe()
        except (psutil.Error, OSError):
            return ""
        return exe if isinstance(exe, str) else ""

    @staticmethod
    def _read_cmdline(p, name) -> str:
        try:
            cmd = p.cmdline()
        except (psutil.Error, OSError):
            cmd = None
        return " ".join(cmd) if cmd else (name or "")

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

                pid = info["pid"]
                entry = self._static.get(pid)
                if entry is None:
                    # First sighting of this PID: read the immutable username
                    # once. exe/cmdline stay deferred until something needs them.
                    entry = [self._read_username(p), None, None]
                    self._static[pid] = entry
                # exe + command line never change but are the priciest reads, so
                # fetch them only when the UI needs them, then cache for good.
                if self._need_detail and entry[1] is None:
                    entry[1] = self._read_exe(p)
                    entry[2] = self._read_cmdline(p, info.get("name"))
                username = entry[0]
                exe = entry[1] or ""
                cmdline = entry[2] or ""

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
        # Drop cache entries for exited PIDs so the dict tracks the live set
        # (and to limit staleness if the kernel reuses a PID number).
        if len(self._static) > len(snaps):
            for dead in self._static.keys() - snaps.keys():
                del self._static[dead]
        self.snapshot_ready.emit(snaps)
