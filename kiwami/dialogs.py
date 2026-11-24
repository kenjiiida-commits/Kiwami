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

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.setWindowTitle("属性の変更")
        self._updating = False
        layout = QVBoxLayout(self)
        title = QLabel(info.name)
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
    def __init__(self, info, atime_ns, mtime_ns, parent=None):
        super().__init__(parent)
        self.setWindowTitle("日時の変更")
        layout = QVBoxLayout(self)
        title = QLabel(info.name)
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
