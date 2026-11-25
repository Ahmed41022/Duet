"""Launch Duet's desktop application."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from duet.desktop_runtime import DesktopRuntime
from duet.gui.window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Duet")
    runtime = DesktopRuntime()
    window = MainWindow(runtime)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
