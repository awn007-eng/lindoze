from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QBrush, QFont
from PySide6.QtWidgets import QWidget, QSizePolicy

# Win11 Task Manager accent for CPU page is teal; we override per-page.
DEFAULT_ACCENT = QColor("#17a2b8")
GRID_COLOR = QColor(255, 255, 255, 22)
BG_COLOR = QColor("#1f1f1f")
LABEL_COLOR = QColor(220, 220, 220)


class MiniGraph(QWidget):
    """Filled area sparkline with optional grid + label.

    history_len: number of samples drawn (60 == 60s @ 1Hz, matches Task Manager).
    max_history: deque capacity. Defaults to history_len; pass a larger value to
        let the buffer accumulate more samples than are currently displayed —
        scale toggles can then re-render historical data without losing it.
    y_max: if None, autoscales to max observed (for throughput); else fixed (100 for %).

    The buffer starts empty: graphs grow in from the right edge rather than
    sitting on a misleading flat-zero baseline at launch.
    """

    def __init__(
        self,
        history_len: int = 60,
        y_max: float | None = 100.0,
        accent: QColor = DEFAULT_ACCENT,
        show_grid: bool = True,
        label: str = "",
        compact: bool = False,
        show_scale: bool = False,
        max_history: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        cap = max_history if max_history is not None else history_len
        self._buf: deque[float] = deque(maxlen=cap)
        self._display_len = max(2, history_len)
        self._y_max_fixed = y_max
        self._autoscale_peak = 1.0
        self.accent = accent
        self.show_grid = show_grid
        self.label = label
        self.compact = compact
        # When True, draw "100%" top-left and "0%" bottom-left instead of label.
        # Used by the single-graph pages (CPU aggregate, Memory).
        self.show_scale = show_scale
        self.setMinimumSize(40, 24) if compact else self.setMinimumSize(120, 60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def push(self, v: float) -> None:
        self._buf.append(v)
        if self._y_max_fixed is None:
            self._autoscale_peak = max(self._autoscale_peak * 0.99, v, 1.0)
        self.update()

    def set_display_len(self, n: int) -> None:
        n = max(2, min(n, self._buf.maxlen or n))
        if n != self._display_len:
            self._display_len = n
            self.update()

    def current(self) -> float:
        return self._buf[-1] if self._buf else 0.0

    def y_max(self) -> float:
        return self._y_max_fixed if self._y_max_fixed is not None else self._autoscale_peak

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.fillRect(r, BG_COLOR)

        # Border
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))

        # Grid
        if self.show_grid and not self.compact:
            p.setPen(QPen(GRID_COLOR, 1))
            for i in range(1, 10):
                x = r.left() + r.width() * i / 10
                p.drawLine(int(x), r.top(), int(x), r.bottom())
                y = r.top() + r.height() * i / 10
                p.drawLine(r.left(), int(y), r.right(), int(y))

        # Data — right-anchored: newest sample at right edge, oldest at
        # right - (display_len-1)*step. Buffer may hold more than we display
        # (when max_history > display_len) so the scale toggle can re-render
        # without losing history.
        n_have = len(self._buf)
        if n_have < 2:
            p.end()
            return
        display = self._display_len
        n_draw = min(n_have, display)
        samples = list(self._buf)[-n_draw:]
        ymax = self.y_max() or 1.0
        w = r.width()
        h = r.height()
        step = w / (display - 1)
        oldest_x = r.right() - (n_draw - 1) * step

        path = QPainterPath()
        path.moveTo(oldest_x, r.bottom())
        for i, v in enumerate(samples):
            x = oldest_x + i * step
            y = r.bottom() - (min(v, ymax) / ymax) * h
            path.lineTo(x, y)
        path.lineTo(oldest_x + (n_draw - 1) * step, r.bottom())
        path.closeSubpath()

        fill = QColor(self.accent)
        fill.setAlpha(70)
        p.fillPath(path, QBrush(fill))

        stroke_path = QPainterPath()
        for i, v in enumerate(samples):
            x = oldest_x + i * step
            y = r.bottom() - (min(v, ymax) / ymax) * h
            if i == 0:
                stroke_path.moveTo(x, y)
            else:
                stroke_path.lineTo(x, y)
        p.setPen(QPen(self.accent, 1.4))
        p.drawPath(stroke_path)

        # Scale markers (top-left = ymax, bottom-left = 0) for big single-graph
        # pages; otherwise fall back to the inline label (used by grid cells
        # like "CPU 0" and the multi-graph Disk/Network/GPU pages).
        if self.show_scale:
            font = QFont()
            font.setPointSize(8)
            p.setFont(font)
            p.setPen(LABEL_COLOR)
            p.drawText(r.adjusted(4, 2, -4, -4), Qt.AlignTop | Qt.AlignLeft, "100%")
            p.drawText(r.adjusted(4, 2, -4, -4), Qt.AlignBottom | Qt.AlignLeft, "0%")
        elif self.label:
            font = QFont()
            font.setPointSize(7 if self.compact else 8)
            p.setFont(font)
            p.setPen(LABEL_COLOR)
            p.drawText(r.adjusted(3, 1, -3, -3), Qt.AlignTop | Qt.AlignLeft, self.label)

        p.end()
