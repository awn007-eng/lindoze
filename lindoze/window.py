from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from .pages.performance import PerformancePage
from .pages.processes import ProcessesPage
from .pages.startup import StartupPage
from .process_sampler import ProcessSampler
from .sampler import Sampler


OUTER_SIDEBAR = """
QListWidget {
    background: #141414;
    border: none;
    outline: 0;
    color: #ccc;
}
QListWidget::item {
    padding: 14px 18px;
    border-left: 3px solid transparent;
}
QListWidget::item:selected {
    background: #232323;
    border-left: 3px solid #17a2b8;
    color: white;
}
QListWidget::item:hover {
    background: #1d1d1d;
}
"""

WINDOW_DARK = """
QMainWindow, QWidget {
    background: #1f1f1f;
    color: #e6e6e6;
}
QLabel { color: #e6e6e6; }
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lindoze Process Manager")
        self.resize(1200, 760)
        self.setStyleSheet(WINDOW_DARK)

        self.outer_sidebar = QListWidget()
        self.outer_sidebar.setFixedWidth(150)
        self.outer_sidebar.setStyleSheet(OUTER_SIDEBAR)
        nav_font = QFont(); nav_font.setPointSize(11)
        for label in ("Processes", "Performance", "Startup apps"):
            item = QListWidgetItem(label)
            item.setFont(nav_font)
            item.setSizeHint(QSize(150, 46))
            self.outer_sidebar.addItem(item)

        self.processes_page = ProcessesPage()
        self.performance_page = PerformancePage()
        self.startup_page = StartupPage()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.processes_page)
        self.stack.addWidget(self.performance_page)
        self.stack.addWidget(self.startup_page)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.outer_sidebar)
        layout.addWidget(self.stack, stretch=1)
        self.setCentralWidget(central)

        self.sampler = Sampler()
        self.sampler.sample_ready.connect(self.performance_page.on_sample)
        self.sampler.start()

        self.process_sampler = ProcessSampler()
        self.process_sampler.snapshot_ready.connect(self.processes_page.on_snapshot)

        # Wire tab switch -> stack + process-sampler pause/resume. Connect AFTER
        # process_sampler exists so the initial setCurrentRow doesn't fire into
        # an uninitialized attribute.
        self.outer_sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.outer_sidebar.currentRowChanged.connect(self._on_tab_changed)
        # Default to Performance — the per-thread grid was the whole point of
        # building this thing; make it the welcome screen.
        self.outer_sidebar.setCurrentRow(1)

    def _on_tab_changed(self, row: int) -> None:
        # Row 0 = Processes, Row 1 = Performance.
        self.process_sampler.set_active(row == 0)
