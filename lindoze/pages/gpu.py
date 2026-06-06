from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..graphs import MiniGraph

GPU_ACCENT = QColor("#d17b59")  # Win11 GPU orange


def _gb(n: int) -> str:
    return f"{n / 1024**3:.1f} GB"


class GPUPage(QWidget):
    def __init__(self, index: int, title_text: str = "GPU 0", parent=None) -> None:
        super().__init__(parent)
        self._index = index

        title = QLabel(title_text)
        f = QFont(); f.setPointSize(20); f.setBold(True)
        title.setFont(f)
        self._name = QLabel("—")
        self._name.setStyleSheet("color: #aaa;")
        self._name.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self._util_g = MiniGraph(y_max=100.0, accent=GPU_ACCENT, label="Utilization   100%")
        self._vram_g = MiniGraph(y_max=100.0, accent=GPU_ACCENT, label="VRAM   100%")

        stats = QWidget()
        sg = QGridLayout(stats)
        self._util = self._stat(sg, 0, 0, "Utilization")
        self._vram = self._stat(sg, 0, 1, "Dedicated GPU memory")
        self._temp = self._stat(sg, 0, 2, "Temperature")
        self._power = self._stat(sg, 0, 3, "Power")
        # Tooltip explains the "Package power" label that appears on AMD APUs.
        self._power[0].setToolTip(
            "On AMD integrated GPUs, this is total package power "
            "(CPU + iGPU + uncore) since they share a power budget."
        )
        self._enc = self._stat(sg, 1, 0, "Video encode")
        self._dec = self._stat(sg, 1, 1, "Video decode")
        self._ckcore = self._stat(sg, 1, 2, "Core clock")
        self._ckmem = self._stat(sg, 1, 3, "Memory clock")

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(title); top.addStretch(); top.addWidget(self._name)
        root.addLayout(top)
        graphs = QHBoxLayout()
        graphs.addWidget(self._util_g); graphs.addWidget(self._vram_g)
        root.addLayout(graphs, stretch=3)
        root.addWidget(stats, stretch=1)

    @staticmethod
    def _stat(grid, r, c, label):
        box = QVBoxLayout()
        cap = QLabel(label); cap.setStyleSheet("color: #888; font-size: 10px;")
        val = QLabel("—")
        vf = QFont(); vf.setPointSize(14); val.setFont(vf)
        box.addWidget(cap); box.addWidget(val)
        w = QWidget(); w.setLayout(box)
        grid.addWidget(w, r, c)
        return (cap, val)

    def on_sample(self, s) -> None:
        if self._index >= len(s.gpus):
            return
        g = s.gpus[self._index]
        if not g:
            return
        self._name.setText(g.get("name", ""))
        util = g.get("util", 0) or 0
        self._util_g.push(util)
        self._util[1].setText(f"{util}%")
        mt = g.get("mem_total", 0) or 0
        mu = g.get("mem_used", 0) or 0
        pct = 100.0 * mu / mt if mt else 0
        self._vram_g.push(pct)
        self._vram[1].setText(f"{_gb(mu)} / {_gb(mt)}" if mt else "—")
        temp = g.get("temp")
        self._temp[1].setText(f"{temp} °C" if temp is not None else "—")
        pw = g.get("power")
        self._power[1].setText(f"{pw:.1f} W" if pw is not None else "—")
        self._power[0].setText("Package power" if g.get("is_integrated") else "Power")
        enc = g.get("enc"); dec = g.get("dec")
        self._enc[1].setText(f"{enc}%" if enc is not None else "—")
        self._dec[1].setText(f"{dec}%" if dec is not None else "—")
        ck = g.get("clk_core"); cm = g.get("clk_mem")
        self._ckcore[1].setText(f"{ck} MHz" if ck else "—")
        self._ckmem[1].setText(f"{cm} MHz" if cm else "—")
