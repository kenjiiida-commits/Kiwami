"""圧縮と解凍。

決まりごと:
- 解凍は、圧縮ファイルと同じ場所に新しいフォルダーを作ってその中へ出す
  （ファイル1つをそのまま圧縮した .gz などは、ファイルとして隣に出す）。
- 同じ名前があれば「名前 (2)」のように番号を付け、上書きはしない。
- 途中で失敗・中止したら、作りかけのものは消す。
- 圧縮ファイルの中の「../」などで外へ書き出そうとする項目は書き出さない。

どの関数も progress(割合 0〜1 または None, 説明) と cancel（threading.Event）を受け取る。
"""

import bz2
import gzip
import lzma
import os
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import time
import zipfile
import zlib
from dataclasses import dataclass, field

from .archive import SEVENZIP_MIMES, TAR_MIMES, ZIP_MIMES, sevenzip_command, zip_entry_name
from .detect import split_name
from .fileops import rename_no_replace

CHUNK = 1024 * 1024
RAR_MIMES = {"application/x-rar", "application/vnd.rar", "application/x-rar-compressed"}
SINGLE_OPENERS = {
    "application/gzip": gzip.open,
    "application/x-gzip": gzip.open,
    "application/x-bzip2": bz2.open,
    "application/x-xz": lzma.open,
}

FORMATS = {
    # 表示名, 拡張子, パスワードを使えるか
    "zip": ("zip（Windows でもそのまま開ける）", "zip", True),
    "7z": ("7z（よく縮む）", "7z", True),
    "tar.gz": ("tar.gz（Linux でよく使う）", "tar.gz", False),
    "tar.xz": ("tar.xz（よく縮む・Linux 向け）", "tar.xz", False),
}


class PackError(Exception):
    pass


class Cancelled(Exception):
    pass


class _ZipReadError(PackError):
    """Python の zip 機能では読めなかった（7z ならいけるかもしれない）。"""


class PasswordNeeded(Exception):
    """パスワードが必要（wrong=True なら、入れたパスワードが違う）。"""

    def __init__(self, wrong=False):
        super().__init__("パスワードが違います" if wrong else "パスワードが必要です")
        self.wrong = wrong


@dataclass
class Result:
    path: str  # 作ったフォルダーまたはファイル
    skipped: list[str] = field(default_factory=list)  # 安全のため書き出さなかった項目
    note: str = ""
    damaged: list[str] = field(default_factory=list)  # 壊れていて取り出せなかった項目


def can_extract(mime):
    return mime in ZIP_MIMES or mime in TAR_MIMES or mime in SEVENZIP_MIMES


# ------------------------------------------------------------------ 名前

def unique_path(folder, name, is_dir):
    """folder の中で、まだ使われていない名前のパスを返す。"""
    candidate = os.path.join(folder, name)
    if not os.path.lexists(candidate):
        return candidate
    stem, ext = (name, "") if is_dir else split_name(name)
    n = 2
    while True:
        new = f"{stem} ({n})" + (f".{ext}" if ext else "")
        candidate = os.path.join(folder, new)
        if not os.path.lexists(candidate):
            return candidate
        n += 1


def _make_unique_dir(folder, name):
    while True:
        path = unique_path(folder, name, True)
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            continue  # 同時に作られた → 次の番号


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def _safe_parts(name):
    """圧縮ファイルの中の名前を、安全なフォルダー・ファイル名の並びにする。外へ出るものは None。"""
    name = name.replace("\\", "/")
    parts = []
    for p in name.split("/"):
        if p in ("", "."):
            continue
        if p == ".." or "\0" in p:
            return None
        parts.append(p)
    return parts or None


# ------------------------------------------------------------------ 解凍

