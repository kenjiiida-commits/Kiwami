"""ファイルの中身を調べて種類と本来の拡張子を判別し、今の拡張子と比べる。"""

import grp
import os
import pwd
import stat
from dataclasses import dataclass, field
from datetime import datetime

from . import textenc
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

# 判定結果
OK = "ok"  # 拡張子と中身が一致
OK_TEXT = "ok_text"  # テキストなので拡張子は自由
MISMATCH = "mismatch"  # 拡張子と中身が違う
NOEXT = "noext"  # 拡張子がない（付けたほうがよい）
NOEXT_OK = "noext_ok"  # 拡張子がないのが普通の形式
UNKNOWN = "unknown"  # 中身が分からず判定できない
MAYBE = "maybe"  # 外れやすい判別で、拡張子と違う（確実ではない）

DOUBLE_EXTS = ("tar.gz", "tar.bz2", "tar.xz", "tar.zst")


@dataclass
class FileInfo:
    path: str
    name: str
    kind: str  # "file" / "dir" / "symlink" / "other" / "missing"
    size: int = 0
    mtime: datetime | None = None
    atime: datetime | None = None
    mode: int = 0  # 属性（パーミッション）の数字。例: 0o644
    owner: str = ""
    group: str = ""
    description: str = ""  # 判別エンジンの説明（英語）
    mime: str = ""
    encoding: str = ""
    magic_exts: list[str] = field(default_factory=list)  # 判別エンジンが挙げた拡張子
    current_ext: str = ""  # 小文字・ドットなし
    error: str = ""
    type_name: str = ""  # 日本語の名前
    from_signature: bool = False  # 辞書の先頭バイト列で判別した
    status: str | None = None
    suggestions: list[str] = field(default_factory=list)  # 直す候補（"" は拡張子を外す）
    txt_name: str = ""  # .txt にしてもよいテキストなら、その新しい名前（そうでなければ ""）


# 意味のある拡張子（.txt にしようとは言わない）。辞書のテキスト系の拡張子もこれに加わる。
KNOWN_TEXT_EXTS = {
    "txt", "text", "md", "markdown", "rst", "adoc", "org", "tex", "bib", "log", "ini", "cfg", "conf",
    "config", "toml", "yaml", "yml", "json", "jsonl", "xml", "html", "htm", "xhtml", "css", "scss", "less",
    "js", "mjs", "cjs", "ts", "tsx", "jsx", "vue", "py", "pyw", "pyi", "sh", "bash", "zsh", "fish", "bat",
    "cmd", "ps1", "c", "h", "cc", "cpp", "cxx", "hpp", "hh", "java", "kt", "kts", "go", "rs", "rb", "php",
    "pl", "pm", "lua", "swift", "dart", "scala", "cs", "vb", "fs", "r", "m", "jl", "hs", "ml", "clj", "lisp",
    "el", "vim", "sql", "csv", "tsv", "srt", "vtt", "ass", "ssa", "sub", "lrc", "cue", "m3u", "m3u8", "pls",
    "desktop", "service", "socket", "timer", "properties", "env", "diff", "patch", "po", "pot", "ics", "vcf",
    "eml", "mbox", "nfo", "url", "reg", "inf", "asm", "s", "gradle", "cmake", "mk", "mak", "dockerfile",
    "gitignore", "gitattributes", "editorconfig", "lock", "sum", "mod", "asc", "pem", "crt", "csr", "key",
    "pub", "svg", "rss", "atom", "kml", "gpx", "gml", "plist", "strings", "rc", "manifest", "webmanifest",
    "map", "ipynb", "arb", "lst", "cnf", "sample", "tmpl", "template", "j2", "ejs", "hbs", "mustache",
}
# 拡張子なしが普通のファイル名（大文字小文字は区別しない）
KNOWN_TEXT_NAMES = {
    "readme", "license", "licence", "copying", "authors", "contributors", "changelog", "changes", "news",
    "install", "todo", "makefile", "gnumakefile", "dockerfile", "containerfile", "vagrantfile", "gemfile",
    "rakefile", "procfile", "brewfile", "jenkinsfile", "pipfile", "notice", "version", "manifest",
}


def _plausible_ext(ext):
    """拡張子らしいか（英数字 1〜6 文字で、数字だけではない）。"""
    return 1 <= len(ext) <= 6 and ext.isascii() and ext.isalnum() and not ext.isdigit()


def txt_rename_target(info, text_exts=()):
    """.txt にしてもよさそうなテキストなら新しい名前を、そうでなければ "" を返す。"""
    if info.kind != "file" or info.error or info.status != OK_TEXT or info.mime != "text/plain":
        return ""
    name = info.name
    if name.startswith("."):
        return ""  # .bashrc などの設定ファイル
    stem, ext = split_name(name)
    if stem.lower() in KNOWN_TEXT_NAMES or name.lower() in KNOWN_TEXT_NAMES:
        return ""
    if info.mode & 0o111:
        return ""  # 実行できる印が付いている（スクリプトかもしれない）
    cur = ext.lower()
    if cur and (cur in KNOWN_TEXT_EXTS or cur in text_exts or cur.rsplit(".", 1)[-1] in KNOWN_TEXT_EXTS):
        return ""
    if cur and _plausible_ext(cur):
        return f"{stem}.txt"  # 見慣れない拡張子（.dat など）は .txt に置き換える
    return f"{name}.txt"  # 拡張子なし、または「v1.2」の「2」のように拡張子らしくないものは付け足す


