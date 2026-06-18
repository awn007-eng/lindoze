from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="lindoze")
    parser.add_argument(
        "--dump-gpu", action="store_true",
        help="Dump raw GPU detection state (sysfs paths, PMU fds/values) to "
             "stderr and exit. Paste the output when filing an Intel GPU bug.",
    )
    args = parser.parse_args()

    if args.dump_gpu:
        from .gpu_backends import dump_gpus
        dump_gpus()
        return 0

    import setproctitle
    from pathlib import Path

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from .window import MainWindow

    # Override the python interpreter's argv[0] / /proc/<pid>/comm so the
    # app shows up as "lindoze" in ps/htop/our own Processes tab instead of
    # the misleading "python". Comm name truncates to 15 chars in kernel.
    setproctitle.setproctitle("lindoze")

    app = QApplication(sys.argv)
    app.setOrganizationName("Lindoze")
    app.setOrganizationDomain("lindoze.local")
    app.setApplicationName("Lindoze Process Manager")
    # Tie the running app to lindoze.desktop so Wayland compositors
    # (KWin/Mutter) can resolve the window's app_id to the .desktop's
    # Icon= entry. Without this the taskbar shows a generic fallback.
    app.setDesktopFileName("lindoze")

    # Window-icon fallback: prefer an installed themed icon (user, then
    # system), then the SVG bundled inside the package. The packaged copy is
    # what saves pipx installs, which drop no .desktop and no hicolor icon —
    # without it the window/taskbar shows a generic placeholder.
    for candidate in (
        Path.home() / ".local/share/icons/hicolor/scalable/apps/lindoze.svg",
        Path("/usr/share/icons/hicolor/scalable/apps/lindoze.svg"),
        Path(__file__).resolve().parent / "lindoze.svg",
    ):
        if candidate.exists():
            app.setWindowIcon(QIcon(str(candidate)))
            break

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
