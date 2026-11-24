"""ファイルの中身を調べて種類と本来の拡張子を判別する。"""

import os
import stat
from dataclasses import dataclass, field
from datetime import datetime

from .magic_lib import (
    MAGIC_EXTENSION,
    MAGIC_MIME_ENCODING,
    MAGIC_MIME_TYPE,
    MAGIC_NONE,
    Magic,
    MagicError,
)

_describe = Magic(MAGIC_NONE)
_mime = Magic(MAGIC_MIME_TYPE)
_encoding = Magic(MAGIC_MIME_ENCODING)
_extension = Magic(MAGIC_EXTENSION)


@dataclass
class FileInfo:
    path: str
    name: str
    kind: str  # "file" / "dir" / "symlink" / "other" / "missing"
    size: int = 0
    mtime: datetime | None = None
    description: str = ""
    mime: str = ""
    encoding: str = ""
    candidate_exts: list[str] = field(default_factory=list)
    current_ext: str = ""
    error: str = ""


def current_extension(name):
    """ファイル名から今付いている拡張子（小文字・ドットなし）を取り出す。

    .tar.gz のような二重拡張子も1つとして扱う。先頭がドットの隠しファイル
    （.bashrc など）は拡張子なしとみなす。
    """
    base = name.lstrip(".")
    if "." not in base:
        return ""
    lower = base.lower()
    for double in ("tar.gz", "tar.bz2", "tar.xz", "tar.zst"):
        if lower.endswith("." + double):
            return double
    return lower.rsplit(".", 1)[1]


def parse_extensions(raw):
    """libmagic の拡張子出力（例: "jpeg/jpg/jpe/jfif"、不明なら "???"）を一覧にする。"""
    raw = raw.strip()
    if not raw or raw == "???":
        return []
    return [e.strip().lower() for e in raw.split("/") if e.strip()]


def inspect(path):
    path = os.path.abspath(path)
    name = os.path.basename(path.rstrip("/")) or path
    info = FileInfo(path=path, name=name, kind="other", current_ext=current_extension(name))

    try:
        st = os.lstat(path)
    except FileNotFoundError:
        info.kind = "missing"
        info.error = "ファイルが見つかりません"
        return info
    except OSError as e:
        info.error = f"情報を取得できません: {e.strerror}"
        return info

    info.mtime = datetime.fromtimestamp(st.st_mtime)
    if stat.S_ISLNK(st.st_mode):
        info.kind = "symlink"
        try:
            info.description = "シンボリックリンク → " + os.readlink(path)
        except OSError:
            info.description = "シンボリックリンク"
        return info
    if stat.S_ISDIR(st.st_mode):
        info.kind = "dir"
        info.description = "フォルダー"
        return info
    if not stat.S_ISREG(st.st_mode):
        info.description = "特殊ファイル（デバイス・FIFO・ソケットなど）"
        return info

    info.kind = "file"
    info.size = st.st_size
    try:
        info.description = _describe.from_file(path)
        info.mime = _mime.from_file(path)
        info.encoding = _encoding.from_file(path)
        info.candidate_exts = parse_extensions(_extension.from_file(path))
    except MagicError as e:
        info.error = str(e)
    return info


def format_size(n):
    if n < 1024:
        return f"{n:,} バイト"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}（{n:,} バイト）"
