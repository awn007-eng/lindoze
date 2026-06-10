from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..graphs import MiniGraph
from ..sampler import cpu_model
from ..styles import BUTTON_QSS

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
        self._view_btn.setStyleSheet(BUTTON_QSS + " QToolButton { padding: 4px 10px; }")
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

        # --- Per-thread grid. Cells have a readable floor (100x50) and the
        # column count reflows on resize to fit the available viewport width.
        # If even one row of cells exceeds the viewport vertically (high-core
        # systems), the scroll area handles overflow vertically.
        self._grid_cell_min_w = 100
        self._grid_cell_min_h = 50
        self._grid_cols = 0  # set by _reflow_grid on first resize
        self._grid_w = QWidget()
        self._grid_layout = QGridLayout(self._grid_w)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(2)
        self._cells: list[MiniGraph] = []
        for i in range(n_threads):
            mg = MiniGraph(y_max=100.0, accent=CPU_ACCENT, compact=True, label=f"CPU {i}", max_history=3600)
            mg.setMinimumSize(self._grid_cell_min_w, self._grid_cell_min_h)
            self._cells.append(mg)

        self._grid_scroll = QScrollArea()
        self._grid_scroll.setWidget(self._grid_w)
        self._grid_scroll.setWidgetResizable(True)
        self._grid_scroll.setFrameShape(QScrollArea.NoFrame)
        # Vertical scroll only — horizontal overflow is prevented by reflow.
        self._grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._stack.addWidget(self._grid_scroll)

        # Seed an initial layout so cells aren't unplaced before first paint.
        self._reflow_grid(self._initial_cols(n_threads))

        # Right-click on either view -> switch
        self._agg.setContextMenuPolicy(Qt.CustomContextMenu)
        self._agg.customContextMenuRequested.connect(self._menu_aggregate)
        self._grid_w.setContextMenuPolicy(Qt.CustomContextMenu)
        self._grid_w.customContextMenuRequested.connect(self._menu_grid)

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

    def _initial_cols(self, n_threads: int) -> int:
        if n_threads >= 64:
            return 16
        if n_threads >= 16:
            return 8
        return 4

    def _cols_for_width(self, viewport_w: int) -> int:
        """How many columns fit in viewport_w at the cell-width floor."""
        spacing = self._grid_layout.spacing()
        denom = self._grid_cell_min_w + spacing
        cols = max(1, (viewport_w + spacing) // denom)
        return min(int(cols), self._n)

    def _reflow_grid(self, cols: int) -> None:
        if cols == self._grid_cols or cols < 1:
            return
        for mg in self._cells:
            self._grid_layout.removeWidget(mg)
        n = len(self._cells)
        full_rows, last_count = divmod(n, cols)
        # Center the partial last row so cells like a 4-cell tail in a 7-col
        # grid sit under the middle of the rows above instead of left-clinging.
        last_pad = (cols - last_count) // 2 if last_count else 0
        for i, mg in enumerate(self._cells):
            row = i // cols
            col_in_row = i % cols
            col = (last_pad + col_in_row) if row == full_rows else col_in_row
            self._grid_layout.addWidget(mg, row, col)
        self._grid_cols = cols

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._grid_scroll is None or self._grid_w is None:
            return
        viewport_w = self._grid_scroll.viewport().width()
        if viewport_w > 0:
            self._reflow_grid(self._cols_for_width(viewport_w))

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