def extract(path, mime, password=None, progress=None, cancel=None):
    progress = progress or (lambda *a: None)
    folder = os.path.dirname(path)
    stem = split_name(os.path.basename(path))[0]

    if mime in SINGLE_OPENERS and not _is_tar(path):
        return _extract_single(path, mime, folder, stem, progress, cancel)

    dest = _make_unique_dir(folder, stem)
    try:
        if mime in ZIP_MIMES and not _zip_needs_7z(path):
            try:
                result = _extract_zip(path, dest, password, progress, cancel)
            except _ZipReadError as e:
                # 4GB を超える zip などで、中の位置情報が少し壊れていることがある。
                # 7z のほうが粘り強く読めるので、作り直したフォルダーでやり直す。
                if sevenzip_command() is None:
                    raise PackError(f"{e}\n（7zip を入れると読めることがあります: sudo apt install 7zip）")
                shutil.rmtree(dest, ignore_errors=True)
                os.mkdir(dest)
                progress(0.0, "別の方法（7z）でやり直しています")
                result = _extract_7z(path, mime, dest, password, progress, cancel)
                result.note = ("通常の方法では読めなかったため、7z で解凍しました。"
                               + (f"\n{result.note}" if result.note else ""))
        elif mime in TAR_MIMES:
            result = _extract_tar(path, dest, progress, cancel)
        elif mime in RAR_MIMES and shutil.which("unrar"):
            result = _extract_unrar(path, dest, password, progress, cancel)
        elif mime in SEVENZIP_MIMES or mime in ZIP_MIMES:
            result = _extract_7z(path, mime, dest, password, progress, cancel)
        else:
            raise PackError("この形式の解凍には対応していません")
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return result


def _is_tar(path):
    try:
        with tarfile.open(path, "r:*") as t:
            t.next()
        return True
    except (tarfile.TarError, OSError, EOFError):
        return False


def _zip_needs_7z(path):
    """AES で暗号化された zip は Python では開けないので 7z に任せる。"""
    try:
        with zipfile.ZipFile(path) as z:
            return any(i.compress_type == 99 for i in z.infolist())
    except (zipfile.BadZipFile, OSError):
        return False


def _extract_single(path, mime, folder, stem, progress, cancel):
    target = unique_path(folder, stem, False)
    total = os.path.getsize(path)
    try:
        with SINGLE_OPENERS[mime](path, "rb") as src, open(target, "xb") as dst:
            raw = getattr(src, "fileobj", None) or getattr(src, "_fp", None)
            while True:
                _check(cancel)
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                dst.write(chunk)
                pos = _tell(raw)
                progress(min(pos / total, 1.0) if pos and total else None, os.path.basename(target))
    except BaseException as e:
        try:
            os.remove(target)
        except OSError:
            pass
        if isinstance(e, (OSError, EOFError, lzma.LZMAError)) and not isinstance(e, PermissionError):
            raise PackError(f"解凍できませんでした: {e}")
        raise
    return Result(target, note="ファイル1つを圧縮したものなので、そのまま隣に出しました")


def _tell(fileobj):
    try:
        return fileobj.tell()
    except (AttributeError, OSError, ValueError):
        return None


def _extract_zip(path, dest, password, progress, cancel):
    skipped = []
    pwd = password.encode("utf-8") if password else None
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise _ZipReadError(f"zip として読めません: {e}")
    except OSError as e:
        raise PackError(f"開けません: {e.strerror or e}")
    with z:
        infos = z.infolist()
        if any(i.flag_bits & 0x1 for i in infos) and not password:
            raise PasswordNeeded()
        total = sum(i.file_size for i in infos) or 1
        done = 0
        damaged = []
        files = 0
        for info in infos:
            _check(cancel)
            name = zip_entry_name(info)
            parts = _safe_parts(name)
            if parts is None:
                skipped.append(name)
                continue
            unix_mode = info.external_attr >> 16 if info.create_system == 3 else 0
            if stat.S_ISLNK(unix_mode):
                skipped.append(name)  # シンボリックリンクは作らない
                continue
            target = os.path.join(dest, *parts)
            if info.is_dir() or name.endswith(("/", "\\")):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.isdir(target):
                skipped.append(name)
                continue
            target = unique_path(os.path.dirname(target), os.path.basename(target), False)
            files += 1
            start_done = done
            try:
                with z.open(info, pwd=pwd) as src, open(target, "xb") as dst:
                    while True:
                        _check(cancel)
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        dst.write(chunk)
                        done += len(chunk)
                        progress(min(done / total, 1.0), name)
            except RuntimeError as e:  # NotImplementedError（読めない圧縮方式）もここに来る
                if "password" in str(e).lower():
                    raise PasswordNeeded(wrong=bool(password))
                raise _ZipReadError(f"「{name}」を読めませんでした（{e}）")
            except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError,
                    zlib.error, lzma.LZMAError, OSError) as e:
                if isinstance(e, OSError) and e.errno is not None:
                    raise  # 書き込み側の問題（空き容量不足など）はそのまま知らせる
                # このファイルだけ壊れている → 作りかけを消して、次のファイルへ
                try:
                    os.remove(target)
                except OSError:
                    pass
                damaged.append(name)
                done = start_done + info.file_size
                continue
            try:
                os.utime(target, (time.time(), time.mktime(info.date_time + (0, 0, -1))))
            except (OverflowError, ValueError, OSError):
                pass
            if unix_mode & 0o777:
                os.chmod(target, unix_mode & 0o777 | 0o600)  # 自分は必ず読み書きできるように
    if damaged and len(damaged) == files:
        # 全部読めないのは、zip の中の位置情報の崩れなど。7z ならいけるかもしれない
        raise _ZipReadError(f"どのファイルも読めませんでした（{len(damaged)} 個）")
    return Result(dest, skipped, damaged=damaged)


