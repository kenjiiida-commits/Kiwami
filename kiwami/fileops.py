"""ファイルを書き換える操作（名前・拡張子・属性・日時）と、その取り消し。

どの操作も、元に戻すための記録（Operation）を返す。元に戻すときは、
対象がその後に別のファイルへ入れ替わっていないか（同じ実体か）と、
別のソフトでさらに変更されていないかを確かめる。
"""

import ctypes
import errno
import os
import stat
from dataclasses import dataclass

from .detect import name_with_extension

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1

try:
    _libc = ctypes.CDLL(None, use_errno=True)
    _renameat2 = _libc.renameat2
    _renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    _renameat2.restype = ctypes.c_int
except (OSError, AttributeError):
    _renameat2 = None


class UndoError(Exception):
    """元に戻せない（対象が見つからない・入れ替わっている）。"""


class UndoConflict(Exception):
    """変更のあと、さらに別の変更がされている（force=True なら戻せる）。"""


def _same_file(a, b):
    try:
        sa, sb = os.lstat(a), os.lstat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def rename_no_replace(src, dst):
    """dst が既にあれば上書きせず FileExistsError にする名前の変更。"""
    if os.path.lexists(dst):
        if _same_file(src, dst):
            # 大文字小文字だけの変更（USB メモリなど大小を区別しない場所）
            os.rename(src, dst)
            return
        raise FileExistsError(errno.EEXIST, "同じ名前のファイルがすでにあります", dst)
    if _renameat2 is not None:
        r = _renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE)
        if r == 0:
            return
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise FileExistsError(err, "同じ名前のファイルがすでにあります", dst)
        if err not in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP):
            raise OSError(err, os.strerror(err), src)
        # この場所では上書き禁止の指定が使えない → 下の方法へ
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "同じ名前のファイルがすでにあります", dst)
    os.rename(src, dst)


def check_name(name):
    """ファイル名として使えるか調べ、だめなら理由を返す（使えるなら ""）。"""
    if not name or not name.strip():
        return "名前が空です"
    if name in (".", ".."):
        return "この名前は使えません"
    if "/" in name:
        return "「/」は名前に使えません"
    if "\0" in name:
        return "使えない文字が入っています"
    if len(os.fsencode(name)) > 255:
        return "名前が長すぎます"
    return ""


def _identity(path):
    st = os.lstat(path)
    return st.st_dev, st.st_ino


def _check_identity(path, ident):
    try:
        now = _identity(path)
    except FileNotFoundError:
        raise UndoError(f"「{os.path.basename(path)}」が見つかりません（移動または削除されたようです）")
    except OSError as e:
        raise UndoError(f"「{os.path.basename(path)}」を調べられません: {e.strerror}")
    if now != ident:
        raise UndoError(f"「{os.path.basename(path)}」は別のファイルに入れ替わっています")


# ---------------------------------------------------------------- 名前の変更

@dataclass
class RenameOp:
    old_path: str
    new_path: str
    ident: tuple
    content: tuple = ()  # (大きさ, 更新日時) 。変わっていたら確認する

    @property
    def path(self):
        return self.new_path

    def describe(self):
        return f"名前の変更（{os.path.basename(self.old_path)} → {os.path.basename(self.new_path)}）"

    def undo(self, force=False):
        _check_identity(self.new_path, self.ident)
        if self.content and not force:
            st = os.lstat(self.new_path)
            if not stat.S_ISDIR(st.st_mode) and (st.st_size, st.st_mtime_ns) != self.content:
                raise UndoConflict("名前を変えたあとで、中身が変わっています")
        try:
            rename_no_replace(self.new_path, self.old_path)
        except FileExistsError:
            raise UndoError(f"元の名前「{os.path.basename(self.old_path)}」のファイルが別にできているため戻せません")
        return self.old_path

    def relocate(self, old, new):
        self.old_path = _relocated(self.old_path, old, new)
        self.new_path = _relocated(self.new_path, old, new)


def rename(path, new_name):
    """同じフォルダーの中で名前を変える。(新しいパス, RenameOp) を返す。"""
    reason = check_name(new_name)
    if reason:
        raise ValueError(reason)
    folder = os.path.dirname(path)
    new_path = os.path.join(folder, new_name)
    if new_path == path:
        return path, None
    st = os.lstat(path)
    rename_no_replace(path, new_path)
    return new_path, RenameOp(path, new_path, (st.st_dev, st.st_ino), (st.st_size, st.st_mtime_ns))


