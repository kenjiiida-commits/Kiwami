"""名前・属性・日時を変えるための小さなダイアログ。"""

import os

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (
    QCheckBox,
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