def _extract_tar(path, dest, progress, cancel):
    skipped = []
    try:
        t = tarfile.open(path, "r:*")
    except (tarfile.TarError, OSError, EOFError) as e:
        raise PackError(f"読み取れません: {e}")
    total = os.path.getsize(path) or 1
    member = None
    with t:
        try:
            for member in t:
                _check(cancel)
                try:
                    # filter="data" は、外へ出る・危ないリンクなどを拒否する安全な展開方法
                    t.extract(member, dest, filter="data")
                except tarfile.FilterError:
                    skipped.append(member.name)
                    continue
                pos = _tell(t.fileobj)
                progress(min(pos / total, 1.0) if pos else None, member.name)
            # gzip などは最後まで読んだときに初めてチェック値（CRC）を確かめる。
            # tar の終わりの印で止めると壊れに気づかないので、最後まで読み切る。
            while t.fileobj.read(CHUNK):
                _check(cancel)
        except (tarfile.TarError, EOFError, zlib.error, lzma.LZMAError, OSError) as e:
            if isinstance(e, OSError) and e.errno is not None:
                raise  # 書き込み側の問題はそのまま知らせる
            if not os.listdir(dest):
                raise PackError(_BROKEN + f"解凍できませんでした: {e}")
            if member is not None and _all_members_done(t, member):
                # 全部取り出せたが、最後のチェック値が合わない → どれかが壊れている
                return Result(dest, skipped, damaged=["（どのファイルかは特定できません）"],
                              note="圧縮ファイルのチェック値が合いません。取り出したファイルのどれかが"
                                   "壊れている可能性があります。大事なファイルは中身を確かめてください")
            # tar は先頭から順に読むので、壊れた所より後ろは取り出せない
            label = f"「{member.name}」以降" if member is not None else "途中から後ろ"
            _remove_partial(dest, member)
            return Result(dest, skipped, damaged=[label],
                          note="途中から壊れていたため、それより前のファイルだけ取り出しました")
    return Result(dest, skipped)


def _all_members_done(t, last):
    try:
        return t.members and t.members[-1] is last and t._loaded
    except AttributeError:
        return False


def _remove_partial(dest, member):
    """途中で切れて作りかけになったファイルを消す。"""
    if member is None or not member.isfile():
        return
    parts = _safe_parts(member.name)
    if parts:
        target = os.path.join(dest, *parts)
        try:
            if os.path.isfile(target) and os.path.getsize(target) != member.size:
                os.remove(target)
        except OSError:
            pass


_PERCENT = re.compile(rb"(\d{1,3})%")


