"""libmagic（Ubuntu の `file` コマンドと同じ判別エンジン）を ctypes で直接呼び出す。

追加の Python パッケージを入れずに済むよう、Ubuntu に最初から入っている
libmagic.so.1 を使う。
"""

import ctypes
import ctypes.util
import os
import threading

# libmagic のフラグ（/usr/include/magic.h より）
MAGIC_NONE = 0x0000000
MAGIC_MIME_TYPE = 0x0000010
MAGIC_MIME_ENCODING = 0x0000400
MAGIC_EXTENSION = 0x1000000


class MagicError(Exception):
    pass


def _load_library():
    candidates = []
    found = ctypes.util.find_library("magic")
    if found:
        candidates.append(found)
    candidates.append("libmagic.so.1")
    last_error = None
    for name in candidates:
        try:
            return ctypes.CDLL(name)
        except OSError as e:
            last_error = e
    raise MagicError(
        "libmagic が見つかりません。`sudo apt install libmagic1` を実行してください。"
        f"（{last_error}）"
    )


_lib = _load_library()
_lib.magic_open.restype = ctypes.c_void_p
_lib.magic_open.argtypes = [ctypes.c_int]
_lib.magic_close.restype = None
_lib.magic_close.argtypes = [ctypes.c_void_p]
_lib.magic_load.restype = ctypes.c_int
_lib.magic_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_lib.magic_file.restype = ctypes.c_char_p
_lib.magic_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_lib.magic_error.restype = ctypes.c_char_p
_lib.magic_error.argtypes = [ctypes.c_void_p]


class Magic:
    """1種類の問い合わせ（説明文・MIME・拡張子など）を受け持つ。"""

    def __init__(self, flags):
        self._cookie = _lib.magic_open(flags)
        if not self._cookie:
            raise MagicError("libmagic の初期化に失敗しました")
        if _lib.magic_load(self._cookie, None) != 0:
            msg = _lib.magic_error(self._cookie)
            raise MagicError(f"判別データを読み込めません: {msg!r}")
        self._lock = threading.Lock()

    def from_file(self, path):
        with self._lock:
            result = _lib.magic_file(self._cookie, os.fsencode(path))
            if result is None:
                msg = _lib.magic_error(self._cookie)
                raise MagicError(msg.decode("utf-8", "replace") if msg else "判別に失敗しました")
            return result.decode("utf-8", "replace")

    def close(self):
        if self._cookie:
            _lib.magic_close(self._cookie)
            self._cookie = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
