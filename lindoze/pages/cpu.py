from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..graphs import MiniGraph
from ..sampler import cpu_model

CPU_ACCENT = QColor("#17a2b8")  # Win11 teal-cyan


def _fmt_uptime(secs: float) -> str:
    s = int(secs)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{d}:{h:02d}:{m:02d}:{s:02d}"


class CPUPage(QWidget):
    def __init__(self, n_threads: int, parent=None) -> None:
        super().__init__(parent)
        self._n = n_threads
        self._boot = None

        title_box = QHBoxLayout()
        title = QLabel("CPU")
        f = QFont(); f.setPointSize(20); f.setBold(True)
        title.setFont(f)
        self._model_lbl = QLabel(cpu_model())
        self._model_lbl.setStyleSheet("color: #aaa;")
        self._model_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self._view_btn = QToolButton()
        self._view_btn.setText("Overall")
        self._view_btn.setToolTip("Toggle aggregate / logical processors")
        self._view_btn.setCursor(Qt.PointingHandCursor)
        self._view_btn.setStyleSheet(
            "QToolButton { color: #ddd; background: #2a2a2a; border: 1px solid #444; "
            "border-radius: 3px; padding: 4px 10px; } "
            "QToolButton:hover { background: #353535; }"
        )
        self._view_btn.clicked.connect(self._toggle_view)

        title_box.addWidget(title)
        title_box.addStretch()
        title_box.addWidget(self._view_btn)
        title_box.addSpacing(12)
        title_box.addWidget(self._model_lbl)

        # Stacked: page 0 = aggregate big graph; page 1 = per-thread grid.
        self._stack = QStackedWidget()

        # --- Aggregate view
        self._agg = MiniGraph(y_max=100.0, accent=CPU_ACCENT, show_scale=True, max_history=3600)
        self._stack.addWidget(self._agg)

        # --- Per-thread grid (8 cols x 4 rows for 32 threads; falls back gracefully)
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        cols = 8 if n_threads >= 16 else 4
        self._cells: list[MiniGraph] = []
        for i in range(n_threads):
            mg = MiniGraph(y_max=100.0, accent=CPU_ACCENT, compact=True, label=f"CPU {i}", max_history=3600)
            self._cells.append(mg)
            grid.addWidget(mg, i // cols, i % cols)
        self._stack.addWidget(grid_w)

        # Right-click on either view -> switch
        self._agg.setContextMenuPolicy(Qt.CustomContextMenu)
        self._agg.customContextMenuRequested.connect(self._menu_aggregate)
        grid_w.setContextMenuPolicy(Qt.CustomContextMenu)
        grid_w.customContextMenuRequested.connect(self._menu_grid)

        # Default to logical processors view — the reason this app exists.
        # Button label reads as the destination action, not the current state.
        self._stack.setCurrentIndex(1)
        self._view_btn.setText("Overall")

        # --- Stats block below
        stats_w = QWidget()
        sg = QGridLayout(stats_w)
        sg.setContentsMargins(8, 8, 8, 8)
        self._lbl_util = self._stat(sg, 0, 0, "Utilization")
        self._lbl_speed = self._stat(sg, 0, 1, "Speed")
        self._lbl_procs = self._stat(sg, 0, 2, "Processes")
        self._lbl_threads = self._stat(sg, 0, 3, "Threads")
        self._lbl_uptime = self._stat(sg, 1, 0, "Up time")
        self._lbl_cores = self._stat(sg, 1, 1, "Logical processors")
        self._lbl_max = self._stat(sg, 1, 2, "Max speed")
        self._lbl_min = self._stat(sg, 1, 3, "Min speed")
        self._lbl_cores[1].setText(str(n_threads))

        root = QVBoxLayout(self)
        root.addLayout(title_box)
        root.addWidget(self._stack, stretch=3)
        root.addWidget(stats_w, stretch=1)

    @staticmethod
    def _stat(grid: QGridLayout, r: int, c: int, label: str):
        box = QVBoxLayout()
        cap = QLabel(label)
        cap.setStyleSheet("color: #888; font-size: 10px;")
        val = QLabel("—")
        vf = QFont(); vf.setPointSize(14)
        val.setFont(vf)
        box.addWidget(cap)
        box.addWidget(val)
        wrap = QWidget(); wrap.setLayout(box)
        grid.addWidget(wrap, r, c)
        return (cap, val)

    def _menu_aggregate(self, pos) -> None:
        m = QMenu(self)
        a = QAction("Change graph to → Logical processors", self)
        a.triggered.connect(lambda: self._stack.setCurrentIndex(1))
        m.addAction(a)
        m.exec(self._agg.mapToGlobal(pos))

    def _menu_grid(self, pos) -> None:
        m = QMenu(self)
        a = QAction("Change graph to → Overall utilization", self)
        a.triggered.connect(lambda: self._stack.setCurrentIndex(0))
        m.addAction(a)
        m.exec(self.mapToGlobal(pos))

    def _toggle_view(self) -> None:
        # Button text names the destination, not the current view.
        if self._stack.currentIndex() == 0:  # currently overall → go to grid
            self._stack.setCurrentIndex(1)
            self._view_btn.setText("Overall")
        else:  # currently grid → go to overall
            self._stack.setCurrentIndex(0)
            self._view_btn.setText("Logical processors")

    def on_sample(self, s) -> None:
        if self._boot is None:
            import psutil
            self._boot = psutil.boot_time()
        self._agg.push(s.cpu_total)
        for i, v in enumerate(s.cpu_per[: self._n]):
            self._cells[i].push(v)
        self._lbl_util[1].setText(f"{s.cpu_total:.0f}%")
        if s.cpu_freq_per:
            avg_ghz = sum(s.cpu_freq_per) / len(s.cpu_freq_per) / 1000.0
            mx = max(s.cpu_freq_per) / 1000.0
            mn = min(s.cpu_freq_per) / 1000.0
            self._lbl_speed[1].setText(f"{avg_ghz:.2f} GHz")
            self._lbl_max[1].setText(f"{mx:.2f} GHz")
            self._lbl_min[1].setText(f"{mn:.2f} GHz")
        self._lbl_procs[1].setText(str(s.proc_count))
        self._lbl_threads[1].setText(str(s.thread_count))
        self._lbl_uptime[1].setText(_fmt_uptime(time.time() - self._boot))
