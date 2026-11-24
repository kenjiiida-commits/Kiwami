"""小窓（メインウィンドウ）。ファイルをドロップすると判別結果を表示する。"""

import os

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .detect import (
    MAYBE,
    MISMATCH,
    NOEXT,
    NOEXT_OK,
    OK,
    OK_TEXT,
    UNKNOWN,
    format_size,
    inspect,
    name_with_extension,
)
from .fileops import change_extension
from .typesdb import TypeDB

PATH_ROLE = Qt.UserRole

RED = "#c62828"
AMBER = "#9a6700"
GREEN = "#2e7d32"

# 判定ごとの帯の色（文字色, 背景色）
BANNER_STYLE = {
    OK: (GREEN, "#e8f5e9"),
    OK_TEXT: (GREEN, "#e8f5e9"),
    NOEXT_OK: (GREEN, "#e8f5e9"),
    MISMATCH: (RED, "#ffebee"),
    NOEXT: (AMBER, "#fff8e1"),
    UNKNOWN: ("#555555", "#eeeeee"),
    MAYBE: ("#555555", "#eeeeee"),
}


def _type_text(info):
    return info.type_name or info.description.split(",")[0]


def _short_type(info):
    if info.error:
        return "エラー"
    text = _type_text(info)
    return text if len(text) <= 40 else text[:39] + "…"


def _ext_label(ext):
    return "." + ext if ext else "（拡張子なし）"


