"""中身を見る窓（簡易ビューア）：テキスト・画像・16進ダンプ・圧縮ファイルの中身一覧。"""

import threading

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Signal
from PySide6.QtGui import QFontDatabase, QImageReader, QMovie, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import archive, textenc
from .detect import OK_TEXT, format_size, format_size_short

MAX_TEXT = 4 * 1024 * 1024  # テキストとして読む最大の大きさ


def _mono_font():
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    return font


def _note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.PlainText)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


# ------------------------------------------------------------------ テキスト

class TextTab(QWidget):
    def __init__(self, path, size):
        super().__init__()
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("文字コード："))
        self.combo = QComboBox()
        top.addWidget(self.combo)
        self.wrap = QCheckBox("折り返す")
        self.wrap.setChecked(True)
        self.wrap.toggled.connect(self._set_wrap)
        top.addWidget(self.wrap)
        top.addStretch(1)
        self.info = QLabel()
        top.addWidget(self.info)
        layout.addLayout(top)
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setFont(_mono_font())
        layout.addWidget(self.edit, 1)
        self.note = _note("")
        layout.addWidget(self.note)

        try:
            with open(path, "rb") as f:
                self.data = f.read(MAX_TEXT)
        except OSError as e:
            self.data = b""
            self.note.setText(f"読み取れません: {e.strerror}")
            return
        self.truncated = size > len(self.data)
        detected = textenc.detect(self.data, truncated=self.truncated)
        self.combo.addItem(f"自動（{textenc.display_name(detected)}）", detected)
        for enc, name in textenc.ENCODINGS:
            self.combo.addItem(name, enc)
        self.combo.currentIndexChanged.connect(self._show)
        if self.truncated:
            self.note.setText(f"大きいファイルのため、最初の {format_size(len(self.data))} だけ表示しています")
        if b"\0" in self.data[:8192] and not detected.startswith("utf-16"):
            self.note.setText((self.note.text() + "\n").lstrip() + "※ テキストではない部分が含まれているようです")
        self._set_wrap(True)
        self._show()

    def _set_wrap(self, on):
        self.edit.setLineWrapMode(QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap)

    def _show(self):
        enc = self.combo.currentData()
        text = textenc.decode(self.data, enc)
        if "\0" in text:
            text = text.replace("\0", "·")
        self.edit.setPlainText(text)
        lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        self.info.setText(f"{textenc.line_ending(self.data)}　{lines:,} 行")


# ------------------------------------------------------------------ 画像

