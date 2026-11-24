"""時間のかかる処理（圧縮・解凍）を裏で動かし、進み具合の窓を出す。"""

import threading

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QProgressDialog


class _Bridge(QObject):
    progress = Signal(float, str)  # 割合（わからなければ -1）, 説明
    finished = Signal(object, object)  # 結果, エラー


def run_job(parent, title, func):
    """func(progress=..., cancel=...) を裏で動かし、終わるまで待って (結果, エラー) を返す。

    待っている間も画面は固まらず、「中止」ボタンで止められる。
    """
    dialog = QProgressDialog(title, "中止", 0, 1000, parent)
    dialog.setWindowTitle(title)
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setMinimumDuration(300)  # すぐ終わるときは窓を出さない
    dialog.setMinimumWidth(420)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setValue(0)

    cancel = threading.Event()
    bridge = _Bridge()
    loop = QEventLoop()
    outcome = {}

    def on_progress(fraction, text):
        if dialog.wasCanceled():
            cancel.set()
            return
        if fraction < 0:
            dialog.setRange(0, 0)  # 割合がわからない → 動き続ける表示
        else:
            if dialog.maximum() == 0:
                dialog.setRange(0, 1000)
            dialog.setValue(int(fraction * 1000))
        dialog.setLabelText(f"{title}\n{text}" if text else title)

    def on_finished(result, error):
        outcome["result"] = result
        outcome["error"] = error
        loop.quit()

    bridge.progress.connect(on_progress)
    bridge.finished.connect(on_finished)
    dialog.canceled.connect(cancel.set)
    dialog.canceled.connect(lambda: dialog.setLabelText("中止しています…"))

    def report(fraction, text=""):
        try:
            bridge.progress.emit(-1.0 if fraction is None else float(fraction), text or "")
        except RuntimeError:
            pass

    def work():
        result, error = None, None
        try:
            result = func(progress=report, cancel=cancel)
        except BaseException as e:  # どんな失敗も画面側で知らせる
            error = e
        try:
            bridge.finished.emit(result, error)
        except RuntimeError:
            pass

    # 進み具合の知らせが来ないあいだも「中止」を見逃さないよう、定期的に確かめる
    timer = QTimer()
    timer.timeout.connect(lambda: dialog.wasCanceled() and cancel.set())
    timer.start(200)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    loop.exec()
    timer.stop()
    thread.join()
    dialog.close()
    dialog.deleteLater()
    return outcome.get("result"), outcome.get("error")
