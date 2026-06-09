from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..graphs import MiniGraph

DISK_ACCENT = QColor("#4caf50")
NET_ACCENT = QColor("#e0c947")


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
        self._read = MiniGraph(y_max=None, accent=DISK_ACCENT, label="Read", max_history=3600)
        self._write = MiniGraph(y_max=None, accent=DISK_ACCENT, label="Write", max_history=3600)

        stats = QWidget(); sg = QGridLayout(stats)
        self._lr = self._stat(sg, 0, 0, "Read speed")
        self._lw = self._stat(sg, 0, 1, "Write speed")
        self._pr = self._stat(sg, 0, 2, "Peak read")
        self._pw = self._stat(sg, 0, 3, "Peak write")
        self._peak_r = 0.0; self._peak_w = 0.0

        root = QVBoxLayout(self)
        top = QHBoxLayout(); top.addWidget(title); top.addStretch()
        root.addLayout(top)
        gh = QHBoxLayout(); gh.addWidget(self._read); gh.addWidget(self._write)
        root.addLayout(gh, stretch=3)
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
        self._read.push(r); self._write.push(w)
        self._lr[1].setText(_bps(r)); self._lw[1].setText(_bps(w))
        self._peak_r = max(self._peak_r, r); self._peak_w = max(self._peak_w, w)
        self._pr[1].setText(_bps(self._peak_r)); self._pw[1].setText(_bps(self._peak_w))


class NetPage(QWidget):
    def __init__(self, iface: str, parent=None) -> None:
        super().__init__(parent)
        self.iface = iface
        title = QLabel(f"Network — {iface}")
        f = QFont(); f.setPointSize(20); f.setBold(True); title.setFont(f)
        self._rx = MiniGraph(y_max=None, accent=NET_ACCENT, label="Receive", max_history=3600)
        self._tx = MiniGraph(y_max=None, accent=NET_ACCENT, label="Send", max_history=3600)

        stats = QWidget(); sg = QGridLayout(stats)
        self._lrx = self._stat(sg, 0, 0, "Receive")
        self._ltx = self._stat(sg, 0, 1, "Send")
        self._prx = self._stat(sg, 0, 2, "Peak receive")
        self._ptx = self._stat(sg, 0, 3, "Peak send")
        self._peak_rx = 0.0; self._peak_tx = 0.0

        root = QVBoxLayout(self)
        top = QHBoxLayout(); top.addWidget(title); top.addStretch()
        root.addLayout(top)
        gh = QHBoxLayout(); gh.addWidget(self._rx); gh.addWidget(self._tx)
        root.addLayout(gh, stretch=3)
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
        self._rx.push(rx); self._tx.push(tx)
        self._lrx[1].setText(_bps(rx)); self._ltx[1].setText(_bps(tx))
        self._peak_rx = max(self._peak_rx, rx); self._peak_tx = max(self._peak_tx, tx)
        self._prx[1].setText(_bps(self._peak_rx)); self._ptx[1].setText(_bps(self._peak_tx))
