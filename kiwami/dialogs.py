"""名前・属性・日時を変えるための小さなダイアログ。"""

import os

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QWidget,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .detect import split_name
from .fileops import check_name, format_mode

NS = 1_000_000_000


class RenameDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("名前の変更")
        self.folder = os.path.dirname(path)
        self.old_name = os.path.basename(path)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("新しい名前："))
        self.edit = QLineEdit(self.old_name)
        self.edit.setMinimumWidth(360)
        layout.addWidget(self.edit)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #c62828;")
        self.error.setTextFormat(Qt.PlainText)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("変更")
        self.buttons.button(QDialogButtonBox.Cancel).setText("やめる")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.edit.textChanged.connect(self._validate)
        # 拡張子より前だけを選んでおく（すぐ打ち直せるように）
        stem, ext = split_name(self.old_name)
        self.edit.setSelection(0, len(stem) if ext else len(self.old_name))
        self._validate(self.old_name)

    def _validate(self, text):
        reason = check_name(text)
        if not reason and text != self.old_name:
            target = os.path.join(self.folder, text)
            if os.path.lexists(target) and text.lower() != self.old_name.lower():
                reason = "同じ名前のファイルがすでにあります"
        self.error.setText(reason)
        ok = not reason and text != self.old_name
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)

    def new_name(self):
        return self.edit.text()


class ModeDialog(QDialog):
    """属性（パーミッション）を、チェックボックスか数字（644 など）で変える。"""

    WHO = [("所有者", 6), ("グループ", 3), ("その他", 0)]
    WHAT = [("読み取り", 4), ("書き込み", 2), ("実行", 1)]

    def __init__(self, info, parent=None, count=1):
        super().__init__(parent)
        self.setWindowTitle("属性の変更")
        self._updating = False
        layout = QVBoxLayout(self)
        title = QLabel(info.name if count == 1 else f"選んだ {count} 個をまとめて変更（最初の「{info.name}」の値を表示）")
        title.setWordWrap(True)
        title.setTextFormat(Qt.PlainText)
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        owner = QLabel(f"所有者：{info.owner}　グループ：{info.group}")
        owner.setTextFormat(Qt.PlainText)
        layout.addWidget(owner)

        grid = QGridLayout()
        what_labels = self.WHAT
        if info.kind == "dir":
            # フォルダーの「実行」は「中に入れる」という意味
            what_labels = [("一覧を見る", 4), ("中身を変える", 2), ("中に入る", 1)]
        for col, (label, _) in enumerate(what_labels, start=1):
            grid.addWidget(QLabel(label), 0, col, Qt.AlignCenter)
        self.boxes = {}
        for row, (who, shift) in enumerate(self.WHO, start=1):
            grid.addWidget(QLabel(who), row, 0)
            for col, (_, bit) in enumerate(self.WHAT, start=1):
                box = QCheckBox()
                box.setChecked(bool(info.mode & (bit << shift)))
                box.toggled.connect(self._boxes_changed)
                grid.addWidget(box, row, col, Qt.AlignCenter)
                self.boxes[(shift, bit)] = box
        layout.addLayout(grid)

        number_row = QHBoxLayout()
        number_row.addWidget(QLabel("数字で："))
        self.number = QLineEdit(f"{info.mode & 0o777:03o}")
        self.number.setMaxLength(3)
        self.number.setFixedWidth(60)
        self.number.setValidator(QRegularExpressionValidator(QRegularExpression("[0-7]{0,3}")))
        self.number.textEdited.connect(self._number_changed)
        number_row.addWidget(self.number)
        self.preview = QLabel(format_mode(info.mode))
        number_row.addWidget(self.preview)
        number_row.addStretch(1)
        layout.addLayout(number_row)

        note = QLabel("※ 自分が所有者でないファイルは変更できないことがあります")
        note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("変更")
        buttons.button(QDialogButtonBox.Cancel).setText("やめる")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def mode(self):
        value = 0
        for (shift, bit), box in self.boxes.items():
            if box.isChecked():
                value |= bit << shift
        return value

    def _boxes_changed(self):
        if self._updating:
            return
        m = self.mode()
        self.number.setText(f"{m:03o}")
        self.preview.setText(format_mode(m))

    def _number_changed(self, text):
        if len(text) != 3:
            return
        m = int(text, 8)
        self._updating = True
        for (shift, bit), box in self.boxes.items():
            box.setChecked(bool(m & (bit << shift)))
        self._updating = False
        self.preview.setText(format_mode(m))


