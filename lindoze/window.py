from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, QSize, Qt
from PySide6.QtGui import QAction, QFont, QGuiApplication
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
/* The generic QWidget rule above cascades into QMenu (a QWidget) and
   suppresses the native check indicator, so style the menu + indicator
   explicitly — otherwise checkable items (e.g. the Columns menu) show no
   tick. A filled teal box = checked, hollow outline = unchecked. */
QMenu {
    background: #232323;
    color: #e6e6e6;
    border: 1px solid #333;
}
QMenu::item { padding: 4px 24px 4px 20px; }
QMenu::item:selected { background: #2d4a5a; }
QMenu::item:disabled { color: #888; }
QMenu::separator { height: 1px; background: #333; margin: 4px 0; }
QMenu::indicator {
    width: 13px;
    height: 13px;
    margin-left: 4px;
    border: 1px solid #555;
    border-radius: 2px;
}
QMenu::indicator:checked {
    background: #17a2b8;
    border: 1px solid #17a2b8;
}
/* Menu bar: the generic QWidget color above applies even to disabled items,
   so style the disabled state explicitly or a greyed-out menu still looks
   clickable. */
QMenuBar { background: #1f1f1f; color: #e6e6e6; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: #2d4a5a; }
QMenuBar::item:disabled { color: #5a5a5a; }
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lindoze Process Manager")
        self.resize(1200, 760)
        # Allow shrinking to a small corner-of-the-screen monitor; internal
        # views scroll rather than enforcing a large floor.
        self.setMinimumSize(380, 280)
        self.setStyleSheet(WINDOW_DARK)

        self._settings = QSettings()
        geom = self._settings.value("window/geometry")
        if isinstance(geom, QByteArray) and not geom.isEmpty():
            self.restoreGeometry(geom)

        # "Always on top": the portable Qt flag works on X11 but Wayland
        # compositors (KWin/Mutter) don't let a client set its own stacking, so
        # the menu item is disabled there with an explanation rather than
        # silently doing nothing. Apply the persisted flag before the first
        # show() so it takes effect without a disruptive re-show.
        self._is_wayland = QGuiApplication.platformName().startswith("wayland")
        aot = (self._settings.value("window/always_on_top", False, type=bool)
               and not self._is_wayland)
        if aot:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._build_menubar(aot)

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
        # Restore last-viewed tab; default to Performance — the per-thread grid
        # was the whole point of building this thing.
        last_tab = self._settings.value("window/last_tab", 1, type=int)
        if not 0 <= last_tab < self.outer_sidebar.count():
            last_tab = 1
        self.outer_sidebar.setCurrentRow(last_tab)

        header_state = self._settings.value("processes/header_state")
        if isinstance(header_state, QByteArray) and not header_state.isEmpty():
            self.processes_page.tree.header().restoreState(header_state)

        # Hand the page its sampler so it can gate the expensive exe/cmdline
        # reads on whether the Path/Command-line columns or search need them.
        # Done after restoreState so the initial column-visibility is final.
        self.processes_page.set_sampler(self.process_sampler)

    def _build_menubar(self, always_on_top: bool) -> None:
        view = self.menuBar().addMenu("View")
        view.setToolTipsVisible(True)
        act = QAction("Always on Top", self)
        act.setCheckable(True)
        act.setChecked(always_on_top)  # set before connecting — no signal fires
        act.toggled.connect(self._toggle_always_on_top)
        view.addAction(act)
        self._aot_action = act
        if self._is_wayland:
            # Always on Top is the only View item and Wayland can't honor it
            # (KWin/Mutter ignore client stacking), so grey the whole menu —
            # visible for layout consistency, but it won't open to a dead entry.
            view.menuAction().setEnabled(False)
            view.menuAction().setToolTip(
                "Always on Top isn't available under Wayland.\n"
                "Use KWin: title-bar → More Actions → Keep Above."
            )

    def _toggle_always_on_top(self, checked: bool) -> None:
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self._settings.setValue("window/always_on_top", checked)
        # Toggling a window flag hides the window on most platforms; re-show so
        # it stays visible.
        self.show()

    def _on_tab_changed(self, row: int) -> None:
        # Row 0 = Processes, Row 1 = Performance.
        self.process_sampler.set_active(row == 0)

    def closeEvent(self, event) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/last_tab", self.outer_sidebar.currentRow())
        self._settings.setValue(
            "processes/header_state",
            self.processes_page.tree.header().saveState(),
        )
        # Stop sampler timers so nothing keeps firing after the window is gone.
        self.sampler.stop()
        self.process_sampler.set_active(False)
        super().closeEvent(event)
