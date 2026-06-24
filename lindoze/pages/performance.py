"""Performance "tab" — holds the per-resource sidebar with live-graph rows and
the right-side stack of detail pages (CPU/Memory/GPU/Disk/Network).

Lifted out of MainWindow when the top-level navigation was promoted to a
Win11-style outer sidebar (Processes / Performance / …).
"""
from __future__ import annotations

import os

import psutil
from PySide6.QtCore import QSettings, QSize
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..graphs import MiniGraph
from ..sampler import gpu_names
from ..styles import PILL_BUTTON_QSS
from .cpu import CPUPage, CPU_ACCENT
from .gpu import GPUPage, GPU_ACCENT
from .io import DiskPage, NetPage, DISK_ACCENT, NET_ACCENT
from .memory import MemoryPage, MEM_ACCENT


SIDEBAR_DARK = """
QListWidget {
    background: #181818;
    border: none;
    outline: 0;
}
QListWidget::item { border: 0; padding: 0; }
QListWidget::item:selected { background: #2a2a2a; }
QListWidget::item:hover { background: #232323; }
"""


def _bps(n: float) -> str:
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024; i += 1
    return f"{n:.1f} {units[i]}"


class SidebarRow(QWidget):
    def __init__(self, name: str, accent: QColor, fmt=lambda v: f"{v:.0f}%") -> None:
        super().__init__()
        self._fmt = fmt
        self.graph = MiniGraph(y_max=100.0, accent=accent, compact=True, show_grid=False, label="")
        self.graph.setFixedSize(54, 36)
        self.name_lbl = QLabel(name)
        nf = QFont(); nf.setPointSize(10); nf.setBold(True)
        self.name_lbl.setFont(nf)
        self.value_lbl = QLabel("—")
        self.value_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(0)
        text_box.addWidget(self.name_lbl)
        text_box.addWidget(self.value_lbl)
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.addWidget(self.graph)
        root.addLayout(text_box, stretch=1)

    def set_value(self, v: float, label: str | None = None) -> None:
        self.graph.push(v)
        self.value_lbl.setText(label if label is not None else self._fmt(v))


