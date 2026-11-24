import gzip
import json
import os
import shutil
import tempfile
import unittest
import zipfile

from kiwami.detect import (
    MAYBE,
    MISMATCH,
    NOEXT,
    NOEXT_OK,
    OK,
    OK_TEXT,
    UNKNOWN,
    current_extension,
    inspect,
    name_with_extension,
    parse_extensions,
    split_name,
)
from kiwami.typesdb import TypeDB

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class NameTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(current_extension("a.JPG"), "jpg")
        self.assertEqual(split_name("a.JPG"), ("a", "JPG"))

    def test_none(self):
        self.assertEqual(current_extension("README"), "")

    def test_hidden_file(self):
        self.assertEqual(current_extension(".bashrc"), "")
        self.assertEqual(current_extension(".config.json"), "json")

    def test_trailing_dot(self):
        self.assertEqual(split_name("memo."), ("memo.", ""))

    def test_double(self):
        self.assertEqual(current_extension("backup.tar.gz"), "tar.gz")
        self.assertEqual(name_with_extension("backup.tar.gz", "zip"), "backup.zip")

    def test_name_with_extension(self):
        self.assertEqual(name_with_extension("写真.jpg", "png"), "写真.png")
        self.assertEqual(name_with_extension("写真", "png"), "写真.png")
        self.assertEqual(name_with_extension("prog.jpg", ""), "prog")
        self.assertEqual(name_with_extension("v1.2.final.JPG", "png"), "v1.2.final.png")


class ParseExtensionsTest(unittest.TestCase):
    def test_multi(self):
        self.assertEqual(parse_extensions("jpeg/jpg/jpe/jfif"), ["jpeg", "jpg", "jpe", "jfif"])

    def test_unknown(self):
        self.assertEqual(parse_extensions("???"), [])


class InspectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        # 自分用辞書は一時フォルダーに置く（本物の ~/.config を読まない）
        self.db = TypeDB(user_path=os.path.join(self.dir, "user-types.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _zip(self, name):
        path = os.path.join(self.dir, name)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("a.txt", "hello")
        return path

    def test_png_with_wrong_extension(self):
        info = inspect(self._write("写真.jpg", PNG_1PX), self.db)
        self.assertEqual(info.mime, "image/png")
        self.assertEqual(info.type_name, "PNG 画像")
        self.assertEqual(info.status, MISMATCH)
        self.assertEqual(info.suggestions[0], "png")

    def test_png_ok(self):
        info = inspect(self._write("写真.PNG", PNG_1PX), self.db)
        self.assertEqual(info.status, OK)

    def test_png_without_extension(self):
        info = inspect(self._write("写真", PNG_1PX), self.db)
        self.assertEqual(info.status, NOEXT)
        self.assertEqual(info.suggestions, ["png"])

    def test_zip_family_is_not_warned(self):
        # zip を土台にした形式（apk・jar など）は zip と判別されても警告しない
        for name in ("a.zip", "app.apk", "lib.jar"):
            self.assertEqual(inspect(self._zip(name), self.db).status, OK, name)

    def test_zip_named_pdf(self):
        info = inspect(self._zip("見積書.pdf"), self.db)
        self.assertEqual(info.status, MISMATCH)
        self.assertEqual(info.suggestions, ["zip"])

    def test_gzip_tar_gz(self):
        path = self._write("backup.tar.gz", gzip.compress(b"x" * 100))
        self.assertEqual(inspect(path, self.db).status, OK)

    def test_text_any_extension(self):
        info = inspect(self._write("notes.md", b"# title\nhello world\n"), self.db)
        self.assertIn(info.status, (OK, OK_TEXT))

    def test_text_without_extension(self):
        info = inspect(self._write("README", b"hello world\n"), self.db)
        self.assertEqual(info.status, OK_TEXT)

    def test_text_named_as_image(self):
        info = inspect(self._write("memo.jpg", "日本語の文章です。\n".encode() * 5), self.db)
        self.assertEqual(info.status, MISMATCH)
        self.assertEqual(info.suggestions, ["txt"])

    def test_shift_jis_text(self):
        info = inspect(self._write("memo.txt", "日本語の文章です。\r\n".encode("cp932") * 5), self.db)
        self.assertTrue(info.mime.startswith("text/"))
        self.assertIn(info.status, (OK, OK_TEXT))

    def test_executable(self):
        path = os.path.join(self.dir, "prog")
        shutil.copy("/bin/ls", path)
        info = inspect(path, self.db)
        self.assertEqual(info.status, NOEXT_OK)
        os.rename(path, path + ".jpg")
        info = inspect(path + ".jpg", self.db)
        self.assertEqual(info.status, MISMATCH)
        self.assertEqual(info.suggestions, [""])  # 拡張子を外す

    def test_unknown_data(self):
        info = inspect(self._write("data.bin", b"\x01\x02\x03KIWAMI" + bytes(range(256)) * 4), self.db)
        self.assertEqual(info.mime, "application/octet-stream")
        self.assertEqual(info.status, UNKNOWN)

    def test_weak_detection_is_not_red(self):
        # 判別エンジンが TGA と誤判別しやすいデータ
        info = inspect(self._write("data.bin", bytes(range(256)) * 4), self.db)
        if info.mime == "image/x-tga":
            self.assertEqual(info.status, MAYBE)

    def test_empty(self):
        info = inspect(self._write("empty.jpg", b""), self.db)
        self.assertEqual(info.status, OK)

    def test_builtin_signature_ico(self):
        ico = bytes.fromhex("000001000100101000000100200068040000160000") + bytes(1128)
        info = inspect(self._write("icon.png", ico), self.db)
        if info.from_signature or info.mime in ("image/vnd.microsoft.icon", "image/x-icon"):
            self.assertEqual(info.status, MISMATCH)
            self.assertEqual(info.suggestions[0], "ico")

    def test_directory(self):
        info = inspect(self.dir, self.db)
        self.assertEqual(info.kind, "dir")
        self.assertIsNone(info.status)

    def test_symlink(self):
        target = self._write("t.txt", b"x")
        link = os.path.join(self.dir, "link")
        os.symlink(target, link)
        self.assertEqual(inspect(link, self.db).kind, "symlink")

    def test_missing(self):
        self.assertEqual(inspect(os.path.join(self.dir, "nothing"), self.db).kind, "missing")


class TxtOfferTest(unittest.TestCase):
    """テキストを .txt にするボタンを出すかどうか。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.db = TypeDB(user_path=os.path.join(self.dir, "user-types.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def offer(self, name, data="これはテキストのメモです。\nhello world\n", mode=0o644):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(path, mode)
        return inspect(path, self.db).txt_name

    def test_no_extension(self):
        self.assertEqual(self.offer("カシミール3Dみたいなもの"), "カシミール3Dみたいなもの.txt")

    def test_unfamiliar_extension_is_replaced(self):
        self.assertEqual(self.offer("memo.dat"), "memo.txt")
        self.assertEqual(self.offer("記録.bak"), "記録.txt")

    def test_number_like_extension_is_kept(self):
        self.assertEqual(self.offer("議事録 v1.2"), "議事録 v1.2.txt")
        self.assertEqual(self.offer("第3.5版のメモ"), "第3.5版のメモ.txt")

    def test_meaningful_extensions_are_left_alone(self):
        for name in ("a.txt", "notes.md", "app.log", "settings.ini", "data.csv", "a.py", "page.html", "x.TXT"):
            self.assertEqual(self.offer(name), "", name)

    def test_known_names_and_dotfiles(self):
        for name in ("README", "LICENSE", "Makefile", ".bashrc", "readme"):
            self.assertEqual(self.offer(name), "", name)

    def test_scripts_are_left_alone(self):
        self.assertEqual(self.offer("myscript", "#!/bin/bash\necho hi\n"), "")
        self.assertEqual(self.offer("tool", "hello world\n", mode=0o755), "")

    def test_binary_is_not_offered(self):
        path = os.path.join(self.dir, "pic")
        with open(path, "wb") as f:
            f.write(PNG_1PX)
        self.assertEqual(inspect(path, self.db).txt_name, "")


class UserDictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.user = os.path.join(self.dir, "types.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _save(self, data):
        with open(self.user, "w", encoding="utf-8") as f:
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f, ensure_ascii=False)
        # 書き換えを確実に検出させるため更新時刻をずらす
        st = os.stat(self.user)
        os.utime(self.user, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    def test_template_is_valid_and_unused(self):
        db = TypeDB(user_path=self.user)
        db.ensure_user_file()
        self.assertTrue(db.reload_if_changed())
        self.assertEqual(db.user_error, "")
        self.assertIsNone(db.lookup("application/x-jikkyo-data"))

    def test_user_signature_and_override(self):
        db = TypeDB(user_path=self.user)
        self._save({"types": [
            {"mime": "application/x-mine", "name": "自作データ", "exts": ["mine"],
             "signature": {"offset": 0, "hex": "4D 49 4E 45"}},
            {"mime": "image/png", "name": "ぴーえぬじー", "exts": ["png"]},
        ]})
        self.assertTrue(db.reload_if_changed())
        path = os.path.join(self.dir, "x.dat")
        with open(path, "wb") as f:
            f.write(b"MINE" + b"hello world text\n")
        info = inspect(path, db)
        self.assertTrue(info.from_signature)
        self.assertEqual(info.type_name, "自作データ")
        self.assertEqual(info.status, MISMATCH)
        self.assertEqual(info.suggestions, ["mine"])
        self.assertEqual(db.lookup("image/png").name, "ぴーえぬじー")

    def test_broken_json(self):
        db = TypeDB(user_path=self.user)
        self._save('{"types": [\n  {"mime": "a/b", }\n]}')
        db.reload_if_changed()
        self.assertIn("行目", db.user_error)
        # 組み込み辞書はそのまま使える
        self.assertEqual(db.lookup("image/png").name, "PNG 画像")

    def test_missing_name(self):
        db = TypeDB(user_path=self.user)
        self._save({"types": [{"mime": "a/b"}]})
        db.reload_if_changed()
        self.assertIn("name", db.user_error)

    def test_bad_signature(self):
        db = TypeDB(user_path=self.user)
        self._save({"types": [{"mime": "a/b", "name": "x", "signature": {"hex": "zz"}}]})
        db.reload_if_changed()
        self.assertIn("signature", db.user_error)


if __name__ == "__main__":
    unittest.main()
