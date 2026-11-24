"""小窓（メインウィンドウ）。ファイルをドロップすると判別結果を表示する。"""

import os

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QMenu,
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
from . import packer
from .dialogs import CompressDialog, ModeDialog, PasswordDialog, RenameDialog, TimesDialog
from .fileops import (
    History,
    RenameOp,
    UndoConflict,
    UndoError,
    change_extension,
    format_mode,
    rename,
    set_mode,
    set_times,
)
from .jobs import run_job
from .typesdb import TypeDB
from .viewer import ViewerWindow

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
        self.history = History()

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
        self.list.setSelectionMode(QTreeWidget.ExtendedSelection)  # 圧縮のため複数選べる
        self.list.setColumnWidth(0, 200)
        self.list.currentItemChanged.connect(self._show_current)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemDoubleClicked.connect(lambda *_: self.view_current())
        self.stack.addWidget(self.list)
        layout.addWidget(self.stack, 1)

        # 変更操作の列
        ops = QHBoxLayout()
        self.rename_button = QPushButton("名前変更")
        self.rename_button.setToolTip("名前を変える（F2）")
        self.rename_button.clicked.connect(self.rename_current)
        self.mode_button = QPushButton("属性")
        self.mode_button.setToolTip("読み書きなどの許可（パーミッション）を変える")
        self.mode_button.clicked.connect(self.change_mode_current)
        self.times_button = QPushButton("日時")
        self.times_button.setToolTip("更新日時・アクセス日時を変える")
        self.times_button.clicked.connect(self.change_times_current)
        self.view_button = QPushButton("中身を見る")
        self.view_button.setToolTip("テキスト・画像・16進・圧縮ファイルの中身を表示（ダブルクリックでも可）")
        self.view_button.clicked.connect(self.view_current)
        self.undo_button = QPushButton("元に戻す")
        self.undo_button.clicked.connect(self.undo)
        for b in (self.view_button, self.rename_button, self.mode_button, self.times_button):
            ops.addWidget(b)
        ops.addStretch(1)
        ops.addWidget(self.undo_button)
        layout.addLayout(ops)

        # 圧縮・解凍の列
        pack = QHBoxLayout()
        self.extract_button = QPushButton("解凍する")
        self.extract_button.setToolTip("同じ場所に新しいフォルダーを作って、その中へ出します")
        self.extract_button.clicked.connect(self.extract_current)
        self.compress_button = QPushButton("圧縮する…")
        self.compress_button.setToolTip("一覧で選んだもの（Ctrl や Shift で複数選べます）をまとめます")
        self.compress_button.clicked.connect(self.compress_selected)
        pack.addWidget(self.extract_button)
        pack.addWidget(self.compress_button)
        pack.addStretch(1)
        layout.addLayout(pack)
        self.list.itemSelectionChanged.connect(self._update_pack_buttons)

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
            ("mode", "属性"),
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

        for keys, slot in (
            (QKeySequence.Open, self.open_dialog),
            (QKeySequence(Qt.Key_F2), self.rename_current),
            (QKeySequence.Undo, self.undo),
        ):
            action = QAction(self)
            action.setShortcut(keys)
            action.triggered.connect(slot)
            self.addAction(action)

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(460, 560)
        self._apply_on_top(self.on_top.isChecked())
        self._show_info(None)
        self._update_undo_button()
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
            # 新しい名前の行が別にあれば（古い情報なので）消す
            other = self._find_item(info.path)
            if other is not None and other is not item:
                self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(other))
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
            new_path, op = change_extension(fresh.path, new_ext)
        except FileExistsError:
            QMessageBox.warning(
                self,
                APP_NAME,
                f"同じ名前のファイルがすでにあるため、変更しませんでした。\n\n"
                f"{fresh.name} → {name_with_extension(fresh.name, new_ext)}",
            )
            return
        except (OSError, ValueError) as e:
            self._warn("名前を変更できませんでした。", e)
            return
        self.history.push(op)
        self._path_changed(fresh.path, new_path)
        self._show_message(f"「{fresh.name}」→「{os.path.basename(new_path)}」に変更しました")

    # ---- 名前・属性・日時の変更 ----
    def _fresh_current(self):
        """選んでいるファイルを調べ直して返す。見つからなければ知らせて None。"""
        info = self._current_info()
        if info is None:
            return None
        fresh = inspect(info.path, self.db)
        if fresh.kind == "missing":
            self._set_info(fresh)
            self._show_info(fresh)
            self._show_message("ファイルが見つかりません（移動または削除されたようです）")
            return None
        return fresh

    def _warn(self, message, error):
        if isinstance(error, PermissionError):
            reason = "権限がありません（自分が所有者でない、または書き込み禁止の場所です）"
        elif isinstance(error, OSError) and error.strerror:
            reason = error.strerror
        else:
            reason = str(error)
        QMessageBox.warning(self, APP_NAME, f"{message}\n{reason}")

    def rename_current(self):
        info = self._fresh_current()
        if info is None:
            return
        dialog = RenameDialog(info.path, self)
        if dialog.exec() != RenameDialog.Accepted:
            return
        try:
            new_path, op = rename(info.path, dialog.new_name())
        except FileExistsError:
            QMessageBox.warning(self, APP_NAME, "同じ名前のファイルがすでにあるため、変更しませんでした。")
            return
        except (OSError, ValueError) as e:
            self._warn("名前を変更できませんでした。", e)
            return
        if op is None:
            return
        self.history.push(op)
        self._path_changed(info.path, new_path)
        self._show_message(f"「{info.name}」→「{os.path.basename(new_path)}」に変更しました")

    def change_mode_current(self):
        info = self._fresh_current()
        if info is None:
            return
        if info.kind == "symlink":
            self._show_message("シンボリックリンクの属性は変更できません")
            return
        dialog = ModeDialog(info, self)
        if dialog.exec() != ModeDialog.Accepted:
            return
        try:
            op = set_mode(info.path, dialog.mode())
        except (OSError, ValueError) as e:
            self._warn("属性を変更できませんでした。", e)
            return
        if op is None:
            return
        self.history.push(op)
        self._refresh_path(info.path)
        self._show_message(f"属性を {format_mode(op.new_mode)} にしました")

    def change_times_current(self):
        info = self._fresh_current()
        if info is None:
            return
        if info.kind == "symlink":
            self._show_message("シンボリックリンクの日時は変更できません")
            return
        try:
            st = os.lstat(info.path)
        except OSError as e:
            self._warn("日時を読み取れませんでした。", e)
            return
        dialog = TimesDialog(info, st.st_atime_ns, st.st_mtime_ns, self)
        if dialog.exec() != TimesDialog.Accepted:
            return
        try:
            op = set_times(info.path, *dialog.times_ns())
        except (OSError, ValueError, OverflowError) as e:
            self._warn("日時を変更できませんでした。", e)
            return
        if op is None:
            return
        self.history.push(op)
        self._refresh_path(info.path)
        self._show_message("日時を変更しました")

    # ---- 中身を見る ----
    def view_current(self):
        info = self._fresh_current()
        if info is None:
            return
        if info.kind != "file":
            self._show_message("中身を見られるのはファイルだけです")
            return
        try:
            with open(info.path, "rb"):
                pass
        except OSError as e:
            self._warn("ファイルを開けませんでした。", e)
            return
        ViewerWindow(info, self).show()

    # ---- 圧縮・解凍 ----
    def _selected_paths(self):
        paths = []
        for item in self.list.selectedItems():
            info = self.infos.get(item.data(0, PATH_ROLE))
            if info is not None and info.kind in ("file", "dir"):
                paths.append(info.path)
        return paths

    def _update_pack_buttons(self):
        info = self._current_info()
        self.extract_button.setEnabled(
            info is not None and info.kind == "file" and packer.can_extract(info.mime)
        )
        self.compress_button.setEnabled(bool(self._selected_paths()))

    def extract_current(self):
        info = self._fresh_current()
        if info is None:
            return
        if info.kind != "file" or not packer.can_extract(info.mime):
            self._show_message("このファイルは解凍できる形式ではありません")
            return
        password = None
        while True:
            result, error = run_job(
                self, f"解凍しています：{info.name}",
                lambda progress, cancel: packer.extract(info.path, info.mime, password, progress, cancel),
            )
            if isinstance(error, packer.PasswordNeeded):
                dialog = PasswordDialog(info.name, wrong=error.wrong, parent=self)
                if dialog.exec() != PasswordDialog.Accepted:
                    self._show_message("解凍をやめました")
                    return
                password = dialog.password()
                continue
            break
        if isinstance(error, packer.Cancelled):
            self._show_message("解凍を中止しました（途中まで出したものは消しました）")
            return
        if error is not None:
            self._report_error("解凍できませんでした。", error)
            return
        self._finish_pack(result, f"「{os.path.basename(result.path)}」に解凍しました")

    def compress_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        if len({os.path.dirname(p.rstrip("/")) for p in paths}) != 1:
            QMessageBox.warning(self, APP_NAME, "同じフォルダーにあるものだけ、まとめて圧縮できます。")
            return
        dialog = CompressDialog(paths, self)
        if dialog.exec() != CompressDialog.Accepted:
            return
        opts = dialog.options()
        result, error = run_job(
            self, f"圧縮しています：{opts['name']}",
            lambda progress, cancel: packer.create(paths, progress=progress, cancel=cancel, **opts),
        )
        if isinstance(error, packer.Cancelled):
            self._show_message("圧縮を中止しました（作りかけのファイルは消しました）")
            return
        if error is not None:
            self._report_error("圧縮できませんでした。", error)
            return
        self._finish_pack(result, f"「{os.path.basename(result.path)}」を作りました")

    def _report_error(self, message, error):
        if isinstance(error, packer.PackError):
            QMessageBox.warning(self, APP_NAME, f"{message}\n{error}")
        elif isinstance(error, OSError):
            self._warn(message, error)
        else:
            QMessageBox.warning(self, APP_NAME, f"{message}\n思いがけない問題が起きました: {error!r}")

    def _finish_pack(self, result, message):
        self.add_paths([result.path])
        if result.damaged:
            message += f"（壊れていた {len(result.damaged)} 個を除く）"
        self._show_message(message)
        if result.damaged:
            shown = "\n".join("・" + n for n in result.damaged[:15])
            more = f"\nほか {len(result.damaged) - 15} 個" if len(result.damaged) > 15 else ""
            QMessageBox.warning(
                self, APP_NAME,
                "圧縮ファイルの一部が壊れていました。\n"
                f"無事なファイルは「{os.path.basename(result.path)}」に取り出しました。\n\n"
                f"壊れていて取り出せなかったもの（{len(result.damaged)} 個）:\n{shown}{more}"
                + (f"\n\n{result.note}" if result.note else "")
                + "\n\n元の場所（スマホやクラウドなど）にまだ残っていれば、そこからもう一度取ってきてください。",
            )
            result.note = ""
        notes = []
        if result.note:
            notes.append(result.note)
        if result.skipped:
            shown = "\n".join("・" + n for n in result.skipped[:10])
            more = f"\nほか {len(result.skipped) - 10} 個" if len(result.skipped) > 10 else ""
            notes.append(
                f"安全のため、次の {len(result.skipped)} 個は入れていません"
                "（シンボリックリンクや、外の場所を指す名前など）:\n" + shown + more
            )
        if notes:
            QMessageBox.information(self, APP_NAME, message + "\n\n" + "\n\n".join(notes))

    # ---- 元に戻す ----
    def _update_undo_button(self):
        op = self.history.peek()
        self.undo_button.setEnabled(op is not None)
        self.undo_button.setToolTip(f"元に戻す（Ctrl+Z）：{op.describe()}" if op else "元に戻す操作はありません")

    def undo(self):
        op = self.history.peek()
        if op is None:
            return
        try:
            try:
                _, path = self.history.undo()
            except UndoConflict as e:
                answer = QMessageBox.question(
                    self, APP_NAME, f"{e}。\nそれでも元に戻しますか？\n\n{op.describe()}",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                _, path = self.history.undo(force=True)
        except UndoError as e:
            QMessageBox.warning(self, APP_NAME, f"元に戻せませんでした。\n{e}")
            self._update_undo_button()
            return
        except OSError as e:
            self._warn("元に戻せませんでした。", e)
            self._update_undo_button()
            return
        if isinstance(op, RenameOp):
            self._path_changed(op.new_path, op.old_path)
        else:
            self._refresh_path(path)
        self._show_message(f"元に戻しました：{op.describe()}")

    # ---- 一覧の更新 ----
    def _path_changed(self, old, new):
        """名前が変わったファイル（フォルダーなら中のファイルも）の行を付け替える。"""
        old_prefix = old.rstrip("/") + "/"
        current = None
        for path in list(self.infos):
            if path == old:
                moved = new
            elif path.startswith(old_prefix):
                moved = new.rstrip("/") + "/" + path[len(old_prefix):]
            else:
                continue
            item = self._set_info(inspect(moved, self.db), old_path=path)
            if path == old:
                current = item
        if current is not None:
            self.list.setCurrentItem(current)
            self._show_info(self.infos[new])
        self._update_undo_button()

    def _refresh_path(self, path):
        if path in self.infos:
            item = self._set_info(inspect(path, self.db))
            self.list.setCurrentItem(item)
            self._show_info(self.infos[path])
        self._update_undo_button()

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        self.list.setCurrentItem(item)
        info = self._current_info()
        menu = QMenu(self)
        view_action = menu.addAction("中身を見る", self.view_current)
        view_action.setEnabled(info is not None and info.kind == "file")
        menu.addSeparator()
        menu.addAction("名前の変更…", self.rename_current)
        mode_action = menu.addAction("属性の変更…", self.change_mode_current)
        times_action = menu.addAction("日時の変更…", self.change_times_current)
        if info is not None and info.kind in ("symlink", "missing"):
            mode_action.setEnabled(False)
            times_action.setEnabled(False)
        menu.addSeparator()
        menu.addAction("一覧から外す", lambda: self._remove_item(item))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _remove_item(self, item):
        path = item.data(0, PATH_ROLE)
        self.infos.pop(path, None)
        self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(item))
        if not self.infos:
            self.clear()

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
        usable = info is not None and info.kind not in ("missing",)
        self.view_button.setEnabled(usable and info.kind == "file")
        self.rename_button.setEnabled(usable)
        self.mode_button.setEnabled(usable and info.kind != "symlink")
        self.times_button.setEnabled(usable and info.kind != "symlink")
        self._update_pack_buttons()
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
        f["mode"].setText(
            f"{format_mode(info.mode)}　{info.owner}:{info.group}" if info.mtime and info.kind != "symlink" else ""
        )
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
        # 画面の配置が決まったあとで、もう一度高さを合わせる
        QTimer.singleShot(0, self._fit_labels)

    def _fit_labels(self):
        # 折り返した文字が切れないよう、各欄の高さを内容に合わせる
        for label in self.fields.values():
            width = label.width()
            if width < 50:
                continue  # まだ配置が決まっていない
            label.setMinimumHeight(0)  # 前のファイルの高さが残らないように一度戻す
            label.setMinimumHeight(max(0, label.heightForWidth(width)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_labels()

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
