from __future__ import annotations

import sys

import setproctitle
from PySide6.QtWidgets import QApplication

from .window import MainWindow


def main() -> int:
    # Override the python interpreter's argv[0] / /proc/<pid>/comm so the
    # app shows up as "lindoze" in ps/htop/our own Processes tab instead of
    # the misleading "python". Comm name truncates to 15 chars in kernel.
    setproctitle.setproctitle("lindoze")

    app = QApplication(sys.argv)
    app.setApplicationName("Lindoze Process Manager")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