class PerformancePage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        n_threads = psutil.cpu_count(logical=True) or 1
        block_devices = set(os.listdir("/sys/block"))
        self._disks = sorted(
            d for d in psutil.disk_io_counters(perdisk=True).keys()
            if d in block_devices
            and not d.startswith(("loop", "ram", "zram", "dm-"))
        )
        stats = psutil.net_if_stats()
        self._ifaces = sorted(n for n, st in stats.items() if n != "lo" and st.isup)

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet(SIDEBAR_DARK)
        self.sidebar.setIconSize(QSize(0, 0))

        self.stack = QStackedWidget()
        self.cpu_page = CPUPage(n_threads)
        self.mem_page = MemoryPage()
        self._gpu_names = gpu_names()
        self.gpu_pages = [
            GPUPage(index=i, title_text=f"GPU {i}") for i in range(len(self._gpu_names))
        ]
        self._disk_pages = [DiskPage(d) for d in self._disks]
        self._net_pages = [NetPage(i) for i in self._ifaces]
        self._rows: list[tuple[SidebarRow, QWidget]] = []

        self._add_entry(SidebarRow("CPU", CPU_ACCENT), self.cpu_page)
        self._add_entry(SidebarRow("Memory", MEM_ACCENT), self.mem_page)
        for i, page in enumerate(self.gpu_pages):
            self._add_entry(SidebarRow(f"GPU {i}", GPU_ACCENT), page)
        for d, page in zip(self._disks, self._disk_pages):
            row = SidebarRow(f"Disk — {d}", DISK_ACCENT, fmt=_bps)
            row.graph._y_max_fixed = None
            self._add_entry(row, page)
        for i, page in zip(self._ifaces, self._net_pages):
            row = SidebarRow(f"Network — {i}", NET_ACCENT, fmt=_bps)
            row.graph._y_max_fixed = None
            self._add_entry(row, page)

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        # Time-scale toolbar — top of the right pane. Applies globally to all
        # detail-page graphs; sidebar mini-graphs stay at 60s (they're 54px
        # wide, larger scales just produce noise).
        self._scale_buttons: list[tuple[int, QPushButton]] = []
        scale_bar = QHBoxLayout()
        scale_bar.setContentsMargins(8, 6, 8, 6)
        scale_bar.setSpacing(4)
        scale_bar.addStretch(1)
        time_lbl = QLabel("Time")
        time_lbl.setStyleSheet("color: #888; padding-right: 6px;")
        scale_bar.addWidget(time_lbl)
        for label_text, seconds in [("60s", 60), ("10min", 600), ("1hr", 3600)]:
            btn = QPushButton(label_text)
            btn.setCheckable(True)
            btn.setFixedWidth(56)
            btn.setStyleSheet(PILL_BUTTON_QSS + " QPushButton { padding: 3px 6px; }")
            btn.clicked.connect(lambda _c=False, s=seconds: self.set_time_scale(s))
            self._scale_buttons.append((seconds, btn))
            scale_bar.addWidget(btn)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addLayout(scale_bar)
        right_layout.addWidget(self.stack, stretch=1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(right, stretch=1)

        self._settings = QSettings()
        saved = self._settings.value("performance/time_scale", 60, type=int)
        if saved not in (60, 600, 3600):
            saved = 60
        self.set_time_scale(saved)

    def set_time_scale(self, seconds: int) -> None:
        for g in self.stack.findChildren(MiniGraph):
            g.set_display_len(seconds)
        for sec, btn in self._scale_buttons:
            btn.setChecked(sec == seconds)
        self._settings.setValue("performance/time_scale", seconds)

    def _add_entry(self, row: SidebarRow, page: QWidget) -> None:
        item = QListWidgetItem()
        item.setSizeHint(QSize(220, 52))
        self.sidebar.addItem(item)
        self.sidebar.setItemWidget(item, row)
        self.stack.addWidget(page)
        self._rows.append((row, page))

    def on_sample(self, s) -> None:
        # Skip the whole fan-out when the Performance tab isn't the active
        # outer tab — Qt's isVisible() returns False for non-current children
        # of a QStackedWidget. Saves ~40 MiniGraph.push() + repaint schedules
        # per tick while the user is on Processes or Startup.
        if not self.isVisible():
            return
        self.cpu_page.on_sample(s)
        self.mem_page.on_sample(s)
        for p in self.gpu_pages:
            p.on_sample(s)
        for p in self._disk_pages:
            p.on_sample(s)
        for p in self._net_pages:
            p.on_sample(s)

        idx = 0
        self._rows[idx][0].set_value(s.cpu_total, f"{s.cpu_total:.0f}%")
        idx += 1
        mem_pct = 100.0 * s.mem_used / s.mem_total if s.mem_total else 0
        self._rows[idx][0].set_value(mem_pct, f"{mem_pct:.0f}%   ({s.mem_used/1024**3:.1f} GB)")
        idx += 1
        for i in range(len(self.gpu_pages)):
            g = s.gpus[i] if i < len(s.gpus) else {}
            gu = g.get("util", 0) or 0
            self._rows[idx][0].set_value(gu, f"{gu}%")
            idx += 1
        for d in self._disks:
            r = s.disk_read_bps.get(d, 0.0); w = s.disk_write_bps.get(d, 0.0)
            self._rows[idx][0].set_value(r + w, f"R {_bps(r)}  W {_bps(w)}")
            idx += 1
        for i in self._ifaces:
            rx = s.net_rx_bps.get(i, 0.0); tx = s.net_tx_bps.get(i, 0.0)
            self._rows[idx][0].set_value(rx + tx, f"↓{_bps(rx)}  ↑{_bps(tx)}")
            idx += 1
