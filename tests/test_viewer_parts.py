import gzip
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

from kiwami import archive, textenc

JA = "吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。\n"


class TextEncodingTest(unittest.TestCase):
    def test_utf8(self):
        self.assertEqual(textenc.detect(JA.encode("utf-8")), "utf-8")

    def test_ascii(self):
        self.assertEqual(textenc.detect(b"hello world\n"), "ascii")

    def test_shift_jis(self):
        self.assertEqual(textenc.detect(JA.encode("cp932") * 3), "cp932")

    def test_euc_jp(self):
        self.assertEqual(textenc.detect(JA.encode("euc_jp") * 3), "euc_jp")

    def test_jis(self):
        self.assertEqual(textenc.detect(JA.encode("iso2022_jp")), "iso2022_jp")

    def test_short_shift_jis(self):
        # 短い文でも Shift_JIS と EUC-JP を取り違えない
        for word in ("テスト", "日本語", "ファイル名", "了解です"):
            self.assertEqual(textenc.detect(word.encode("cp932")), "cp932", word)
            self.assertEqual(textenc.detect(word.encode("euc_jp")), "euc_jp", word)

    def test_bom(self):
        self.assertEqual(textenc.detect(b"\xef\xbb\xbf" + JA.encode()), "utf-8-sig")
        self.assertEqual(textenc.detect(b"\xff\xfe" + JA.encode("utf-16-le")), "utf-16-le")

    def test_utf16_without_bom(self):
        self.assertEqual(textenc.detect("hello world, this is text\r\n".encode("utf-16-le")), "utf-16-le")

    def test_truncated_in_middle_of_character(self):
        data = (JA * 50).encode("utf-8")
        cut = data[:1001]  # 文字の途中で切れる
        self.assertEqual(textenc.detect(cut, truncated=True), "utf-8")

    def test_decode_replaces_bad_bytes(self):
        self.assertEqual(textenc.decode(b"a\xffb", "utf-8"), "a〓b")

    def test_line_ending(self):
        self.assertEqual(textenc.line_ending(b"a\r\nb\r\n"), "CRLF（Windows）")
        self.assertEqual(textenc.line_ending(b"a\nb\n"), "LF（Linux）")
        self.assertTrue(textenc.line_ending(b"a\r\nb\n").startswith("混在"))
        self.assertEqual(textenc.line_ending(b"abc"), "改行なし")


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_zip(self):
        path = os.path.join(self.dir, "a.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("フォルダー/", "")
            z.writestr("フォルダー/日本語.txt", "x" * 1000)
        entries, note = archive.list_entries(path, "application/zip")
        names = [e.name for e in entries]
        self.assertEqual(names, ["フォルダー/", "フォルダー/日本語.txt"])
        self.assertTrue(entries[0].is_dir)
        self.assertEqual(entries[1].size, 1000)
        self.assertLess(entries[1].packed, 1000)

    def test_windows_zip_with_shift_jis_names(self):
        # Windows で作った zip（UTF-8 の印なし・Shift_JIS の名前）をまねる
        real = "テスト資料.txt".encode("cp932")
        dummy = b"A" * len(real)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(dummy.decode(), "hello")
        data = buf.getvalue().replace(dummy, real)
        path = os.path.join(self.dir, "win.zip")
        with open(path, "wb") as f:
            f.write(data)
        entries, _ = archive.list_entries(path, "application/zip")
        self.assertEqual(entries[0].name, "テスト資料.txt")

    def test_tar_gz(self):
        path = os.path.join(self.dir, "a.tar.gz")
        with tarfile.open(path, "w:gz") as t:
            info = tarfile.TarInfo("中身/メモ.txt")
            info.size = 5
            t.addfile(info, io.BytesIO(b"hello"))
        entries, _ = archive.list_entries(path, "application/gzip")
        self.assertEqual([e.name for e in entries], ["中身/メモ.txt"])
        self.assertEqual(entries[0].size, 5)

    def test_plain_gzip(self):
        path = os.path.join(self.dir, "log.txt.gz")
        with open(path, "wb") as f:
            f.write(gzip.compress(b"hello" * 100))
        entries, note = archive.list_entries(path, "application/gzip")
        self.assertEqual(entries[0].name, "log.txt")
        self.assertIn("1つ", note)

    def test_broken_zip(self):
        path = os.path.join(self.dir, "broken.zip")
        with open(path, "wb") as f:
            f.write(b"PK\x03\x04" + b"\0" * 20)
        with self.assertRaises(archive.ArchiveError):
            archive.list_entries(path, "application/zip")

    @unittest.skipUnless(archive.sevenzip_command(), "7z コマンドがありません")
    def test_7z(self):
        src = os.path.join(self.dir, "元")
        os.mkdir(src)
        with open(os.path.join(src, "日本語の名前.txt"), "w") as f:
            f.write("hello" * 100)
        path = os.path.join(self.dir, "a.7z")
        subprocess.run([archive.sevenzip_command(), "a", "-bd", path, src],
                       capture_output=True, check=True)
        entries, _ = archive.list_entries(path, "application/x-7z-compressed")
        names = sorted(e.name for e in entries)
        self.assertEqual(names, ["元/", "元/日本語の名前.txt"])
        self.assertEqual([e.size for e in entries if not e.is_dir], [500])

    def test_parse_7z_output(self):
        out = (
            "Path = x.rar\nType = Rar\n\n----------\n"
            "Path = フォルダー\nFolder = +\nSize = 0\nModified = 2024-05-06 07:08:09\n\n"
            "Path = フォルダー/秘密.txt\nFolder = -\nSize = 123\nPacked Size = 50\n"
            "Modified = 2024-05-06 07:08:09.1234567\nEncrypted = +\n\n"
        )
        entries, _ = archive._parse_7z_slt(out)
        self.assertEqual([e.name for e in entries], ["フォルダー/", "フォルダー/秘密.txt"])
        self.assertTrue(entries[1].encrypted)
        self.assertEqual(entries[1].size, 123)
        self.assertEqual(entries[1].mtime.year, 2024)

    def test_missing_7z_command_message(self):
        saved = shutil.which
        try:
            archive.shutil.which = lambda name: None
            with self.assertRaises(archive.ArchiveError) as cm:
                archive.list_entries(os.path.join(self.dir, "x.7z"), "application/x-7z-compressed")
            self.assertIn("apt install 7zip", str(cm.exception))
        finally:
            archive.shutil.which = saved


if __name__ == "__main__":
    unittest.main()
