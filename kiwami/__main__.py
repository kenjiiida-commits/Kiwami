"""起動: python3 -m kiwami [ファイル ...]

すでに極み窓が開いていれば、新しい窓は作らず、開いている窓にファイルを送る
（ファイル画面の右クリックから何度呼んでも窓が増えないように）。
新しい窓を開きたいときは --new-window を付ける。
"""

import json
import os
import sys


def server_name():
    return f"kiwami-{os.getuid()}"


def main():
    # Ubuntu の標準画面（Wayland）では「常に最前面」が効かないため、
    # 互換モード（XWayland）で動かす。使えない環境では Wayland に戻る。
    if os.environ.get("WAYLAND_DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb;wayland"

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    try:
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
    except ImportError:  # python3-pyside6.qtnetwork が入っていなければ、窓を1つにまとめる機能は使わない
        QLocalServer = QLocalSocket = None

    from . import APP_NAME

    args = sys.argv[1:]
    new_window = "--new-window" in args
    files = [os.path.abspath(a) for a in args if not a.startswith("--")]

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("kiwami")

    if QLocalSocket is None:
        new_window = True
    if not new_window:
        # すでに開いている極み窓があれば、そちらにファイルを渡して終わる
        sock = QLocalSocket()
        sock.connectToServer(server_name())
        if sock.waitForConnected(500):
            sock.write(json.dumps({"files": files}).encode("utf-8") + b"\n")
            sock.flush()
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
            return 0

    from .main_window import ICON_PATH, MainWindow

    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    window = MainWindow()
    window.show()

    server = None
    if not new_window:
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.UserAccessOption)  # 自分だけが送れる
        QLocalServer.removeServer(server_name())  # 前回の残りがあれば片付ける
        if server.listen(server_name()):
            def on_connection():
                conn = server.nextPendingConnection()
                buf = bytearray()

                def on_ready():
                    buf.extend(bytes(conn.readAll()))
                    if not buf.endswith(b"\n"):
                        return
                    try:
                        data = json.loads(buf.decode("utf-8"))
                        paths = [p for p in data.get("files", []) if isinstance(p, str)]
                    except (ValueError, AttributeError):
                        paths = []
                    conn.disconnectFromServer()
                    window.bring_to_front()
                    if paths:
                        window.add_paths(paths)

                conn.readyRead.connect(on_ready)
                conn.disconnected.connect(conn.deleteLater)

            server.newConnection.connect(on_connection)

    if files:
        window.add_paths(files)
    code = app.exec()
    if server is not None:
        server.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