def _run_with_progress(cmd, progress, cancel, label, cwd=None):
    """外部コマンドを動かし、出力の「45%」を読んで進み具合にする。(終了コード, 出力) を返す。"""
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, cwd=cwd)
    except OSError as e:
        raise PackError(f"{os.path.basename(cmd[0])} を実行できません: {e}")
    out = bytearray()
    os.set_blocking(proc.stdout.fileno(), False)
    try:
        while True:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise Cancelled()
            try:
                chunk = proc.stdout.read(4096)
            except BlockingIOError:
                chunk = None
            if chunk:
                out += chunk
                found = _PERCENT.findall(chunk)
                if found:
                    progress(min(int(found[-1]) / 100, 1.0), label)
            elif proc.poll() is not None:
                rest = proc.stdout.read() or b""
                out += rest
                break
            else:
                time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return proc.returncode, out.decode("utf-8", "replace")


def _password_problem(text):
    t = text.lower()
    return any(k in t for k in ("wrong password", "incorrect password", "encrypted", "password is incorrect",
                                "can not open encrypted", "enter password"))


def _extract_7z(path, mime, dest, password, progress, cancel):
    cmd = sevenzip_command()
    if cmd is None:
        raise PackError("この形式の解凍には 7z コマンドが必要です。\n端末で `sudo apt install 7zip` を実行してください。")
    # パスワードがないときもダミーを渡す（7z が入力待ちで止まらないように）
    pw = password if password else "kiwami-no-password"
    args = [cmd, "x", f"-o{dest}", "-aou", "-bsp1", "-bso0", "-bse1", "-sccUTF-8", f"-p{pw}", "--", path]
    code, out = _run_with_progress(args, progress, cancel, os.path.basename(path))
    if code == 0:
        return Result(dest)
    if _password_problem(out):
        raise PasswordNeeded(wrong=bool(password))
    damaged = _damaged_from_7z(out)
    if damaged and _has_files(dest):
        _remove_listed(dest, damaged)
        return Result(dest, damaged=damaged)
    if "unsupported method" in out.lower() and mime in RAR_MIMES:
        raise PackError("rar の解凍には unrar が必要です。\n端末で `sudo apt install unrar` を実行してください。")
    if code == 1:  # 警告（一部のファイルが書き出せなかったなど）
        return Result(dest, note="一部のファイルで警告がありました:\n" + _last_lines(out))
    raise PackError(_broken_hint(out) + "解凍できませんでした:\n" + _last_lines(out))


_BROKEN = ("圧縮ファイルが壊れているようです。ダウンロードやコピーが途中で切れた可能性があります。"
           "元の場所からもう一度取ってくると直ることがあります。\n\n")

_7Z_DAMAGE = re.compile(r"ERROR:\s*(?:Data Error|CRC Failed|Unexpected end of data|"
                        r"Unsupported Method|There are data after the end of archive)\s*:\s*(.+)$")


def _damaged_from_7z(out):
    names = []
    for line in out.replace("\b", "").splitlines():
        m = _7Z_DAMAGE.search(line.strip())
        if m and m.group(1).strip() not in names:
            names.append(m.group(1).strip())
    return names


def _has_files(dest):
    for _, _, files in os.walk(dest):
        if files:
            return True
    return False


def _remove_listed(dest, names):
    """壊れていると報告されたファイルが作りかけで残っていれば消す。"""
    for name in names:
        parts = _safe_parts(name)
        if not parts:
            continue
        target = os.path.join(dest, *parts)
        try:
            if os.path.isfile(target) and not os.path.islink(target):
                os.remove(target)
        except OSError:
            pass


def _broken_hint(out):
    t = out.lower()
    if any(k in t for k in ("data error", "crc failed", "unexpected end", "headers error",
                            "is not archive", "there are data after the end")):
        return _BROKEN
    return ""


def _extract_unrar(path, dest, password, progress, cancel):
    pw = f"-p{password}" if password else "-p-"
    args = [shutil.which("unrar"), "x", "-o+", "-y", pw, "--", path, dest + os.sep]
    code, out = _run_with_progress(args, progress, cancel, os.path.basename(path))
    if code == 0:
        return Result(dest)
    if code == 11 or _password_problem(out):  # 11: パスワード違い
        raise PasswordNeeded(wrong=bool(password))
    damaged = _damaged_from_unrar(out)
    if code == 3 and not damaged and password is None and _has_encrypted_hint(out):
        raise PasswordNeeded()
    if damaged and _has_files(dest):
        _remove_listed(dest, damaged)
        return Result(dest, damaged=damaged)
    if code == 1:
        return Result(dest, note="一部のファイルで警告がありました:\n" + _last_lines(out))
    raise PackError("解凍できませんでした:\n" + _last_lines(out))


