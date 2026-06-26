from __future__ import annotations

from collections import deque
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QSizePolicy

# Win11 Task Manager accent for CPU page is teal; we override per-page.
DEFAULT_ACCENT = QColor("#17a2b8")
GRID_COLOR = QColor(255, 255, 255, 22)
BG_COLOR = QColor("#1f1f1f")
LABEL_COLOR = QColor(220, 220, 220)
SUBLABEL_COLOR = QColor(170, 170, 170)

# A 1px dark halo painted behind the accent-coloured value readout so the
# number stays legible when a tall (high-utilisation) area fill rises up
# behind it in the same hue. Invisible against the dark card at idle.
_HALO_COLOR = QColor(0, 0, 0, 180)
_HALO_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _pct_fmt(v: float) -> str:
    return f"{v:.0f}%"


class MiniGraph(QWidget):
    """Filled area sparkline with optional grid, labels, scale and a second series.

    history_len: number of samples drawn (60 == 60s @ 1Hz, matches Task Manager).
    max_history: deque capacity. Defaults to history_len; pass a larger value to
        let the buffer accumulate more samples than are currently displayed —
        scale toggles can then re-render historical data without losing it.
    y_max: if None, autoscales to max observed (for throughput); else fixed (100 for %).

    Labeling (sysmon-inspired, rendered inside the Win11 dark card):
        show_scale: draw the y-axis ceiling (top) and floor (bottom) at the left
            edge. With a fixed y_max=100 and no value_fmt this is "100%"/"0%"
            (legacy behavior); with value_fmt it shows the live ceiling, e.g.
            "247 MB/s", so autoscaling throughput graphs gain a readable scale.
        show_value: print the current sample value on the graph in the accent
            colour — the "at a glance" readout.
        value_fmt: formats scale + value numbers. Defaults to "NN%".
        sublabel / set_sublabel(): small dim text top-right (per-core MHz).

    Secondary series (dual overlay, e.g. Disk Read+Write or Net RX+TX):
        accent2 + label2 enable a second trace fed via push2(). Both series share
        one autoscaled y-axis so they're directly comparable; a two-swatch legend
        is drawn when active.

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
        show_value: bool = False,
        value_fmt: Callable[[float], str] | None = None,
        sublabel: str = "",
        label_color: QColor | None = None,
        sublabel_color: QColor | None = None,
        accent2: QColor | None = None,
        label2: str = "",
        max_history: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        cap = max_history if max_history is not None else history_len
        self._buf: deque[float] = deque(maxlen=cap)
        self._buf2: deque[float] | None = deque(maxlen=cap) if accent2 is not None else None
        self._display_len = max(2, history_len)
        self._y_max_fixed = y_max
        self._autoscale_peak = 1.0
        self.accent = accent
        self.accent2 = accent2
        self.show_grid = show_grid
        self.label = label
        self.label2 = label2
        self.compact = compact
        # When True, draw the ceiling top-left and floor bottom-left.
        # Used by the single-graph pages (CPU aggregate, Memory) and throughput.
        self.show_scale = show_scale
        self.show_value = show_value
        self.value_fmt = value_fmt or _pct_fmt
        self.sublabel = sublabel
        # Per-graph label tints. Default to the module greys so existing call
        # sites are unchanged; the CPU grid cells opt into rose.
        self._label_color = label_color or LABEL_COLOR
        self._sublabel_color = sublabel_color or SUBLABEL_COLOR
        self.setMinimumSize(40, 24) if compact else self.setMinimumSize(120, 60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Cached brushes and pens — rebuilt only when the widget resizes,
        # not on every paint. The wash + fill gradients depend on geometry
        # so they were the most expensive per-paint allocations.
        self._wash_brush: QBrush | None = None
        self._fill_brush: QBrush | None = None
        self._wash_brush2: QBrush | None = None
        self._fill_brush2: QBrush | None = None
        self._trace_pen = QPen(self.accent, 1.8)
        self._trace_pen2 = QPen(self.accent2, 1.8) if accent2 is not None else None
        self._border_pen = QPen(QColor(255, 255, 255, 18), 1)
        self._grid_pen = QPen(GRID_COLOR, 1)
        self._label_font = QFont()
        self._label_font.setPointSize(7 if compact else 8)
        self._scale_font = QFont(); self._scale_font.setPointSize(8)
        self._value_font = QFont(); self._value_font.setPointSize(8 if compact else 11)
        self._value_font.setBold(True)

    def _make_brushes(self, accent: QColor) -> tuple[QBrush, QBrush]:
        r = self.rect()
        wash = QLinearGradient(0, r.top(), 0, r.bottom())
        wash_top = QColor(accent); wash_top.setAlpha(0)
        wash_bot = QColor(accent); wash_bot.setAlpha(28)
        wash.setColorAt(0.0, wash_top)
        wash.setColorAt(1.0, wash_bot)
        fill = QLinearGradient(0, r.top(), 0, r.bottom())
        hi = QColor(accent); hi.setAlpha(110)
        lo = QColor(accent); lo.setAlpha(18)
        fill.setColorAt(0.0, hi)
        fill.setColorAt(1.0, lo)
        return QBrush(wash), QBrush(fill)

    def _build_brushes(self) -> None:
        self._wash_brush, self._fill_brush = self._make_brushes(self.accent)
        if self.accent2 is not None:
            self._wash_brush2, self._fill_brush2 = self._make_brushes(self.accent2)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._wash_brush = None
        self._fill_brush = None
        self._wash_brush2 = None
        self._fill_brush2 = None

    def _note_peak(self, v: float) -> None:
        if self._y_max_fixed is None:
            self._autoscale_peak = max(self._autoscale_peak * 0.99, v, 1.0)

    def push(self, v: float) -> None:
        self._buf.append(v)
        # Decay toward the live max across whatever series exist this frame; the
        # second series (if any) tops it up in push2 before the repaint.
        self._note_peak(v)
        self.update()

    def push2(self, v: float) -> None:
        if self._buf2 is None:
            return
        self._buf2.append(v)
        self._note_peak(v)
        self.update()

    def set_sublabel(self, text: str) -> None:
        if text != self.sublabel:
            self.sublabel = text
            self.update()

    def set_display_len(self, n: int) -> None:
        n = max(2, min(n, self._buf.maxlen or n))
        if n != self._display_len:
            self._display_len = n
            self.update()

    def current(self) -> float:
        return self._buf[-1] if self._buf else 0.0

    def current2(self) -> float:
        return self._buf2[-1] if self._buf2 else 0.0

    def y_max(self) -> float:
        return self._y_max_fixed if self._y_max_fixed is not None else self._autoscale_peak

    def _draw_series(self, p: QPainter, buf: deque[float], fill_brush: QBrush,
                     trace_pen: QPen, r, ymax: float) -> None:
        n_have = len(buf)
        if n_have < 2:
            return
        display = self._display_len
        n_draw = min(n_have, display)
        samples = list(buf)[-n_draw:]
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

        p.fillPath(fill_path, fill_brush)
        p.setPen(trace_pen)
        p.drawPath(stroke_path)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.fillRect(r, BG_COLOR)

        if self._wash_brush is None or self._fill_brush is None:
            self._build_brushes()
        p.fillRect(r, self._wash_brush)
        if self._wash_brush2 is not None:
            p.fillRect(r, self._wash_brush2)

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

        # Both series share one y-axis (already reflected in _autoscale_peak)
        # so the two traces are directly comparable.
        ymax = self.y_max() or 1.0
        # Draw the secondary series first so the primary trace reads on top.
        if self._buf2 is not None and self._fill_brush2 is not None and self._trace_pen2:
            self._draw_series(p, self._buf2, self._fill_brush2, self._trace_pen2, r, ymax)
        self._draw_series(p, self._buf, self._fill_brush, self._trace_pen, r, ymax)

        self._paint_overlays(p, r)
        p.end()

    def _draw_readout_at(self, p: QPainter, x: int, baseline: int, text: str,
                         color: QColor) -> None:
        """Draw a value readout at a baseline point with a dark halo behind it."""
        p.setPen(_HALO_COLOR)
        for dx, dy in _HALO_OFFSETS:
            p.drawText(x + dx, baseline + dy, text)
        p.setPen(color)
        p.drawText(x, baseline, text)

    def _draw_readout_rect(self, p: QPainter, rect, flags, text: str,
                           color: QColor) -> None:
        """Halo'd readout positioned by an aligned rect (compact cells)."""
        p.setPen(_HALO_COLOR)
        for dx, dy in _HALO_OFFSETS:
            p.drawText(rect.translated(dx, dy), flags, text)
        p.setPen(color)
        p.drawText(rect, flags, text)

    def _paint_overlays(self, p: QPainter, r) -> None:
        inner = r.adjusted(4, 2, -4, -4)

        # Top-right: scale ceiling (graphs with a scale) or the per-core MHz
        # sublabel (compact CPU cells). These two never coexist on one widget.
        if self.show_scale:
            p.setFont(self._scale_font)
            p.setPen(LABEL_COLOR)
            if self._y_max_fixed == 100.0 and self.value_fmt is _pct_fmt:
                top_txt, bot_txt = "100%", "0%"
            else:
                top_txt, bot_txt = self.value_fmt(self.y_max()), self.value_fmt(0)
            p.drawText(inner, Qt.AlignTop | Qt.AlignRight, top_txt)
            p.drawText(inner, Qt.AlignBottom | Qt.AlignLeft, bot_txt)
        elif self.sublabel:
            p.setFont(self._label_font)
            p.setPen(self._sublabel_color)
            p.drawText(inner, Qt.AlignTop | Qt.AlignRight, self.sublabel)

        # Top-left stack: name label, then the current-value readout(s) beneath
        # it so the two never overlap. In dual mode the single label is replaced
        # by a bottom-right legend, so skip it here.
        y = inner.top()
        if self.label and self._buf2 is None:
            p.setFont(self._label_font)
            p.setPen(self._label_color)
            fm = p.fontMetrics()
            p.drawText(inner.left(), y + fm.ascent(), self.label)
            y += fm.height()

        if self.show_value:
            p.setFont(self._value_font)
            fm = p.fontMetrics()
            if self.compact:
                # Leave the top row for "CPU N" + MHz; value sits bottom-left.
                self._draw_readout_rect(p, inner, Qt.AlignBottom | Qt.AlignLeft,
                                        self.value_fmt(self.current()), self.accent)
            elif self._buf2 is not None:
                self._draw_readout_at(p, inner.left(), y + fm.ascent(),
                                      self.value_fmt(self.current()), self.accent)
                y += fm.height()
                self._draw_readout_at(p, inner.left(), y + fm.ascent(),
                                      self.value_fmt(self.current2()), self.accent2)
            else:
                self._draw_readout_at(p, inner.left(), y + fm.ascent(),
                                      self.value_fmt(self.current()), self.accent)

        # Dual-series legend, bottom-right.
        if self._buf2 is not None and (self.label or self.label2):
            self._paint_legend(p, r)

    def _paint_legend(self, p: QPainter, r) -> None:
        p.setFont(self._label_font)
        fm = p.fontMetrics()
        chip = 8
        gap = 4
        pad = 5
        entries = [(self.accent, self.label), (self.accent2, self.label2)]
        # Lay the two entries out on one row, bottom-right.
        widths = [chip + gap + fm.horizontalAdvance(text) for _, text in entries]
        total = sum(widths) + 12 * (len(entries) - 1)
        y = r.bottom() - pad - fm.height() // 2
        x = r.right() - pad - total
        for (col, text), w in zip(entries, widths):
            p.setBrush(QBrush(col))
            p.setPen(Qt.NoPen)
            p.drawRect(x, int(y - chip / 2), chip, chip)
            p.setPen(LABEL_COLOR)
            p.drawText(x + chip + gap, r.bottom() - pad, text)
            x += w + 12
