"""圧縮ファイルの中身の一覧を取り出す（展開はしない）。

zip と tar 系は Python だけで読む。7z・rar・lzh などは 7z コマンドを使う。
"""

import os
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime

MAX_ENTRIES = 50000

ZIP_MIMES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/epub+zip",
    "application/java-archive",
    "application/vnd.android.package-archive",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/vnd.oasis.opendocument.graphics",
}
TAR_MIMES = {
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
    "application/x-bzip2",
    "application/x-xz",
}
SEVENZIP_MIMES = {
    "application/x-7z-compressed",
    "application/x-rar",
    "application/vnd.rar",
    "application/x-rar-compressed",
    "application/x-lzh-compressed",
    "application/x-lha",
    "application/x-lharc",
    "application/vnd.ms-cab-compressed",
    "application/x-iso9660-image",
    "application/zstd",
    "application/x-zstd",
    "application/x-rpm",
    "application/vnd.debian.binary-package",
}


@dataclass
class Entry:
    name: str
    size: int | None = None
    packed: int | None = None
    mtime: datetime | None = None
    is_dir: bool = False
    encrypted: bool = False


class ArchiveError(Exception):
    pass


def is_archive(mime):
    return mime in ZIP_MIMES or mime in TAR_MIMES or mime in SEVENZIP_MIMES


def sevenzip_command():
    for name in ("7z", "7zz", "7za"):
        found = shutil.which(name)
        if found:
            return found
    return None


def zip_entry_name(info):
    """zip の中のファイル名を正しく読む。

    UTF-8 の印がないものは、Windows で作られた Shift_JIS の名前のことが多い。
    """
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.orig_filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return info.filename


def list_entries(path, mime):
    """(Entry の一覧, 補足のメッセージ) を返す。読めなければ ArchiveError。"""
    if mime in ZIP_MIMES:
        return _list_zip(path)
    if mime in TAR_MIMES:
        return _list_tar(path, mime)
    if mime in SEVENZIP_MIMES:
        return _list_7z(path)
    raise ArchiveError("この形式の中身一覧には対応していません")


def _list_zip(path):
    entries = []
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if len(entries) >= MAX_ENTRIES:
                    return entries, f"多すぎるため、最初の {MAX_ENTRIES:,} 件だけ表示しています"
                try:
                    mtime = datetime(*info.date_time)
                except ValueError:
                    mtime = None
                entries.append(Entry(
                    name=zip_entry_name(info),
                    size=info.file_size,
                    packed=info.compress_size,
                    mtime=mtime,
                    is_dir=info.is_dir(),
                    encrypted=bool(info.flag_bits & 0x1),
                ))
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise ArchiveError(f"zip として読めません: {e}")
    return entries, ""


def _list_tar(path, mime):
    entries = []
    try:
        with tarfile.open(path, "r:*") as t:
            for m in t:
                if len(entries) >= MAX_ENTRIES:
                    return entries, f"多すぎるため、最初の {MAX_ENTRIES:,} 件だけ表示しています"
                entries.append(Entry(
                    name=m.name + ("/" if m.isdir() else ""),
                    size=m.size if m.isfile() else None,
                    mtime=datetime.fromtimestamp(m.mtime) if m.mtime else None,
                    is_dir=m.isdir(),
                ))
    except tarfile.ReadError:
        if mime == "application/x-tar":
            raise ArchiveError("tar として読めません")
        # tar ではなく、ファイル1つをそのまま圧縮したもの
        stem = os.path.basename(path).rsplit(".", 1)[0]
        return [Entry(name=stem)], "ファイル1つをそのまま圧縮したものです（tar ではありません）"
    except (tarfile.TarError, OSError, EOFError) as e:
        raise ArchiveError(f"読み取れません: {e}")
    return entries, ""


def _list_7z(path):
    cmd = sevenzip_command()
    if cmd is None:
        raise ArchiveError(
            "この形式の中身を見るには 7z コマンドが必要です。\n"
            "端末で `sudo apt install 7zip` を実行してください。"
        )
    try:
        proc = subprocess.run(
            [cmd, "l", "-slt", "-sccUTF-8", "--", path],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise ArchiveError("中身の読み取りに時間がかかりすぎたため、中止しました")
    except OSError as e:
        raise ArchiveError(f"7z を実行できません: {e}")
    out = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0 and "----------" not in out:
        err = (proc.stderr.decode("utf-8", "replace").strip() or out.strip()).splitlines()
        message = err[-1] if err else "不明なエラー"
        if "password" in message.lower() or "encrypted" in message.lower():
            message = "パスワード付きのため、中身の名前を読めません"
        raise ArchiveError(message)
    return _parse_7z_slt(out)


def _parse_7z_slt(out):
    """7z l -slt の出力を Entry の一覧にする。"""
    entries = []
    _, sep, body = out.partition("----------")
    if not sep:
        return entries, ""
    block = {}

    def flush():
        if "Path" not in block:
            return
        attrs = block.get("Attributes", "")
        is_dir = block.get("Folder") == "+" or attrs.startswith("D")
        mtime = None
        m = block.get("Modified", "")
        if m:
            try:
                mtime = datetime.strptime(m[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        entries.append(Entry(
            name=block["Path"] + ("/" if is_dir else ""),
            size=_int(block.get("Size")),
            packed=_int(block.get("Packed Size")),
            mtime=mtime,
            is_dir=is_dir,
            encrypted=block.get("Encrypted") == "+",
        ))

    for line in body.splitlines():
        if not line.strip():
            flush()
            block = {}
            if len(entries) >= MAX_ENTRIES:
                return entries, f"多すぎるため、最初の {MAX_ENTRIES:,} 件だけ表示しています"
            continue
        key, eq, value = line.partition(" = ")
        if eq:
            block[key.strip()] = value
    flush()
    return entries, ""


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None
