"""Desktop entry point with a package-only, isolated smoke mode."""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path


def smoke() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from duet.core.config import Config
    from duet.desktop_runtime import DesktopRuntime
    from duet.gui.window import MainWindow
    from duet.server import app as server_app

    static = server_app.resources.files("duet.server").joinpath("static")
    html = static.joinpath("index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="\./(assets/[^\"]+)"', html)
    if "<title>Duet" not in html:
        raise RuntimeError("bundled phone page is missing")
    if not any(path.endswith(".css") for path in refs):
        raise RuntimeError("phone CSS reference is missing")
    if not any(path.endswith(".js") for path in refs):
        raise RuntimeError("phone JavaScript reference is missing")
    if not all(static.joinpath(*path.split("/")).read_bytes() for path in refs):
        raise RuntimeError("one or more phone web assets are missing")

    with tempfile.TemporaryDirectory(prefix="duet-package-smoke-") as scratch:
        temp = Path(scratch)
        app = QApplication(sys.argv[:1])
        runtime = DesktopRuntime(
            config=Config(received_directory=temp / "Received"), data_directory=temp
        )
        window = MainWindow(runtime, poll_interval_ms=100)
        window.show()
        QTimer.singleShot(400, window.close)
        try:
            result = app.exec()
            if not window._closed:
                raise RuntimeError("Qt window did not close cleanly")
            return result
        finally:
            runtime.close()
            window.deleteLater()
            app.shutdown()


def main() -> int:
    if "--smoke-test" in sys.argv[1:]:
        return smoke()
    from duet.gui.__main__ import main as desktop_main

    return desktop_main()


if __name__ == "__main__":
    raise SystemExit(main())
