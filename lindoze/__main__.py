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
    from PySide6.QtWidgets import QApplication
    from .window import MainWindow

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