def split_name(name):
    """ファイル名を（拡張子より前, 拡張子）に分ける。拡張子は元の大文字小文字のまま。

    .tar.gz のような二重拡張子は1つとして扱う。先頭がドットの隠しファイル
    （.bashrc など）は拡張子なしとみなす。
    """
    leading = len(name) - len(name.lstrip("."))
    body = name[leading:]
    if "." not in body:
        return name, ""
    lower = body.lower()
    for double in DOUBLE_EXTS:
        if lower.endswith("." + double) and len(body) > len(double) + 1:
            cut = len(name) - len(double)
            return name[:cut - 1], name[cut:]
    stem, ext = name.rsplit(".", 1)
    if not ext or stem == "." * leading:
        return name, ""
    return stem, ext


def current_extension(name):
    return split_name(name)[1].lower()


def parse_extensions(raw):
    """判別エンジンの拡張子出力（例: "jpeg/jpg/jpe/jfif"、不明なら "???"）を一覧にする。"""
    raw = raw.strip()
    if not raw or raw == "???":
        return []
    return [e.strip().lower() for e in raw.split("/") if e.strip()]


def name_with_extension(name, new_ext):
    """拡張子を new_ext（"" なら拡張子なし）に付け替えたファイル名。"""
    stem, _ = split_name(name)
    return f"{stem}.{new_ext}" if new_ext else stem


def _ext_in(ext, accepted):
    if not ext:
        return False
    if ext in accepted:
        return True
    last = ext.rsplit(".", 1)[-1]
    return last in accepted


def judge(info, entry, binary_exts):
    """今の拡張子が中身に合っているかを決め、info.status と info.suggestions を埋める。"""
    if info.kind != "file" or info.error:
        info.status = None
        return
    magic_exts = info.magic_exts
    if entry is not None:
        suggestions = list(entry.exts) or list(magic_exts if not entry.noext_ok else [])
        accepted = set(entry.exts) | set(entry.also) | set(magic_exts)
        is_text = entry.text
        if entry.noext_ok:
            suggestions.append("")
    else:
        suggestions = list(magic_exts)
        accepted = set(magic_exts)
        is_text = info.mime.startswith("text/")
    # 重複を除く（順番は保つ）
    seen = set()
    info.suggestions = [s for s in suggestions if not (s in seen or seen.add(s))]

    cur = info.current_ext
    last = cur.rsplit(".", 1)[-1] if cur else ""

    if entry is not None and entry.any_ext:
        info.status = UNKNOWN if entry.mime == "application/octet-stream" else OK
    elif is_text:
        if _ext_in(cur, accepted):
            info.status = OK
        elif cur and last in binary_exts:
            # テキストなのに画像などの拡張子が付いている
            info.status = MISMATCH
            if not info.suggestions:
                info.suggestions = ["txt"]
        else:
            info.status = OK_TEXT
    elif not accepted and not info.suggestions:
        info.status = UNKNOWN
    elif not cur:
        info.status = NOEXT_OK if (entry is not None and entry.noext_ok) else NOEXT
    elif _ext_in(cur, accepted):
        info.status = OK
    elif entry is not None and entry.weak:
        info.status = MAYBE
    else:
        info.status = MISMATCH
    if entry is not None and entry.weak and info.status == NOEXT:
        info.status = MAYBE
    if info.status in (OK, OK_TEXT, NOEXT_OK, UNKNOWN):
        # 今のままでよいときは、今と同じ候補を出さない
        info.suggestions = [s for s in info.suggestions if s != cur]


def _user_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _group_name(gid):
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def inspect(path, db=None):
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
    info.atime = datetime.fromtimestamp(st.st_atime)
    info.mode = stat.S_IMODE(st.st_mode)
    info.owner = _user_name(st.st_uid)
    info.group = _group_name(st.st_gid)
    if stat.S_ISLNK(st.st_mode):
        info.kind = "symlink"
        info.type_name = "シンボリックリンク"
        try:
            info.description = "→ " + os.readlink(path)
        except OSError:
            pass
        return info
    if stat.S_ISDIR(st.st_mode):
        info.kind = "dir"
        info.type_name = "フォルダー"
        return info
    if not stat.S_ISREG(st.st_mode):
        info.type_name = "特殊ファイル（デバイス・FIFO・ソケットなど）"
        return info

    info.kind = "file"
    info.size = st.st_size
    try:
        info.description = _describe.from_file(path)
        info.mime = _mime.from_file(path)
        info.encoding = _encoding.from_file(path)
        info.magic_exts = parse_extensions(_extension.from_file(path))
    except MagicError as e:
        info.error = str(e)
        return info

    entry = None
    if db is not None:
        # 自分用辞書の先頭バイト列は常に優先。組み込みのものは判別エンジンが分からないときだけ。
        sig = db.match_signature(path, user=True)
        if sig is None and info.mime == "application/octet-stream":
            sig = db.match_signature(path, user=False)
        if sig is not None:
            entry = sig
            info.mime = sig.mime
            info.magic_exts = []
            info.from_signature = True
        else:
            entry = db.lookup(info.mime)
        info.type_name = entry.name if entry else ""
        judge(info, entry, db.binary_exts)
    if db is not None:
        info.txt_name = txt_rename_target(info, db.text_exts)
    if info.mime.startswith("text/") or info.status == OK_TEXT or info.mime == "application/json":
        # 日本語の文字コードは判別エンジンより自前の判定のほうが正確
        try:
            with open(path, "rb") as f:
                head = f.read(65536)
            info.encoding = textenc.display_name(textenc.detect(head, truncated=info.size > len(head)))
        except OSError:
            pass
    return info


def format_size(n):
    if n < 1024:
        return f"{n:,} バイト"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}（{n:,} バイト）"


def format_size_short(n):
    """一覧用の短い表示（例: 1.2 KB）。"""
    if n < 1024:
        return f"{n:,} B"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}"