class _TimeRow:
    def __init__(self, grid, row, label, ns):
        self.original_ns = ns
        grid.addWidget(QLabel(label), row, 0)
        self.edit = QDateTimeEdit()
        self.edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.edit.setCalendarPopup(True)
        self.original = QDateTime.fromSecsSinceEpoch(ns // NS)
        self.edit.setDateTime(self.original)
        grid.addWidget(self.edit, row, 1)
        now_button = QPushButton("今")
        now_button.setToolTip("今の日時にする")
        now_button.clicked.connect(lambda: self.edit.setDateTime(QDateTime.currentDateTime()))
        grid.addWidget(now_button, row, 2)

    def value_ns(self):
        # 触っていなければ、秒より細かい部分も含めて元のまま
        if self.edit.dateTime() == self.original:
            return self.original_ns
        return self.edit.dateTime().toSecsSinceEpoch() * NS


class TimesDialog(QDialog):
    def __init__(self, info, atime_ns, mtime_ns, parent=None, count=1):
        super().__init__(parent)
        self.setWindowTitle("日時の変更")
        layout = QVBoxLayout(self)
        title = QLabel(info.name if count == 1 else
                       f"選んだ {count} 個をまとめて変更（最初の「{info.name}」の値を表示。"
                       "触らなかった方の日時は、それぞれのファイルのまま）")
        title.setWordWrap(True)
        title.setTextFormat(Qt.PlainText)
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        grid = QGridLayout()
        self.mtime = _TimeRow(grid, 0, "更新日時", mtime_ns)
        self.atime = _TimeRow(grid, 1, "アクセス日時", atime_ns)
        layout.addLayout(grid)
        same = QPushButton("アクセス日時を更新日時と同じにする")
        same.clicked.connect(lambda: self.atime.edit.setDateTime(self.mtime.edit.dateTime()))
        layout.addWidget(same)
        note = QLabel("※ 作成日時は Linux の仕組み上、変更できません")
        note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("変更")
        buttons.button(QDialogButtonBox.Cancel).setText("やめる")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def times_ns(self):
        return self.atime.value_ns(), self.mtime.value_ns()

    def changed_ns(self):
        """変えた方だけ値を返し、触らなかった方は None。"""
        a, m = self.times_ns()
        return (None if a == self.atime.original_ns else a,
                None if m == self.mtime.original_ns else m)


# ------------------------------------------------------------------ 圧縮・解凍

class PasswordDialog(QDialog):
    """解凍のときのパスワード入力。"""

    def __init__(self, archive_name, wrong=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("パスワード")
        layout = QVBoxLayout(self)
        title = QLabel(f"「{archive_name}」はパスワード付きです。")
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        if wrong:
            warn = QLabel("パスワードが違います。もう一度入力してください。")
            warn.setStyleSheet("color: #c62828;")
            layout.addWidget(warn)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setMinimumWidth(300)
        layout.addWidget(self.edit)
        show = QCheckBox("パスワードを表示する")
        show.toggled.connect(lambda on: self.edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        layout.addWidget(show)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("解凍")
        buttons.button(QDialogButtonBox.Cancel).setText("やめる")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setEnabled(False)
        self.edit.textChanged.connect(lambda t: self.ok.setEnabled(bool(t)))

    def password(self):
        return self.edit.text()


class CompressDialog(QDialog):
    """形式・名前・パスワードを決めて圧縮する。"""

    def __init__(self, paths, parent=None):
        from . import packer  # 循環を避けるためここで読む

        super().__init__(parent)
        self.packer = packer
        self.paths = paths
        self.setWindowTitle("圧縮")
        layout = QVBoxLayout(self)
        count = QLabel(
            f"{os.path.basename(paths[0].rstrip('/'))} をまとめます" if len(paths) == 1
            else f"選んだ {len(paths)} 個をまとめます"
        )
        count.setTextFormat(Qt.PlainText)
        count.setStyleSheet("font-weight: bold;")
        layout.addWidget(count)

        grid = QGridLayout()
        grid.addWidget(QLabel("形式："), 0, 0)
        self.fmt = QComboBox()
        for key, (label, _, _) in packer.FORMATS.items():
            self.fmt.addItem(label, key)
        grid.addWidget(self.fmt, 0, 1)
        grid.addWidget(QLabel("ファイル名："), 1, 0)
        self.name = QLineEdit()
        self.name.setMinimumWidth(320)
        grid.addWidget(self.name, 1, 1)
        layout.addLayout(grid)
        where = QLabel(f"保存先：{os.path.dirname(paths[0].rstrip('/'))}（同じ名前があれば番号を付けます）")
        where.setTextFormat(Qt.PlainText)
        where.setWordWrap(True)
        where.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(where)

        self.use_pw = QCheckBox("パスワードを付ける")
        layout.addWidget(self.use_pw)
        self.pw_box = QWidget()
        pw = QGridLayout(self.pw_box)
        pw.setContentsMargins(20, 0, 0, 0)
        pw.addWidget(QLabel("パスワード："), 0, 0)
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.Password)
        pw.addWidget(self.pw1, 0, 1)
        pw.addWidget(QLabel("もう一度："), 1, 0)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        pw.addWidget(self.pw2, 1, 1)
        self.show_pw = QCheckBox("パスワードを表示する")
        self.show_pw.toggled.connect(self._toggle_show)
        pw.addWidget(self.show_pw, 2, 1)
        self.zip_method_label = QLabel("暗号の方式：")
        pw.addWidget(self.zip_method_label, 3, 0)
        self.zip_method = QComboBox()
        self.zip_method.addItem("AES-256（安全。Windows の標準機能では開けず、7-Zip などが必要）", False)
        self.zip_method.addItem("ZipCrypto（Windows の標準機能で開ける。ただし破られやすい）", True)
        pw.addWidget(self.zip_method, 3, 1)
        self.hide_names = QCheckBox("ファイル名も隠す（パスワードなしでは中身の一覧も見えない）")
        pw.addWidget(self.hide_names, 4, 1)
        layout.addWidget(self.pw_box)

        self.error = QLabel("")
        self.error.setTextFormat(Qt.PlainText)
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #c62828;")
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("圧縮")
        buttons.button(QDialogButtonBox.Cancel).setText("やめる")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._name_edited = False
        self.name.textEdited.connect(lambda _: setattr(self, "_name_edited", True))
        self.fmt.currentIndexChanged.connect(self._format_changed)
        for w in (self.name, self.pw1, self.pw2):
            w.textChanged.connect(self._validate)
        self.use_pw.toggled.connect(self._update_pw)
        self._format_changed()

    def _toggle_show(self, on):
        mode = QLineEdit.Normal if on else QLineEdit.Password
        self.pw1.setEchoMode(mode)
        self.pw2.setEchoMode(mode)

    def _format_changed(self):
        key = self.fmt.currentData()
        ext = self.packer.FORMATS[key][1]
        if not self._name_edited:
            self.name.setText(self.packer.default_archive_name(self.paths, key))
        else:
            # 自分で付けた名前は残し、拡張子だけ付け替える
            stem = self.name.text()
            for k in self.packer.FORMATS.values():
                if stem.lower().endswith("." + k[1]):
                    stem = stem[: -len(k[1]) - 1]
                    break
            self.name.setText(f"{stem}.{ext}")
        can_pw = self.packer.FORMATS[key][2]
        self.use_pw.setEnabled(can_pw)
        if not can_pw:
            self.use_pw.setChecked(False)
        self.use_pw.setToolTip("" if can_pw else "この形式はパスワードを付けられません（zip か 7z を選んでください）")
        self._update_pw()

    def _update_pw(self):
        key = self.fmt.currentData()
        self.pw_box.setVisible(self.use_pw.isChecked())
        self.zip_method_label.setVisible(key == "zip")
        self.zip_method.setVisible(key == "zip")
        self.hide_names.setVisible(key == "7z")
        self._validate()
        self.adjustSize()

    def _validate(self):
        from .fileops import check_name

        reason = check_name(self.name.text())
        if not reason and self.use_pw.isChecked():
            p1, p2 = self.pw1.text(), self.pw2.text()
            if not p1:
                reason = "パスワードを入力してください"
            elif p1 != p2:
                reason = "2つのパスワードが一致しません"
            elif self.fmt.currentData() == "zip":
                reason = self.packer.zip_password_problem(p1)
        self.error.setText(reason)
        self.ok.setEnabled(not reason)

    def options(self):
        use = self.use_pw.isChecked()
        return {
            "name": self.name.text(),
            "fmt": self.fmt.currentData(),
            "password": self.pw1.text() if use else None,
            "zip_crypto": bool(self.zip_method.currentData()) if use else False,
            "hide_names": self.hide_names.isChecked() if use else False,
        }


# ------------------------------------------------------------------ まとめて直す

class BulkFixDialog(QDialog):
    """拡張子をまとめて直す前に、変更の一覧を見せて確かめてもらう。"""

    def __init__(self, plan, parent=None):
        """plan: [(パス, 今の名前, 新しい名前, 中身の種類, はじめにチェックするか), ...]"""
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

        super().__init__(parent)
        self.setWindowTitle("拡張子をまとめて直す")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"次の {len(plan)} 個の拡張子を直せます。直すものにチェックを付けてください。"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["今の名前", "新しい名前", "中身"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 220)
        for path, old, new, kind, checked in plan:
            item = QTreeWidgetItem([old, new, kind])
            item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            item.setData(0, Qt.UserRole, path)
            item.setToolTip(0, path)
            self.tree.addTopLevelItem(item)
        layout.addWidget(self.tree, 1)
        note = QLabel("※ 同じ名前のファイルがすでにあるものは変更しません。あとで「元に戻す」でまとめて戻せます。")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("直す")
        buttons.button(QDialogButtonBox.Cancel).setText("やめる")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.tree.itemChanged.connect(lambda *_: self.ok.setEnabled(bool(self.chosen())))
        self.ok.setEnabled(bool(self.chosen()))

    def chosen(self):
        result = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                result.append(item.data(0, Qt.UserRole))
        return result


# ------------------------------------------------------------------ 履歴

class HistoryDialog(QDialog):
    """最近扱ったファイルの一覧。選んだものを小窓の一覧に呼び戻せる。"""

    def __init__(self, recent, keep_history, parent=None):
        import time as _time

        from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem

        super().__init__(parent)
        self.recent = recent
        self.setWindowTitle("履歴")
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名前", "場所", "扱った日時"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 260)
        for e in recent.entries:
            path = e["path"]
            exists = os.path.lexists(path)
            item = QTreeWidgetItem([
                os.path.basename(path.rstrip("/")) or path,
                os.path.dirname(path.rstrip("/")),
                _time.strftime("%Y-%m-%d %H:%M", _time.localtime(e["time"])) + ("" if exists else "（見つかりません）"),
            ])
            item.setData(0, Qt.UserRole, path)
            item.setToolTip(0, path)
            if not exists:
                for col in range(3):
                    item.setForeground(col, self.palette().placeholderText())
            self.tree.addTopLevelItem(item)
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept())
        layout.addWidget(self.tree, 1)
        if not recent.entries:
            layout.addWidget(QLabel("履歴はまだありません。"))

        row = QHBoxLayout()
        remove = QPushButton("履歴から外す")
        remove.clicked.connect(self._remove_selected)
        clear = QPushButton("すべて消す")
        clear.clicked.connect(self._clear)
        row.addWidget(remove)
        row.addWidget(clear)
        row.addStretch(1)
        self.keep = QCheckBox("履歴を残す")
        self.keep.setChecked(keep_history)
        row.addWidget(self.keep)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Close)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("一覧に追加")
        buttons.button(QDialogButtonBox.Close).setText("閉じる")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.tree.itemSelectionChanged.connect(self._update)
        self._update()

    def _update(self):
        self.ok.setEnabled(bool(self.tree.selectedItems()))

    def _remove_selected(self):
        paths = [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()]
        self.recent.remove(paths)
        for item in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))

    def _clear(self):
        self.recent.clear()
        self.tree.clear()

    def selected_paths(self):
        return [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()
                if os.path.lexists(i.data(0, Qt.UserRole))]

    def keep_history(self):
        return self.keep.isChecked()


# ------------------------------------------------------------------ このソフトについて

def about_text():
    from . import APP_NAME, __version__

    return (
        f"<h3>{APP_NAME}（Kiwami） Ver.{__version__}</h3>"
        "<p>小さな窓にファイルをドロップするだけで、中身の本当の種類を見分けたり、"
        "名前・属性・日時の変更、中身の表示、圧縮・解凍ができる Ubuntu 用ソフトです。</p>"
        "<p>Windows 用のフリーソフト「極窓」（NTSOFT）の使い心地を目標にした、"
        "別の作者による新しいソフトです。元の極窓とは関係ありません。</p>"
        "<p>作者：飯田顕治<br>ライセンス：MIT<br>"
        "<a href='https://github.com/kenjiiida-commits/Kiwami'>https://github.com/kenjiiida-commits/Kiwami</a></p>"
    )
