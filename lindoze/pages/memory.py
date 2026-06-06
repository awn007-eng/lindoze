from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..graphs import MiniGraph

MEM_ACCENT = QColor("#a67bd1")  # Win11 mem violet


def _gb(n: int) -> str:
    return f"{n / 1024**3:.1f} GB"


class MemoryPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        title = QLabel("Memory")
        f = QFont(); f.setPointSize(20); f.setBold(True)
        title.setFont(f)

        self._graph = MiniGraph(y_max=100.0, accent=MEM_ACCENT, show_scale=True)

        stats = QWidget()
        sg = QGridLayout(stats)
        self._in_use = self._stat(sg, 0, 0, "In use")
        self._available = self._stat(sg, 0, 1, "Available")
        self._cached = self._stat(sg, 0, 2, "Cached")
        self._buffers = self._stat(sg, 0, 3, "Buffers")
        self._committed = self._stat(sg, 1, 0, "Total")
        self._swap_used = self._stat(sg, 1, 1, "Swap in use")
        self._swap_total = self._stat(sg, 1, 2, "Swap total")

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(title); top.addStretch()
        root.addLayout(top)
        root.addWidget(self._graph, stretch=3)
        root.addWidget(stats, stretch=1)

    @staticmethod
    def _stat(grid: QGridLayout, r: int, c: int, label: str):
        box = QVBoxLayout()
        cap = QLabel(label)
        cap.setStyleSheet("color: #888; font-size: 10px;")
        val = QLabel("—")
        vf = QFont(); vf.setPointSize(14)
        val.setFont(vf)
        box.addWidget(cap); box.addWidget(val)
        w = QWidget(); w.setLayout(box)
        grid.addWidget(w, r, c)
        return (cap, val)

    def on_sample(self, s) -> None:
        pct = 100.0 * s.mem_used / s.mem_total if s.mem_total else 0.0
        self._graph.push(pct)
        self._in_use[1].setText(f"{_gb(s.mem_used)} ({pct:.0f}%)")
        self._available[1].setText(_gb(s.mem_available))
        self._cached[1].setText(_gb(s.mem_cached))
        self._buffers[1].setText(_gb(s.mem_buffers))
        self._committed[1].setText(_gb(s.mem_total))
        self._swap_used[1].setText(_gb(s.swap_used))
        self._swap_total[1].setText(_gb(s.swap_total))
