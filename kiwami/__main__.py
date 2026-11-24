"""起動: python3 -m kiwami [ファイル ...]"""

import os
import sys


def main():
    # Ubuntu の標準画面（Wayland）では「常に最前面」が効かないため、
    # 互換モード（XWayland）で動かす。使えない環境では Wayland に戻る。
    if os.environ.get("WAYLAND_DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb;wayland"

    from PySide6.QtWidgets import QApplication

    from . import APP_NAME
    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    files = [a for a in sys.argv[1:] if not a.startswith("-")]
    if files:
        window.add_paths(files)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
