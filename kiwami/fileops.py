"""ファイルを書き換える操作（拡張子の付け替えなど）。"""

import ctypes
import errno
import os

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


def change_extension(path, new_ext):
    """拡張子を new_ext（"" なら拡張子なし）に付け替え、新しいパスを返す。"""
    folder, name = os.path.split(path)
    new_name = name_with_extension(name, new_ext)
    if not new_name or new_name in (".", ".."):
        raise ValueError("この名前にはできません")
    new_path = os.path.join(folder, new_name)
    if new_path == path:
        return path
    rename_no_replace(path, new_path)
    return new_path
