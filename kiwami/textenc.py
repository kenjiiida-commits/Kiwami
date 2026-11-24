"""テキストの文字コード（UTF-8・Shift_JIS・EUC-JP など）を見分ける。"""

import codecs

# (Python での名前, 表示名)
ENCODINGS = [
    ("utf-8", "UTF-8"),
    ("cp932", "Shift_JIS"),
    ("euc_jp", "EUC-JP"),
    ("iso2022_jp", "ISO-2022-JP（JIS）"),
    ("utf-16-le", "UTF-16LE"),
    ("utf-16-be", "UTF-16BE"),
    ("latin-1", "Latin-1（西欧）"),
]
DISPLAY = dict(ENCODINGS)
DISPLAY["utf-8-sig"] = "UTF-8（BOM付き）"
DISPLAY["ascii"] = "ASCII（英数字のみ）"

_BOMS = [
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
]


def _decode_strict(data, encoding, truncated):
    """厳密に読む。truncated なら末尾で途中になった文字は気にしない。"""
    decoder = codecs.getincrementaldecoder(encoding)()
    return decoder.decode(data, final=not truncated)


def _decodes(data, encoding, truncated=False):
    try:
        _decode_strict(data, encoding, truncated)
        return True
    except (UnicodeDecodeError, LookupError):
        return False


def _japanese_score(text):
    """日本語らしさ。ひらがな・カタカナ・よく使う漢字・全角記号の割合。"""
    if not text:
        return 0.0
    good = 0
    for ch in text:
        c = ord(ch)
        if (0x3040 <= c <= 0x30FF or 0x4E00 <= c <= 0x9FFF or 0x3000 <= c <= 0x303F
                or 0xFF01 <= c <= 0xFF5E or ch in "\r\n\t" or 0x20 <= c < 0x7F):
            good += 1
        elif 0xFF61 <= c <= 0xFF9F:  # 半角カナはややまれ
            good += 0.5
    return good / len(text)


def _looks_utf16(data):
    if len(data) < 4:
        return None
    sample = data[:4096]
    even_zero = sample[0::2].count(0)
    odd_zero = sample[1::2].count(0)
    half = len(sample) / 2
    if odd_zero > half * 0.4 and even_zero < half * 0.05:
        return "utf-16-le"
    if even_zero > half * 0.4 and odd_zero < half * 0.05:
        return "utf-16-be"
    return None


def detect(data, truncated=False):
    """バイト列の文字コードを推測して Python での名前を返す。

    truncated=True のときは、末尾で文字が途中で切れているかもしれないので
    最後の数バイトを無視して判定する。
    """
    for bom, name in _BOMS:
        if data.startswith(bom):
            return name
    sample = data
    if not sample:
        return "utf-8"
    utf16 = _looks_utf16(sample)
    if utf16:
        return utf16
    if b"\x1b$B" in sample or b"\x1b$@" in sample or b"\x1b(J" in sample:
        if all(b < 0x80 for b in sample[:65536]) and _decodes(sample, "iso2022_jp", truncated):
            return "iso2022_jp"
    if all(b < 0x80 for b in sample[:65536]) and _decodes(sample, "ascii"):
        return "ascii"
    if _decodes(sample, "utf-8", truncated):
        return "utf-8"
    candidates = []
    for enc in ("cp932", "euc_jp"):
        try:
            text = _decode_strict(sample, enc, truncated)
        except UnicodeDecodeError:
            continue
        candidates.append((_japanese_score(text), enc))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return "latin-1"


def decode(data, encoding):
    """読めない部分は〓に置き換えて文字列にする。"""
    if encoding == "ascii":
        encoding = "utf-8"
    return data.decode(encoding, errors="replace").replace("�", "〓")


def display_name(encoding):
    return DISPLAY.get(encoding, encoding)


def line_ending(data):
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    cr = data.count(b"\r") - crlf
    kinds = [name for name, n in (("CRLF（Windows）", crlf), ("LF（Linux）", lf), ("CR（古いMac）", cr)) if n]
    if not kinds:
        return "改行なし"
    if len(kinds) > 1:
        return "混在：" + "・".join(kinds)
    return kinds[0]
