from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..graphs import MiniGraph

DISK_ACCENT = QColor("#4caf50")   # green — Read / Receive primary
NET_ACCENT = QColor("#e0c947")    # gold

# Second-series tones for the dual overlay. Kept in-family with the page accent
# but lighter/shifted so the two traces are distinguishable. Tuning knobs — adjust
# if the overlay reads muddy on the live graph.
DISK_ACCENT2 = QColor("#a5d66a")  # lime — Write
NET_ACCENT2 = QColor("#e0934a")   # amber — Send


def _bps(n: float) -> str:
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024; i += 1
    return f"{n:.1f} {units[i]}"


class DiskPage(QWidget):
    def __init__(self, device: str, parent=None) -> None:
        super().__init__(parent)
        self.device = device
        title = QLabel(f"Disk — {device}")
        f = QFont(); f.setPointSize(20); f.setBold(True); title.setFont(f)
        # One box, two traces (Read + Write) sharing a y-axis, sysmon-style.
        self._graph = MiniGraph(
            y_max=None, accent=DISK_ACCENT, accent2=DISK_ACCENT2,
            label="Read", label2="Write",
            show_value=True, show_scale=True, value_fmt=_bps, max_history=3600,
        )

        stats = QWidget(); sg = QGridLayout(stats)
        self._lr = self._stat(sg, 0, 0, "Read speed")
        self._lw = self._stat(sg, 0, 1, "Write speed")
        self._pr = self._stat(sg, 0, 2, "Peak read")
        self._pw = self._stat(sg, 0, 3, "Peak write")
        self._peak_r = 0.0; self._peak_w = 0.0

        root = QVBoxLayout(self)
        top = QHBoxLayout(); top.addWidget(title); top.addStretch()
        root.addLayout(top)
        root.addWidget(self._graph, stretch=3)
        root.addWidget(stats, stretch=1)

    @staticmethod
    def _stat(grid, r, c, label):
        box = QVBoxLayout()
        cap = QLabel(label); cap.setStyleSheet("color: #888; font-size: 10px;")
        val = QLabel("—"); vf = QFont(); vf.setPointSize(14); val.setFont(vf)
        box.addWidget(cap); box.addWidget(val)
        w = QWidget(); w.setLayout(box)
        grid.addWidget(w, r, c)
        return (cap, val)

    def on_sample(self, s) -> None:
        r = s.disk_read_bps.get(self.device, 0.0)
        w = s.disk_write_bps.get(self.device, 0.0)
        self._graph.push(r); self._graph.push2(w)
        self._lr[1].setText(_bps(r)); self._lw[1].setText(_bps(w))
        self._peak_r = max(self._peak_r, r); self._peak_w = max(self._peak_w, w)
        self._pr[1].setText(_bps(self._peak_r)); self._pw[1].setText(_bps(self._peak_w))


class NetPage(QWidget):
    def __init__(self, iface: str, parent=None) -> None:
        super().__init__(parent)
        self.iface = iface
        title = QLabel(f"Network — {iface}")
        f = QFont(); f.setPointSize(20); f.setBold(True); title.setFont(f)
        # One box, two traces (Receive + Send) sharing a y-axis, sysmon-style.
        self._graph = MiniGraph(
            y_max=None, accent=NET_ACCENT, accent2=NET_ACCENT2,
            label="Receive", label2="Send",
            show_value=True, show_scale=True, value_fmt=_bps, max_history=3600,
        )

        stats = QWidget(); sg = QGridLayout(stats)
        self._lrx = self._stat(sg, 0, 0, "Receive")
        self._ltx = self._stat(sg, 0, 1, "Send")
        self._prx = self._stat(sg, 0, 2, "Peak receive")
        self._ptx = self._stat(sg, 0, 3, "Peak send")
        self._peak_rx = 0.0; self._peak_tx = 0.0

        root = QVBoxLayout(self)
        top = QHBoxLayout(); top.addWidget(title); top.addStretch()
        root.addLayout(top)
        root.addWidget(self._graph, stretch=3)
        root.addWidget(stats, stretch=1)

    @staticmethod
    def _stat(grid, r, c, label):
        box = QVBoxLayout()
        cap = QLabel(label); cap.setStyleSheet("color: #888; font-size: 10px;")
        val = QLabel("—"); vf = QFont(); vf.setPointSize(14); val.setFont(vf)
        box.addWidget(cap); box.addWidget(val)
        w = QWidget(); w.setLayout(box)
        grid.addWidget(w, r, c)
        return (cap, val)

    def on_sample(self, s) -> None:
        rx = s.net_rx_bps.get(self.iface, 0.0)
        tx = s.net_tx_bps.get(self.iface, 0.0)
        self._graph.push(rx); self._graph.push2(tx)
        self._lrx[1].setText(_bps(rx)); self._ltx[1].setText(_bps(tx))
        self._peak_rx = max(self._peak_rx, rx); self._peak_tx = max(self._peak_tx, tx)
        self._prx[1].setText(_bps(self._peak_rx)); self._ptx[1].setText(_bps(self._peak_tx))
