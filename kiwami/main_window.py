"""小窓（メインウィンドウ）。ファイルをドロップすると判別結果を表示する。"""

import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .detect import format_size, inspect

PATH_ROLE = Qt.UserRole


def _short_type(info):
    if info.error:
        return "エラー"
    if info.kind == "dir":
        return "フォルダー"
    if info.kind == "symlink":
        return "リンク"
    if info.kind != "file":
        return "特殊"
    text = info.description.split(",")[0]
    return text if len(text) <= 40 else text[:39] + "…"


class DropHint(QLabel):
    def __init__(self):
        super().__init__("ここにファイルや\nフォルダーをドロップ")
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "QLabel { border: 2px dashed palette(mid); border-radius: 8px;"
            " color: palette(placeholder-text); font-size: 15px; padding: 24px; }"
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.settings = QSettings("kenjiiida", "kiwami")
        self.infos = {}  # path -> FileInfo

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)

        # 空のときは案内、ファイルがあるときは一覧
        self.stack = QStackedWidget()
        self.stack.addWidget(DropHint())
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["名前", "種類"])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.setColumnWidth(0, 200)
        self.list.currentItemChanged.connect(self._show_current)
        self.stack.addWidget(self.list)
        layout.addWidget(self.stack, 1)

        # 詳細欄
        detail = QWidget()
        form = QFormLayout(detail)
        form.setContentsMargins(0, 4, 0, 4)
        form.setLabelAlignment(Qt.AlignRight)
        self.fields = {}
        for key, label in [
            ("name", "名前"),
            ("folder", "場所"),
            ("size", "大きさ"),
            ("mtime", "更新日時"),
            ("description", "中身の種類"),
            ("mime", "MIME"),
            ("encoding", "文字コード"),
            ("current_ext", "今の拡張子"),
            ("candidate_exts", "中身から見た拡張子"),
        ]:
            value = QLabel("")
            value.setWordWrap(True)
            value.setTextFormat(Qt.PlainText)  # ファイル名の < > をタグと誤解しないように
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(label + "：", value)
            self.fields[key] = value
        layout.addWidget(detail)

        # 下のボタン列
        bottom = QHBoxLayout()
        open_button = QPushButton("開く…")
        open_button.clicked.connect(self.open_dialog)
        clear_button = QPushButton("クリア")
        clear_button.clicked.connect(self.clear)
        self.on_top = QCheckBox("常に最前面")
        self.on_top.setChecked(self.settings.value("on_top", True, type=bool))
        self.on_top.toggled.connect(self._set_on_top)
        bottom.addWidget(open_button)
        bottom.addWidget(clear_button)
        bottom.addStretch(1)
        bottom.addWidget(self.on_top)
        layout.addLayout(bottom)

        self.setCentralWidget(central)

        open_action = QAction(self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.open_dialog)
        self.addAction(open_action)

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(440, 480)
        self._apply_on_top(self.on_top.isChecked())
        self._show_info(None)

    # ---- 常に最前面 ----
    def _apply_on_top(self, on):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)

    def _set_on_top(self, on):
        self.settings.setValue("on_top", on)
        self._apply_on_top(on)
        self.show()  # フラグを変えると一度隠れるので出し直す

    # ---- ドラッグ＆ドロップ ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    # ---- ファイルの追加 ----
    def open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "判別するファイルを選ぶ")
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths):
        last_item = None
        for p in paths:
            info = inspect(p)
            existing = self._find_item(info.path)
            if existing is None:
                existing = QTreeWidgetItem(self.list)
                existing.setData(0, PATH_ROLE, info.path)
            existing.setText(0, info.name)
            existing.setText(1, _short_type(info))
            existing.setToolTip(0, info.path)
            existing.setToolTip(1, info.error or info.description)
            self.infos[info.path] = info
            last_item = existing
        if last_item is not None:
            self.stack.setCurrentWidget(self.list)
            self.list.setCurrentItem(last_item)
            self._show_info(self.infos[last_item.data(0, PATH_ROLE)])

    def _find_item(self, path):
        for i in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(i)
            if item.data(0, PATH_ROLE) == path:
                return item
        return None

    def clear(self):
        self.list.clear()
        self.infos.clear()
        self.stack.setCurrentIndex(0)
        self._show_info(None)

    # ---- 詳細表示 ----
    def _show_current(self, current, _previous):
        if current is None:
            self._show_info(None)
        else:
            self._show_info(self.infos.get(current.data(0, PATH_ROLE)))

    def _show_info(self, info):
        f = self.fields
        if info is None:
            for label in f.values():
                label.setText("")
                label.setStyleSheet("")
            return
        f["name"].setText(info.name)
        f["folder"].setText(os.path.dirname(info.path))
        f["size"].setText(format_size(info.size) if info.kind == "file" else "")
        f["mtime"].setText(info.mtime.strftime("%Y-%m-%d %H:%M:%S") if info.mtime else "")
        if info.error:
            f["description"].setStyleSheet("color: #c62828;")
            f["description"].setText(info.error)
        else:
            f["description"].setStyleSheet("")
            f["description"].setText(info.description)
        f["mime"].setText(info.mime)
        is_text = info.mime.startswith("text/")
        f["encoding"].setText(info.encoding if is_text else "")
        if info.kind == "file":
            f["current_ext"].setText("." + info.current_ext if info.current_ext else "（なし）")
            f["candidate_exts"].setText(
                "  ".join("." + e for e in info.candidate_exts) if info.candidate_exts else "（判別できず）"
            )
        else:
            f["current_ext"].setText("")
            f["candidate_exts"].setText("")
        self._fit_labels()

    def _fit_labels(self):
        # 折り返した文字が切れないよう、各欄の高さを内容に合わせる
        for label in self.fields.values():
            label.setMinimumHeight(max(0, label.heightForWidth(label.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_labels()

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
