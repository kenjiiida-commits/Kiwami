"""形式の辞書（組み込み辞書＋自分用辞書）。

自分用辞書は ~/.config/kiwami/types.json に置く。書式は組み込み辞書
（kiwami/data/types.json）と同じで、同じ MIME の項目は自分用が優先される。
ファイルを書き換えると、次にファイルをドロップしたときに自動で読み直す。
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BUILTIN_PATH = Path(__file__).parent / "data" / "types.json"


def user_dict_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "kiwami" / "types.json"


USER_TEMPLATE = {
    "_説明": (
        "自分用の辞書です。\"types\" の中に項目を追加すると、極み窓の判別に反映されます。"
        "同じ MIME の項目は組み込み辞書より優先されます。書き方は \"_例\" を参考にしてください"
        "（\"_例\" の中身は使われません）。"
    ),
    "_例": [
        {
            "mime": ["application/x-jikkyo-data"],
            "name": "自作ソフトのデータ",
            "exts": ["jkd"],
            "signature": {"offset": 0, "hex": "4A4B4430"},
        },
        {
            "mime": ["text/markdown"],
            "name": "Markdown 文書",
            "exts": ["md"],
            "also": ["markdown"],
            "text": True,
        },
    ],
    "types": [],
}


@dataclass
class TypeEntry:
    mimes: list[str]
    name: str
    exts: list[str] = field(default_factory=list)
    also: list[str] = field(default_factory=list)
    text: bool = False
    any_ext: bool = False
    noext_ok: bool = False
    weak: bool = False
    sig_offset: int = 0
    sig_bytes: bytes = b""

    @property
    def mime(self):
        return self.mimes[0]


def _clean_exts(values):
    result = []
    for v in values or []:
        v = str(v).strip().lower().lstrip(".")
        if v and v not in result:
            result.append(v)
    return result


def _parse_entry(raw):
    if not isinstance(raw, dict):
        raise ValueError("項目が { } の形になっていません")
    mimes = raw.get("mime")
    if isinstance(mimes, str):
        mimes = [mimes]
    if not mimes or not all(isinstance(m, str) and m for m in mimes):
        raise ValueError("\"mime\" がありません")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{mimes[0]} に \"name\" がありません")
    entry = TypeEntry(
        mimes=list(mimes),
        name=name,
        exts=_clean_exts(raw.get("exts")),
        also=_clean_exts(raw.get("also")),
        text=bool(raw.get("text", False)),
        any_ext=bool(raw.get("any_ext", False)),
        noext_ok=bool(raw.get("noext_ok", False)),
        weak=bool(raw.get("weak", False)),
    )
    sig = raw.get("signature")
    if sig is not None:
        try:
            entry.sig_offset = int(sig.get("offset", 0))
            entry.sig_bytes = bytes.fromhex(str(sig["hex"]).replace(" ", ""))
        except (AttributeError, KeyError, ValueError, TypeError):
            raise ValueError(f"{mimes[0]} の \"signature\" の書き方が正しくありません")
        if entry.sig_offset < 0 or not entry.sig_bytes:
            raise ValueError(f"{mimes[0]} の \"signature\" の書き方が正しくありません")
    return entry


def _load_file(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("types"), list):
        raise ValueError("\"types\" の一覧がありません")
    return [_parse_entry(raw) for raw in data["types"]]


class TypeDB:
    def __init__(self, builtin_path=BUILTIN_PATH, user_path=None):
        self.builtin_path = Path(builtin_path)
        self.user_path = Path(user_path) if user_path else user_dict_path()
        self.user_error = ""
        self._user_mtime = None
        self._builtin = _load_file(self.builtin_path)
        self._user = []
        self._rebuild()
        self.reload_if_changed()

    def _rebuild(self):
        self.by_mime = {}
        for entry in self._builtin + self._user:  # 後から入れた自分用が上書き
            for m in entry.mimes:
                self.by_mime[m.lower()] = entry
        entries = list({id(e): e for e in self.by_mime.values()}.values())
        user_ids = {id(e) for e in self._user}
        sigs = [e for e in entries if e.sig_bytes]
        self.user_signatures = [e for e in sigs if id(e) in user_ids]
        self.builtin_signatures = [e for e in sigs if id(e) not in user_ids]
        self.binary_exts = set()
        for e in entries:
            if not e.text and not e.any_ext:
                self.binary_exts.update(e.exts)
                self.binary_exts.update(x for x in e.also if "." not in x)

    def reload_if_changed(self):
        """自分用辞書が書き換えられていたら読み直す。変化があれば True。"""
        try:
            mtime = self.user_path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime = None
        except OSError as e:
            self.user_error = f"自分用辞書を開けません: {e.strerror}"
            return False
        if mtime == self._user_mtime:
            return False
        self._user_mtime = mtime
        self.user_error = ""
        self._user = []
        if mtime is not None:
            try:
                self._user = _load_file(self.user_path)
            except json.JSONDecodeError as e:
                self.user_error = f"自分用辞書の {e.lineno} 行目に書き間違いがあります（{e.msg}）"
            except (OSError, UnicodeDecodeError, ValueError) as e:
                self.user_error = f"自分用辞書を読み込めません: {e}"
        self._rebuild()
        return True

    def ensure_user_file(self):
        """自分用辞書がなければひな形を作り、そのパスを返す。"""
        if not self.user_path.exists():
            self.user_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.user_path, "x", encoding="utf-8") as f:
                json.dump(USER_TEMPLATE, f, ensure_ascii=False, indent=2)
                f.write("\n")
        return self.user_path

    def lookup(self, mime):
        return self.by_mime.get((mime or "").lower())

    def match_signature(self, path, user=True):
        """先頭バイト列で判別する。user=True なら自分用辞書、False なら組み込み辞書の項目を使う。"""
        sigs = self.user_signatures if user else self.builtin_signatures
        if not sigs:
            return None
        need = max(e.sig_offset + len(e.sig_bytes) for e in sigs)
        if need > 1024 * 1024:
            need = 1024 * 1024
        try:
            with open(path, "rb") as f:
                head = f.read(need)
        except OSError:
            return None
        for e in sigs:
            if head[e.sig_offset:e.sig_offset + len(e.sig_bytes)] == e.sig_bytes:
                return e
        return None