def change_extension(path, new_ext):
    """拡張子を new_ext（"" なら拡張子なし）に付け替える。(新しいパス, RenameOp) を返す。"""
    return rename(path, name_with_extension(os.path.basename(path), new_ext))


# ---------------------------------------------------------------- 属性の変更

@dataclass
class ChmodOp:
    path: str
    ident: tuple
    old_mode: int
    new_mode: int

    def describe(self):
        return (f"属性の変更（{os.path.basename(self.path)}："
                f"{format_mode(self.old_mode)} → {format_mode(self.new_mode)}）")

    def undo(self, force=False):
        _check_identity(self.path, self.ident)
        now = stat.S_IMODE(os.lstat(self.path).st_mode)
        if now != self.new_mode and not force:
            raise UndoConflict("変更したあとで、属性がさらに変えられています")
        os.chmod(self.path, self.old_mode)
        return self.path

    def relocate(self, old, new):
        self.path = _relocated(self.path, old, new)


def set_mode(path, mode):
    """属性（パーミッション）を変える。setuid などの特別な印は今のまま残す。"""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise ValueError("シンボリックリンクの属性は変更できません")
    old = stat.S_IMODE(st.st_mode)
    new = (old & ~0o777) | (mode & 0o777)
    if new == old:
        return None
    os.chmod(path, new)
    return ChmodOp(path, (st.st_dev, st.st_ino), old, new)


def format_mode(mode):
    """0o644 → "rw-r--r--（644）" """
    chars = ""
    for shift in (6, 3, 0):
        bits = (mode >> shift) & 7
        chars += ("r" if bits & 4 else "-") + ("w" if bits & 2 else "-") + ("x" if bits & 1 else "-")
    return f"{chars}（{mode & 0o777:03o}）"


# ---------------------------------------------------------------- 日時の変更

@dataclass
class UtimeOp:
    path: str
    ident: tuple
    old_ns: tuple  # (アクセス日時, 更新日時) のナノ秒
    new_ns: tuple

    def describe(self):
        return f"日時の変更（{os.path.basename(self.path)}）"

    def undo(self, force=False):
        _check_identity(self.path, self.ident)
        st = os.lstat(self.path)
        if st.st_mtime_ns != self.new_ns[1] and not force:
            raise UndoConflict("変更したあとで、ファイルの更新日時がさらに変わっています（中身が書き換えられたかもしれません）")
        os.utime(self.path, ns=self.old_ns, follow_symlinks=False)
        return self.path

    def relocate(self, old, new):
        self.path = _relocated(self.path, old, new)


def set_times(path, atime_ns, mtime_ns):
    """アクセス日時と更新日時を変える（ナノ秒）。"""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise ValueError("シンボリックリンクの日時は変更できません")
    old = (st.st_atime_ns, st.st_mtime_ns)
    new = (atime_ns, mtime_ns)
    if new == old:
        return None
    os.utime(path, ns=new, follow_symlinks=False)
    return UtimeOp(path, (st.st_dev, st.st_ino), old, new)


# ---------------------------------------------------------------- 履歴

def _relocated(path, old, new):
    if path == old:
        return new
    if path.startswith(old.rstrip("/") + "/"):
        return new + path[len(old.rstrip("/")):]
    return path


class History:
    """取り消しのための操作の記録（新しいものが最後）。"""

    LIMIT = 100

    def __init__(self):
        self._ops = []

    def push(self, op):
        if op is None:
            return
        self._ops.append(op)
        del self._ops[:-self.LIMIT]
        if isinstance(op, RenameOp):
            # フォルダーの名前を変えたら、それより前の記録の中のパスも付け替える
            for earlier in self._ops[:-1]:
                earlier.relocate(op.old_path, op.new_path)

    def peek(self):
        return self._ops[-1] if self._ops else None

    def __len__(self):
        return len(self._ops)

    def undo(self, force=False):
        """最後の操作を元に戻し、(操作, 戻したあとのパス) を返す。

        UndoConflict のときは記録を残す（force=True でもう一度呼べる）。
        UndoError のときは戻せないので記録から外す。
        """
        op = self._ops[-1]
        try:
            path = op.undo(force=force)
        except UndoConflict:
            raise
        except (UndoError, OSError):
            self._ops.pop()
            raise
        self._ops.pop()
        if isinstance(op, RenameOp):
            for earlier in self._ops:
                earlier.relocate(op.new_path, op.old_path)
        return op, path