def _banner_text(info):
    name = _type_text(info)
    if info.status == OK:
        return f"✔ 拡張子は中身（{name}）と合っています"
    if info.status == OK_TEXT:
        return f"✔ 中身は{name}です（テキストなので拡張子は自由）"
    if info.status == NOEXT_OK:
        return f"✔ {name}は拡張子なしが普通です"
    if info.status == MISMATCH:
        return f"✖ 拡張子が中身と違います。中身は{name}です"
    if info.status == NOEXT:
        return f"！ 拡張子がありません。中身は{name}です"
    if info.status == MAYBE:
        return f"？ 中身は{name}かもしれませんが、確実ではありません"
    return "？ 中身の種類が分からないため、拡張子を判定できません"


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
        self.db = TypeDB()
        self.infos = {}  # path -> FileInfo

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 4)

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

        # 判定の帯
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.PlainText)
        layout.addWidget(self.banner)

        # 拡張子を直す列
        self.fix_row = QWidget()
        fix_layout = QHBoxLayout(self.fix_row)
        fix_layout.setContentsMargins(0, 0, 0, 0)
        fix_layout.addWidget(QLabel("正しい拡張子："))
        self.fix_combo = QComboBox()
        fix_layout.addWidget(self.fix_combo, 1)
        self.fix_button = QPushButton("この拡張子に直す")
        self.fix_button.clicked.connect(self.fix_extension)
        fix_layout.addWidget(self.fix_button)
        layout.addWidget(self.fix_row)

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
            ("type", "中身の種類"),
            ("description", "詳しい説明"),
            ("mime", "MIME"),
            ("encoding", "文字コード"),
            ("current_ext", "今の拡張子"),
            ("candidates", "中身に合う拡張子"),
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
        dict_button = QPushButton("辞書…")
        dict_button.setToolTip("自分用の辞書（形式の追加・日本語名の変更）を開きます")
        dict_button.clicked.connect(self.open_user_dict)
        self.on_top = QCheckBox("常に最前面")
        self.on_top.setChecked(self.settings.value("on_top", True, type=bool))
        self.on_top.toggled.connect(self._set_on_top)
        bottom.addWidget(open_button)
        bottom.addWidget(clear_button)
        bottom.addWidget(dict_button)
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
            self.resize(460, 560)
        self._apply_on_top(self.on_top.isChecked())
        self._show_info(None)
        self._report_dict_error()

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

    # ---- 辞書 ----
    def _report_dict_error(self):
        if self.db.user_error:
            self.statusBar().setStyleSheet(f"color: {RED};")
            self.statusBar().showMessage(self.db.user_error)
        else:
            self.statusBar().setStyleSheet("")

    def _reload_dict(self):
        if self.db.reload_if_changed():
            self._report_dict_error()
            # 辞書が変わったら、一覧のファイルも判定し直す
            self._refresh_all()

    def open_user_dict(self):
        try:
            path = self.db.ensure_user_file()
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"自分用辞書を作れませんでした。\n{e}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self._show_message(f"自分用辞書: {path}（保存すると次の判別から反映されます）")

    # ---- ファイルの追加 ----
    def open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "判別するファイルを選ぶ")
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths):
        self._reload_dict()
        last_item = None
        for p in paths:
            last_item = self._set_info(inspect(p, self.db))
        if last_item is not None:
            self.stack.setCurrentWidget(self.list)
            self.list.setCurrentItem(last_item)
            self._show_info(self.infos[last_item.data(0, PATH_ROLE)])

    def _set_info(self, info, old_path=None):
        """一覧の行を作るか更新して、その行を返す。"""
        item = self._find_item(old_path or info.path)
        if item is None:
            item = self._find_item(info.path)
        if item is None:
            item = QTreeWidgetItem(self.list)
        if old_path:
            self.infos.pop(old_path, None)
        item.setData(0, PATH_ROLE, info.path)
        item.setText(0, info.name)
        item.setText(1, _short_type(info))
        item.setToolTip(0, info.path)
        item.setToolTip(1, info.error or info.description)
        color = {MISMATCH: RED, NOEXT: AMBER}.get(info.status)
        brush = QBrush(QColor(color)) if color else QBrush()
        for col in (0, 1):
            item.setForeground(col, brush)
        self.infos[info.path] = info
        return item

    def _refresh_all(self):
        for path in list(self.infos):
            self._set_info(inspect(path, self.db))
        self._show_current(self.list.currentItem(), None)

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

    # ---- 拡張子を直す ----
    def _current_info(self):
        item = self.list.currentItem()
        return self.infos.get(item.data(0, PATH_ROLE)) if item else None

    def fix_extension(self):
        info = self._current_info()
        if info is None or self.fix_combo.currentIndex() < 0:
            return
        new_ext = self.fix_combo.currentData()
        # ドロップしてから中身や名前が変わっていないか確かめ直す
        self._reload_dict()
        fresh = inspect(info.path, self.db)
        if fresh.status not in (MISMATCH, NOEXT, MAYBE):
            item = self._set_info(fresh)
            self.list.setCurrentItem(item)
            self._show_info(fresh)
            self._show_message("ファイルの状態が変わっていたため、判定し直しました")
            return
        try:
            new_path = change_extension(fresh.path, new_ext)
        except FileExistsError:
            QMessageBox.warning(
                self,
                APP_NAME,
                f"同じ名前のファイルがすでにあるため、変更しませんでした。\n\n"
                f"{fresh.name} → {name_with_extension(fresh.name, new_ext)}",
            )
            return
        except (OSError, ValueError) as e:
            reason = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
            QMessageBox.warning(self, APP_NAME, f"名前を変更できませんでした。\n{reason}")
            return
        new_info = inspect(new_path, self.db)
        item = self._set_info(new_info, old_path=fresh.path)
        self.list.setCurrentItem(item)
        self._show_info(new_info)
        self._show_message(f"「{fresh.name}」→「{new_info.name}」に変更しました")

    def _show_message(self, text):
        self.statusBar().setStyleSheet("")
        self.statusBar().showMessage(text, 10000)

    # ---- 詳細表示 ----
    def _show_current(self, current, _previous):
        if current is None:
            self._show_info(None)
        else:
            self._show_info(self.infos.get(current.data(0, PATH_ROLE)))

    def _show_banner(self, info):
        if info is None or info.status is None:
            self.banner.hide()
            return
        fg, bg = BANNER_STYLE[info.status]
        self.banner.setStyleSheet(
            f"QLabel {{ color: {fg}; background: {bg}; border-radius: 6px;"
            " padding: 6px 8px; font-weight: bold; }"
        )
        self.banner.setText(_banner_text(info))
        self.banner.show()

    def _show_fix_row(self, info):
        if info is None or info.status not in (MISMATCH, NOEXT, MAYBE) or not info.suggestions:
            self.fix_row.hide()
            return
        self.fix_combo.clear()
        for ext in info.suggestions:
            self.fix_combo.addItem(_ext_label(ext), ext)
        self.fix_button.setText("拡張子を付ける" if info.status == NOEXT else "この拡張子に直す")
        self.fix_row.show()

    def _show_info(self, info):
        self._show_banner(info)
        self._show_fix_row(info)
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
            f["type"].setStyleSheet(f"color: {RED};")
            f["type"].setText(info.error)
        else:
            f["type"].setStyleSheet("")
            text = info.type_name or "（辞書にない形式）"
            if info.from_signature:
                text += "　※辞書の先頭バイト列で判別"
            f["type"].setText(text)
        f["description"].setText(info.description)
        f["mime"].setText(info.mime)
        is_text = info.mime.startswith("text/") or info.status == OK_TEXT
        f["encoding"].setText(info.encoding if is_text else "")
        if info.kind == "file" and not info.error:
            f["current_ext"].setText(_ext_label(info.current_ext))
            exts = [e for e in info.suggestions if e]
            if info.status == OK and info.current_ext:
                exts = [info.current_ext] + exts
            f["candidates"].setText("  ".join("." + e for e in exts) if exts else "（判別できず）")
        else:
            f["current_ext"].setText("")
            f["candidates"].setText("")
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