_UNRAR_DAMAGE = [
    re.compile(r"^(.+?)\s+-\s+(?:checksum error|CRC failed)", re.I),
    re.compile(r"(?:checksum error|CRC failed) in (?:the encrypted file )?(.+?)\.?\s*(?:Corrupt file or wrong password\.)?$", re.I),
]


def _damaged_from_unrar(out):
    names = []
    for line in out.replace("\b", "").splitlines():
        line = line.strip()
        for pat in _UNRAR_DAMAGE:
            m = pat.search(line)
            if m:
                name = m.group(1).strip()
                if name and name not in names:
                    names.append(name)
                break
    return names


def _has_encrypted_hint(out):
    return "encrypted" in out.lower()


def _last_lines(text, n=4):
    lines = [line.strip() for line in text.replace("\b", "").splitlines() if line.strip()]
    return "\n".join(lines[-n:])


# ------------------------------------------------------------------ 圧縮

def default_archive_name(paths, fmt):
    ext = FORMATS[fmt][1]
    if len(paths) == 1:
        p = paths[0].rstrip("/")
        base = os.path.basename(p) if os.path.isdir(p) else split_name(os.path.basename(p))[0]
    else:
        base = os.path.basename(os.path.dirname(paths[0].rstrip("/"))) or "まとめ"
    return f"{base}.{ext}"


def _walk(paths, skipped):
    """(実際のパス, 圧縮ファイルの中での名前, フォルダーか) を順に返す。シンボリックリンクは入れない。"""
    for top in paths:
        top = top.rstrip("/")
        base = os.path.basename(top)
        if os.path.islink(top):
            skipped.append(base)
            continue
        if not os.path.isdir(top):
            yield top, base, False
            continue
        yield top, base, True
        for root, dirs, files in os.walk(top):
            rel_root = os.path.relpath(root, os.path.dirname(top))
            for d in list(dirs):
                full = os.path.join(root, d)
                if os.path.islink(full):
                    skipped.append(os.path.join(rel_root, d))
                    dirs.remove(d)
                    continue
                yield full, os.path.join(rel_root, d), True
            for f in files:
                full = os.path.join(root, f)
                if os.path.islink(full) or not os.path.isfile(full):
                    skipped.append(os.path.join(rel_root, f))
                    continue
                yield full, os.path.join(rel_root, f), False


def _total_size(entries):
    total = 0
    for full, _, is_dir in entries:
        if not is_dir:
            try:
                total += os.path.getsize(full)
            except OSError:
                pass
    return total or 1


def create(paths, name, fmt, password=None, hide_names=False, zip_crypto=False, progress=None, cancel=None):
    """paths（同じフォルダーにあるもの）をまとめて圧縮し、Result を返す。"""
    progress = progress or (lambda *a: None)
    if not paths:
        raise PackError("圧縮するものがありません")
    folders = {os.path.dirname(p.rstrip("/")) for p in paths}
    if len(folders) != 1:
        raise PackError("同じフォルダーにあるものだけ、まとめて圧縮できます")
    folder = folders.pop()
    if fmt not in FORMATS:
        raise PackError("この形式には対応していません")
    if password and not FORMATS[fmt][2]:
        raise PackError(f"{fmt} はパスワードを付けられません")
    if password and fmt == "zip":
        reason = zip_password_problem(password)
        if reason:
            raise PackError(reason)
    if "/" in name or not name.strip():
        raise PackError("ファイル名が正しくありません")

    temp = os.path.join(folder, f".{name}.kiwami-{secrets.token_hex(4)}.part")
    skipped = []
    try:
        if fmt == "zip" and not password:
            _create_zip(paths, temp, skipped, progress, cancel)
        elif fmt in ("zip", "7z"):
            _create_7z(paths, folder, temp, fmt, password, hide_names, zip_crypto, skipped, progress, cancel)
        else:
            _create_tar(paths, temp, fmt, skipped, progress, cancel)
        _check(cancel)
        # できあがってから、上書きしない名前の変更で本当の名前にする
        while True:
            final = unique_path(folder, name, False)
            try:
                rename_no_replace(temp, final)
                break
            except FileExistsError:
                continue
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise
    return Result(final, skipped)


