"""小窓（メインウィンドウ）。ファイルをドロップすると判別結果を表示する。"""

import os

from PySide6.QtCore import QItemSelectionModel, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QIcon, QKeySequence
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
from . import nautilus, packer
from .dialogs import (
    BulkFixDialog,
    CompressDialog,
    HistoryDialog,
    ModeDialog,
    PasswordDialog,
    RenameDialog,
    TimesDialog,
    about_text,
)
from .fileops import (
    GroupOp,
    History,
    RenameOp,
    bulk_rename,
    bulk_set_mode,
    bulk_set_times,
    UndoConflict,
    UndoError,
    change_extension,
    format_mode,
    rename,
    set_mode,
    set_times,
)
from .jobs import run_job
from .recent import RecentFiles
from .typesdb import TypeDB
from .viewer import ViewerWindow

PATH_ROLE = Qt.UserRole
TXT = "__txt__"  # 「.txt にする」の印
ICON_PATH = os.path.join(os.path.dirname(__file__), "data", "kiwami.svg")
MANY = 100  # これより多いときは、進み具合の窓を出して裏で調べる
FOLDER_LIMIT = 20000  # フォルダーの中身を一覧に入れる上限

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


def _needs_fix(info):
    """拡張子が違う・ないもので、直す候補があるもの。"""
    return info.status in (MISMATCH, NOEXT) and bool(info.suggestions) and info.suggestions[0] != ""


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
        self.items = {}  # path -> 一覧の行
        self.history = History()
        self.recent = RecentFiles()
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

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
        self.bulk_fix_button = QPushButton()
        self.bulk_fix_button.setToolTip(
            "拡張子が中身と違うもの・拡張子がないものを、確認してからまとめて直します\n"
            "（複数選んでいれば選んだものだけ、そうでなければ一覧の全部が対象）"
        )
        self.bulk_fix_button.clicked.connect(self.bulk_fix)
        pack.addWidget(self.extract_button)
        pack.addWidget(self.compress_button)
        pack.addStretch(1)
        pack.addWidget(self.bulk_fix_button)
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
        self.fix_label = QLabel("正しい拡張子：")
        fix_layout.addWidget(self.fix_label)
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
        history_button = QPushButton("履歴…")
        history_button.setToolTip("最近扱ったファイルを一覧に呼び戻します")
        history_button.clicked.connect(self.show_history)
        more_button = QPushButton("その他")
        more_menu = QMenu(more_button)
        more_menu.addAction("自分用の辞書を開く…", self.open_user_dict)
        self.nautilus_action = more_menu.addAction("ファイル画面の右クリックに登録する")
        self.nautilus_action.setCheckable(True)
        self.nautilus_action.setToolTip("右クリック →「スクリプト」→「極み窓で調べる」で開けるようにします")
        self.nautilus_action.toggled.connect(self._toggle_nautilus)
        more_menu.aboutToShow.connect(self._sync_nautilus_action)
        more_menu.addSeparator()
        more_menu.addAction("このソフトについて", self.show_about)
        more_button.setMenu(more_menu)
        self.on_top = QCheckBox("常に最前面")
        self.on_top.setChecked(self.settings.value("on_top", True, type=bool))
        self.on_top.toggled.connect(self._set_on_top)
        bottom.addWidget(open_button)
        bottom.addWidget(clear_button)
        bottom.addWidget(history_button)
        bottom.addWidget(more_button)
        bottom.addStretch(1)
        bottom.addWidget(self.on_top)
        layout.addLayout(bottom)

        self.setCentralWidget(central)

        for keys, slot in (
            (QKeySequence.Open, self.open_dialog),
            (QKeySequence(Qt.Key_F2), self.rename_current),
            (QKeySequence.Undo, self.undo),
            (QKeySequence(Qt.Key_Delete), self.remove_selected),
        ):
            action = QAction(self)
            action.setShortcut(keys)
            action.triggered.connect(slot)
            self.addAction(action)

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(470, 660)
        self._apply_on_top(self.on_top.isChecked())
        self.list.itemSelectionChanged.connect(self._update_selection_buttons)
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

    def add_paths(self, paths, remember=True):
        self._reload_dict()
        paths = [p for p in dict.fromkeys(os.path.abspath(p) for p in paths)]
        if len(paths) > MANY:
            def work(progress, cancel):
                infos = []
                for i, p in enumerate(paths):
                    if cancel.is_set():
                        break
                    infos.append(inspect(p, self.db))
                    if i % 20 == 0:
                        progress(i / len(paths), os.path.basename(p))
                return infos
            infos, error = run_job(self, f"{len(paths):,} 個を調べています", work)
            if error is not None:
                self._report_error("調べられませんでした。", error)
                return
        else:
            infos = [inspect(p, self.db) for p in paths]
        if not infos:
            return
        self.list.setUpdatesEnabled(False)
        last_item = None
        for info in infos:
            last_item = self._set_info(info)
        self.list.setUpdatesEnabled(True)
        if remember and self.settings.value("keep_history", True, type=bool):
            self.recent.add([i.path for i in infos])
        self.stack.setCurrentWidget(self.list)
        self.list.setCurrentItem(last_item)
        self._show_info(self.infos[last_item.data(0, PATH_ROLE)])
        self._update_selection_buttons()
        if len(infos) < len(paths):
            self._show_message(f"中止したため、{len(infos):,} 個だけ一覧に入れました")

    def add_folder_contents(self, folder):
        """フォルダーの中のファイル（サブフォルダーの中も）を一覧に入れる。"""
        found = []
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
            for f in sorted(files):
                found.append(os.path.join(root, f))
                if len(found) >= FOLDER_LIMIT:
                    break
            if len(found) >= FOLDER_LIMIT:
                break
        if not found:
            self._show_message("このフォルダーにはファイルがありません")
            return
        if len(found) >= FOLDER_LIMIT:
            QMessageBox.information(
                self, APP_NAME, f"ファイルが多いため、最初の {FOLDER_LIMIT:,} 個だけ一覧に入れます。")
        self.add_paths(found, remember=False)
        self._show_message(f"「{os.path.basename(folder)}」の中の {len(found):,} 個を一覧に入れました")

    def _set_info(self, info, old_path=None):
        """一覧の行を作るか更新して、その行を返す。"""
        item = self._find_item(old_path or info.path)
        if item is None:
            item = self._find_item(info.path)
        if item is None:
            item = QTreeWidgetItem(self.list)
        if old_path:
            self.infos.pop(old_path, None)
            self.items.pop(old_path, None)
            # 新しい名前の行が別にあれば（古い情報なので）消す
            other = self._find_item(info.path)
            if other is not None and other is not item:
                self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(other))
        self.items[info.path] = item
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
        return self.items.get(path)

    def clear(self):
        self.list.clear()
        self.infos.clear()
        self.items.clear()
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
        if new_ext == TXT:
            self._rename_to_txt(fresh)
            return
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

    def _rename_to_txt(self, fresh):
        if not fresh.txt_name:
            item = self._set_info(fresh)
            self.list.setCurrentItem(item)
            self._show_info(fresh)
            self._show_message("ファイルの状態が変わっていたため、判定し直しました")
            return
        try:
            new_path, op = rename(fresh.path, fresh.txt_name)
        except FileExistsError:
            QMessageBox.warning(self, APP_NAME, f"「{fresh.txt_name}」がすでにあるため、変更しませんでした。")
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

    def _targets(self):
        """属性・日時を変える対象。複数選んでいれば選んだもの全部、そうでなければ今の行。"""
        selected = [self.infos.get(i.data(0, PATH_ROLE)) for i in self.list.selectedItems()]
        selected = [i for i in selected if i is not None and i.kind in ("file", "dir")]
        if len(selected) > 1:
            fresh = [inspect(i.path, self.db) for i in selected]
            return [i for i in fresh if i.kind in ("file", "dir")]
        info = self._fresh_current()
        return [info] if info is not None else []

    def _report_failures(self, title, failed):
        if not failed:
            return
        shown = "\n".join(f"・{os.path.basename(p)}：{why}" for p, why in failed[:15])
        more = f"\nほか {len(failed) - 15} 個" if len(failed) > 15 else ""
        QMessageBox.warning(self, APP_NAME, f"{title}\n次の {len(failed)} 個は変更できませんでした:\n{shown}{more}")

    def change_mode_current(self):
        targets = self._targets()
        if not targets:
            return
        if any(t.kind == "symlink" for t in targets):
            self._show_message("シンボリックリンクの属性は変更できません")
            return
        dialog = ModeDialog(targets[0], self, count=len(targets))
        if dialog.exec() != ModeDialog.Accepted:
            return
        if len(targets) == 1:
            try:
                op = set_mode(targets[0].path, dialog.mode())
            except (OSError, ValueError) as e:
                self._warn("属性を変更できませんでした。", e)
                return
            if op is None:
                return
            self.history.push(op)
            self._refresh_path(targets[0].path)
            self._show_message(f"属性を {format_mode(op.new_mode)} にしました")
            return
        group, failed = bulk_set_mode([t.path for t in targets], dialog.mode())
        self.history.push(group)
        self._refresh_paths([t.path for t in targets])
        self._show_message(f"{len(group.ops)} 個の属性を {format_mode(dialog.mode())} にしました")
        self._report_failures("属性をまとめて変更しました。", failed)

    def change_times_current(self):
        targets = self._targets()
        if not targets:
            return
        first = targets[0]
        try:
            st = os.lstat(first.path)
        except OSError as e:
            self._warn("日時を読み取れませんでした。", e)
            return
        dialog = TimesDialog(first, st.st_atime_ns, st.st_mtime_ns, self, count=len(targets))
        if dialog.exec() != TimesDialog.Accepted:
            return
        if len(targets) == 1:
            try:
                op = set_times(first.path, *dialog.times_ns())
            except (OSError, ValueError, OverflowError) as e:
                self._warn("日時を変更できませんでした。", e)
                return
            if op is None:
                return
            self.history.push(op)
            self._refresh_path(first.path)
            self._show_message("日時を変更しました")
            return
        atime, mtime = dialog.changed_ns()
        if atime is None and mtime is None:
            return
        group, failed = bulk_set_times([t.path for t in targets], atime, mtime)
        self.history.push(group)
        self._refresh_paths([t.path for t in targets])
        self._show_message(f"{len(group.ops)} 個の日時を変更しました")
        self._report_failures("日時をまとめて変更しました。", failed)

    # ---- 拡張子をまとめて直す ----
    def _bulk_candidates(self):
        selected = [self.infos.get(i.data(0, PATH_ROLE)) for i in self.list.selectedItems()]
        pool = selected if len(selected) > 1 else list(self.infos.values())
        return [i for i in pool if i is not None and (_needs_fix(i) or i.txt_name)]

    def bulk_fix(self):
        self._reload_dict()
        fresh = [inspect(i.path, self.db) for i in self._bulk_candidates()]
        fresh = [i for i in fresh if _needs_fix(i) or i.txt_name]
        if not fresh:
            self._refresh_paths([i.path for i in self._bulk_candidates()])
            self._show_message("直すものはありません")
            return
        plan = []
        for i in fresh:
            if _needs_fix(i):
                plan.append((i.path, i.name, name_with_extension(i.name, i.suggestions[0]), i.type_name, True))
            else:
                # テキストを .txt にするのは任意なので、はじめはチェックを外しておく
                plan.append((i.path, i.name, i.txt_name, "テキスト（任意）", False))
        dialog = BulkFixDialog(plan, self)
        if dialog.exec() != BulkFixDialog.Accepted:
            return
        chosen = set(dialog.chosen())
        targets = {p: new for p, _, new, _, _ in plan}
        group, moved, failed = bulk_rename(
            [(p, targets[p]) for p, *_ in plan if p in chosen], label="拡張子をまとめて直す")
        self.history.push(group)
        for old, new in moved.items():
            self._path_changed(old, new, select=False)
        self._update_selection_buttons()
        self._show_message(f"{len(moved)} 個の拡張子を直しました（「元に戻す」でまとめて戻せます）")
        self._report_failures(f"{len(moved)} 個の拡張子を直しました。", failed)

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
        if isinstance(op, GroupOp):
            for sub, sub_path in path:
                if isinstance(sub, RenameOp):
                    self._path_changed(sub.new_path, sub.old_path, select=False)
                else:
                    self._refresh_paths([sub_path])
            self._update_selection_buttons()
            if op.errors:
                shown = "\n".join("・" + e for e in op.errors[:15])
                QMessageBox.warning(self, APP_NAME, f"一部は元に戻せませんでした:\n{shown}")
        elif isinstance(op, RenameOp):
            self._path_changed(op.new_path, op.old_path)
        else:
            self._refresh_path(path)
        self._show_message(f"元に戻しました：{op.describe()}")

    # ---- 一覧の更新 ----
    def _path_changed(self, old, new, select=True):
        """名前が変わったファイル（フォルダーなら中のファイルも）の行を付け替える。"""
        self.recent.rename(old, new)
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
        if current is not None and select:
            self.list.setCurrentItem(current)
            self._show_info(self.infos[new])
        elif self.list.currentItem() is not None:
            self._show_current(self.list.currentItem(), None)
        self._update_undo_button()
        self._update_selection_buttons()

    def _refresh_paths(self, paths):
        for path in paths:
            if path in self.infos:
                self._set_info(inspect(path, self.db))
        self._show_current(self.list.currentItem(), None)
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
        if not item.isSelected():
            self.list.setCurrentItem(item)
        else:
            # 複数選んだままにする
            self.list.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
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
        if info is not None and info.kind == "dir":
            menu.addAction("フォルダーの中身を一覧に入れる", lambda: self.add_folder_contents(info.path))
        menu.addSeparator()
        menu.addAction("一覧から外す（Delete）", self.remove_selected)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _remove_item(self, item):
        path = item.data(0, PATH_ROLE)
        self.infos.pop(path, None)
        self.items.pop(path, None)
        self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(item))
        if not self.infos:
            self.clear()

    def remove_selected(self):
        for item in list(self.list.selectedItems()):
            self._remove_item(item)
        self._update_selection_buttons()

    def _update_selection_buttons(self):
        """複数選んだときのボタンの文字と、まとめて直すボタンの表示。"""
        count = len([i for i in self.list.selectedItems()])
        multi = count > 1
        self.rename_button.setEnabled(not multi and self._current_info() is not None
                                      and self._current_info().kind != "missing")
        self.mode_button.setText(f"属性（{count}）" if multi else "属性")
        self.times_button.setText(f"日時（{count}）" if multi else "日時")
        n = len(self._bulk_candidates())
        self.bulk_fix_button.setText(f"拡張子をまとめて直す（{n}）")
        self.bulk_fix_button.setVisible(n > 1 or (n == 1 and multi))
        self._update_pack_buttons()

    # ---- 履歴・情報 ----
    def show_history(self):
        dialog = HistoryDialog(self.recent, self.settings.value("keep_history", True, type=bool), self)
        accepted = dialog.exec() == HistoryDialog.Accepted
        self.settings.setValue("keep_history", dialog.keep_history())
        if not dialog.keep_history():
            self.recent.clear()
        if accepted:
            paths = dialog.selected_paths()
            if paths:
                self.add_paths(paths)

    def _sync_nautilus_action(self):
        self.nautilus_action.blockSignals(True)
        self.nautilus_action.setChecked(nautilus.is_installed())
        self.nautilus_action.blockSignals(False)

    def _toggle_nautilus(self, on):
        try:
            if on:
                nautilus.install()
                QMessageBox.information(
                    self, APP_NAME,
                    "登録しました。\nファイル画面でファイルを右クリック →「スクリプト」→"
                    f"「{nautilus.SCRIPT_NAME}」で、極み窓に送れます。\n"
                    "（メニューに出ないときは、ファイル画面を一度閉じて開き直してください）")
            else:
                nautilus.uninstall()
                self._show_message("右クリックメニューから外しました")
        except OSError as e:
            self._warn("登録を変更できませんでした。", e)
        self._sync_nautilus_action()

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle(f"{APP_NAME}について")
        box.setTextFormat(Qt.RichText)
        box.setText(about_text())
        box.setTextInteractionFlags(Qt.TextBrowserInteraction)  # リンクを押せるように
        if os.path.exists(ICON_PATH):
            box.setIconPixmap(QIcon(ICON_PATH).pixmap(64, 64))
        box.exec()

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

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
        if info is not None and info.txt_name:
            self.fix_combo.clear()
            self.fix_combo.addItem(info.txt_name, TXT)
            self.fix_label.setText("新しい名前：")
            self.fix_button.setText(".txt にする")
            self.fix_button.setToolTip("テキストなので、.txt にしておくと他のソフトでも開きやすくなります（任意）")
            self.fix_row.show()
            return
        self.fix_label.setText("正しい拡張子：")
        self.fix_button.setToolTip("")
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
        self._update_selection_buttons()
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