class ImageTab(QWidget):
    def __init__(self, path):
        super().__init__()
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.fit = QCheckBox("窓に合わせる")
        self.fit.setChecked(True)
        self.fit.toggled.connect(self._update)
        top.addWidget(self.fit)
        top.addStretch(1)
        self.info = QLabel()
        top.addWidget(self.info)
        layout.addLayout(top)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignCenter)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        self.movie = None
        self.pixmap = None

        reader = QImageReader(path)
        reader.setAutoTransform(True)  # スマホ写真の向きを直す
        if reader.supportsAnimation() and reader.imageCount() != 1:
            self.movie = QMovie(path)
            if self.movie.isValid():
                self.label.setMovie(self.movie)
                self.movie.start()
                size = self.movie.frameRect().size()
                self.info.setText(f"{size.width()} × {size.height()}（アニメーション）")
                self.fit.setEnabled(False)
                return
            self.movie = None
        image = reader.read()
        if image.isNull():
            self.label.setText(
                "この画像は表示できません。\n"
                f"（{reader.errorString()}）\n\n"
                "WebP・HEIC などは、端末で\n"
                "sudo apt install qt6-image-formats-plugins\n"
                "を実行すると表示できることがあります。"
            )
            self.fit.setEnabled(False)
            return
        self.pixmap = QPixmap.fromImage(image)
        self.info.setText(f"{image.width()} × {image.height()}")
        self._update()

    def _update(self):
        if self.pixmap is None:
            return
        if self.fit.isChecked():
            area = self.scroll.viewport().size()
            if self.pixmap.width() > area.width() or self.pixmap.height() > area.height():
                self.label.setPixmap(self.pixmap.scaled(area, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        self.label.setPixmap(self.pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update()


# ------------------------------------------------------------------ 16進ダンプ

class HexModel(QAbstractTableModel):
    """ファイルを少しずつ読みながら 16 バイトずつ表示する（大きいファイルでも軽い）。"""

    BLOCK = 64 * 1024
    HEADERS = ["アドレス", "00 01 02 03 04 05 06 07  08 09 0A 0B 0C 0D 0E 0F", "文字"]

    def __init__(self, path, size):
        super().__init__()
        self.path = path
        self.size = size
        self._cache = {}
        self.error = ""

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else (self.size + 15) // 16

    def columnCount(self, parent=QModelIndex()):
        return 3

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def _block(self, index):
        if index not in self._cache:
            if len(self._cache) > 64:
                self._cache.clear()
            try:
                with open(self.path, "rb") as f:
                    f.seek(index * self.BLOCK)
                    self._cache[index] = f.read(self.BLOCK)
            except OSError as e:
                self.error = e.strerror or str(e)
                self._cache[index] = b""
        return self._cache[index]

    def row_bytes(self, row):
        offset = row * 16
        block = self._block(offset // self.BLOCK)
        start = offset % self.BLOCK
        return block[start:start + 16]

    def data(self, index, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or not index.isValid():
            return None
        row = index.row()
        if index.column() == 0:
            return f"{row * 16:08X}"
        chunk = self.row_bytes(row)
        if index.column() == 1:
            left = " ".join(f"{b:02X}" for b in chunk[:8])
            right = " ".join(f"{b:02X}" for b in chunk[8:])
            return f"{left}  {right}" if right else left
        return "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)


class HexTab(QWidget):
    def __init__(self, path, size):
        super().__init__()
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("移動先のアドレス："))
        self.jump = QLineEdit()
        self.jump.setPlaceholderText("例: 1F0 （16進）")
        self.jump.setMaximumWidth(160)
        self.jump.returnPressed.connect(self._jump)
        top.addWidget(self.jump)
        go = QPushButton("移動")
        go.clicked.connect(self._jump)
        top.addWidget(go)
        top.addStretch(1)
        top.addWidget(QLabel(f"{format_size(size)}"))
        layout.addLayout(top)

        self.model = HexModel(path, size)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setFont(_mono_font())
        self.view.setShowGrid(False)
        self.view.setSelectionBehavior(QTableView.SelectRows)
        self.view.verticalHeader().hide()
        vh = self.view.verticalHeader()
        vh.setSectionResizeMode(QHeaderView.Fixed)
        vh.setDefaultSectionSize(self.view.fontMetrics().height() + 4)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        layout.addWidget(self.view, 1)
        self.status = _note("")
        layout.addWidget(self.status)
        if size == 0:
            self.status.setText("空のファイルです")

    def _jump(self):
        text = self.jump.text().strip().lower().removeprefix("0x")
        try:
            offset = int(text, 16)
        except ValueError:
            self.status.setText("アドレスは 16 進数で入力してください（例: 1F0）")
            return
        if offset >= self.model.size:
            self.status.setText("ファイルの大きさを超えています")
            return
        index = self.model.index(offset // 16, 0)
        self.view.scrollTo(index, QTableView.PositionAtTop)
        self.view.selectRow(index.row())
        self.status.setText(f"{offset:08X} に移動しました")


# ------------------------------------------------------------------ 圧縮ファイルの中身

class _Loader(QObject):
    done = Signal(object, str, str)  # (entries, note, error)


class ArchiveTab(QWidget):
    def __init__(self, path, mime):
        super().__init__()
        layout = QVBoxLayout(self)
        self.summary = QLabel("読み込み中…")
        layout.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名前", "大きさ", "圧縮後", "更新日時"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(-1, Qt.AscendingOrder)
        self.tree.setColumnWidth(0, 380)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 90)
        layout.addWidget(self.tree, 1)
        self.note = _note("")
        layout.addWidget(self.note)

        self._loader = _Loader()
        self._loader.done.connect(self._loaded)
        threading.Thread(target=self._work, args=(path, mime), daemon=True).start()

    def _work(self, path, mime):
        try:
            entries, note = archive.list_entries(path, mime)
            result = (entries, note, "")
        except archive.ArchiveError as e:
            result = ([], "", str(e))
        except Exception as e:  # 想定外の壊れ方でも窓は落とさない
            result = ([], "", f"読み取れません: {e}")
        try:
            self._loader.done.emit(*result)
        except RuntimeError:
            pass  # 読み終わる前に窓が閉じられた

    def _loaded(self, entries, note, error):
        if error:
            self.summary.setText("中身を読めませんでした")
            self.summary.setStyleSheet("color: #c62828;")
            self.note.setText(error)
            return
        files = [e for e in entries if not e.is_dir]
        dirs = len(entries) - len(files)
        total = sum(e.size or 0 for e in files)
        locked = sum(1 for e in files if e.encrypted)
        text = f"ファイル {len(files):,} 個・フォルダー {dirs:,} 個・展開後の合計 {format_size(total)}"
        if locked:
            text += f"・🔒 パスワード付き {locked:,} 個"
        self.summary.setText(text)
        self.note.setText(note)
        items = []
        for e in entries:
            item = _SortItem([
                ("🔒 " if e.encrypted else "") + e.name,
                format_size_short(e.size) if e.size is not None and not e.is_dir else "",
                format_size_short(e.packed) if e.packed is not None and not e.is_dir else "",
                e.mtime.strftime("%Y-%m-%d %H:%M") if e.mtime else "",
            ])
            item.setData(1, Qt.UserRole, e.size or 0)
            item.setData(2, Qt.UserRole, e.packed or 0)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            items.append(item)
        self.tree.addTopLevelItems(items)


class _SortItem(QTreeWidgetItem):
    """大きさの列は数の大きさで並べる。"""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        if col in (1, 2):
            return (self.data(col, Qt.UserRole) or 0) < (other.data(col, Qt.UserRole) or 0)
        return self.text(col) < other.text(col)


# ------------------------------------------------------------------ 窓

def _is_textish(info):
    return info.mime.startswith("text/") or info.status == OK_TEXT or info.mime == "application/json"


class ViewerWindow(QWidget):
    def __init__(self, info, parent=None):
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"中身を見る — {info.name}")
        self.resize(780, 560)
        layout = QVBoxLayout(self)
        head = QLabel(f"{info.name}　{info.type_name or info.description}　{format_size(info.size)}")
        head.setTextFormat(Qt.PlainText)
        head.setStyleSheet("font-weight: bold;")
        layout.addWidget(head)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        path, size = info.path, info.size
        is_image = info.mime.startswith("image/")
        is_archive = archive.is_archive(info.mime)
        # 開いたときに見せるタブ
        first = "hex"
        if is_archive:
            first = "archive"
        elif is_image:
            first = "image"
        elif _is_textish(info):
            first = "text"
        self._factories = {}
        order = []
        if is_archive:
            order.append(("archive", "中身一覧", lambda: ArchiveTab(path, info.mime)))
        if is_image:
            order.append(("image", "画像", lambda: ImageTab(path)))
        order.append(("text", "テキスト", lambda: TextTab(path, size)))
        order.append(("hex", "16進", lambda: HexTab(path, size)))
        # 開いたタブだけ作る（大きいファイルでも素早く開けるように）
        for key, title, factory in order:
            placeholder = QWidget()
            index = self.tabs.addTab(placeholder, title)
            self._factories[index] = factory
            if key == first:
                first_index = index
        self.tabs.currentChanged.connect(self._ensure_tab)
        self.tabs.setCurrentIndex(first_index)
        self._ensure_tab(first_index)

    def _ensure_tab(self, index):
        factory = self._factories.pop(index, None)
        if factory is None:
            return
        title = self.tabs.tabText(index)
        widget = factory()
        self.tabs.blockSignals(True)
        self.tabs.removeTab(index)
        self.tabs.insertTab(index, widget, title)
        self.tabs.setCurrentIndex(index)
        self.tabs.blockSignals(False)