def zip_password_problem(password):
    """zip のパスワードとして使えなければ理由を返す。zip は半角文字しか正しく扱えない。"""
    if not all(0x20 < ord(c) < 0x7F for c in password):
        return "zip のパスワードには、半角の英数字と記号だけが使えます（日本語や空白は使えません）"
    return ""


def _create_zip(paths, temp, skipped, progress, cancel):
    entries = list(_walk(paths, skipped))
    total = _total_size(entries)
    done = 0
    with zipfile.ZipFile(temp, "x", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for full, arc, is_dir in entries:
            _check(cancel)
            if is_dir:
                z.write(full, arc)  # フォルダーの項目（空のフォルダーも残るように）
                continue
            info = zipfile.ZipInfo.from_file(full, arc)
            info.compress_type = zipfile.ZIP_DEFLATED
            try:
                with open(full, "rb") as src, z.open(info, "w", force_zip64=True) as dst:
                    while True:
                        _check(cancel)
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        dst.write(chunk)
                        done += len(chunk)
                        progress(min(done / total, 1.0), arc)
            except PermissionError:
                raise PackError(f"「{arc}」を読めません（権限がありません）")


def _create_7z(paths, folder, temp, fmt, password, hide_names, zip_crypto, skipped, progress, cancel):
    cmd = sevenzip_command()
    if cmd is None:
        raise PackError("この圧縮には 7z コマンドが必要です。\n端末で `sudo apt install 7zip` を実行してください。")
    names = []
    for p in paths:
        p = p.rstrip("/")
        if os.path.islink(p):
            skipped.append(os.path.basename(p))
            continue
        names.append(os.path.basename(p))
    if not names:
        raise PackError("圧縮できるものがありません")
    args = [cmd, "a", f"-t{fmt}", "-bsp1", "-bso0", "-bse1", "-sccUTF-8", "-y"]
    if password:
        args.append(f"-p{password}")
        if fmt == "zip":
            args.append("-mem=ZipCrypto" if zip_crypto else "-mem=AES256")
        elif hide_names:
            args.append("-mhe=on")
    args += ["--", temp] + [os.path.join(".", n) for n in names]
    code, out = _run_with_progress(args, progress, cancel, os.path.basename(temp), cwd=folder)
    if code != 0 or not os.path.exists(temp):
        raise PackError("圧縮できませんでした:\n" + _last_lines(out))


def _create_tar(paths, temp, fmt, skipped, progress, cancel):
    mode = {"tar.gz": "x:gz", "tar.xz": "x:xz"}[fmt]
    entries = list(_walk(paths, skipped))
    total = _total_size(entries)
    done = 0
    with tarfile.open(temp, mode) as t:
        for full, arc, is_dir in entries:
            _check(cancel)
            try:
                info = t.gettarinfo(full, arcname=arc)
                if is_dir:
                    t.addfile(info)
                    continue
                with open(full, "rb") as src:
                    reader = _WatchedReader(src, cancel, lambda n, arc=arc: progress(min((done + n) / total, 1.0), arc))
                    t.addfile(info, reader)
                done += info.size
            except PermissionError:
                raise PackError(f"「{arc}」を読めません（権限がありません）")
            progress(min(done / total, 1.0), arc)


class _WatchedReader:
    """読むたびに中止を確かめ、読んだ量を知らせる（大きなファイルでもすぐ止められるように）。"""

    def __init__(self, f, cancel, report):
        self.f = f
        self.cancel = cancel
        self.report = report
        self.count = 0

    def read(self, size=-1):
        _check(self.cancel)
        data = self.f.read(size)  # tarfile は頼んだ量ちょうどを期待するので、そのまま読む
        self.count += len(data)
        self.report(self.count)
        return data
