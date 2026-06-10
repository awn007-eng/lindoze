from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
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
        # Cached brushes and pens — rebuilt only when the widget resizes,
        # not on every paint. The wash + fill gradients depend on geometry
        # so they were the most expensive per-paint allocations.
        self._wash_brush: QBrush | None = None
        self._fill_brush: QBrush | None = None
        self._trace_pen = QPen(self.accent, 1.8)
        self._border_pen = QPen(QColor(255, 255, 255, 18), 1)
        self._grid_pen = QPen(GRID_COLOR, 1)
        self._label_font = QFont()
        self._label_font.setPointSize(7 if compact else 8)
        self._scale_font = QFont(); self._scale_font.setPointSize(8)

    def _build_brushes(self) -> None:
        r = self.rect()
        wash = QLinearGradient(0, r.top(), 0, r.bottom())
        wash_top = QColor(self.accent); wash_top.setAlpha(0)
        wash_bot = QColor(self.accent); wash_bot.setAlpha(28)
        wash.setColorAt(0.0, wash_top)
        wash.setColorAt(1.0, wash_bot)
        self._wash_brush = QBrush(wash)
        fill = QLinearGradient(0, r.top(), 0, r.bottom())
        hi = QColor(self.accent); hi.setAlpha(110)
        lo = QColor(self.accent); lo.setAlpha(18)
        fill.setColorAt(0.0, hi)
        fill.setColorAt(1.0, lo)
        self._fill_brush = QBrush(fill)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._wash_brush = None
        self._fill_brush = None

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

        if self._wash_brush is None or self._fill_brush is None:
            self._build_brushes()
        p.fillRect(r, self._wash_brush)

        # Border kept softer than default Qt frame so the per-thread grid
        # at idle reads as "32 quiet cells" rather than "grid of empty boxes."
        p.setPen(self._border_pen)
        p.drawRect(r.adjusted(0, 0, -1, -1))

        if self.show_grid and not self.compact:
            p.setPen(self._grid_pen)
            w_grid = r.width()
            h_grid = r.height()
            for i in range(1, 10):
                x = int(r.left() + w_grid * i / 10)
                p.drawLine(x, r.top(), x, r.bottom())
                y = int(r.top() + h_grid * i / 10)
                p.drawLine(r.left(), y, r.right(), y)

        # Data — right-anchored: newest sample at right edge, oldest at
        # right - (display_len-1)*step. Buffer may hold more than we display
        # (when max_history > display_len) so the scale toggle can re-render
        # without losing history.
        n_have = len(self._buf)
        if n_have < 2:
            self._paint_label(p, r)
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
        bottom_y = r.bottom()

        # Single point pass — feeds both the filled area path and the trace
        # stroke path so we don't recompute (x, y) for every sample twice.
        fill_path = QPainterPath()
        stroke_path = QPainterPath()
        fill_path.moveTo(oldest_x, bottom_y)
        first = True
        for i, v in enumerate(samples):
            x = oldest_x + i * step
            y = bottom_y - (min(v, ymax) / ymax) * h
            fill_path.lineTo(x, y)
            if first:
                stroke_path.moveTo(x, y); first = False
            else:
                stroke_path.lineTo(x, y)
        fill_path.lineTo(oldest_x + (n_draw - 1) * step, bottom_y)
        fill_path.closeSubpath()

        p.fillPath(fill_path, self._fill_brush)
        p.setPen(self._trace_pen)
        p.drawPath(stroke_path)

        self._paint_label(p, r)
        p.end()

    def _paint_label(self, p: QPainter, r) -> None:
        if self.show_scale:
            p.setFont(self._scale_font)
            p.setPen(LABEL_COLOR)
            p.drawText(r.adjusted(4, 2, -4, -4), Qt.AlignTop | Qt.AlignLeft, "100%")
            p.drawText(r.adjusted(4, 2, -4, -4), Qt.AlignBottom | Qt.AlignLeft, "0%")
        elif self.label:
            p.setFont(self._label_font)
            p.setPen(LABEL_COLOR)
            p.drawText(r.adjusted(3, 1, -3, -3), Qt.AlignTop | Qt.AlignLeft, self.label)
