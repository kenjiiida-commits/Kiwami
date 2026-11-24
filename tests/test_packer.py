import gzip
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
import zipfile

from kiwami import packer
from kiwami.archive import sevenzip_command

HAS_7Z = bool(sevenzip_command())
HAS_RAR = bool(shutil.which("rar") and shutil.which("unrar"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def make_tree(self):
        """圧縮するためのフォルダーを作る。"""
        root = os.path.join(self.dir, "資料")
        os.makedirs(os.path.join(root, "サブ"))
        os.makedirs(os.path.join(root, "空のフォルダー"))
        with open(os.path.join(root, "メモ.txt"), "w") as f:
            f.write("こんにちは\n" * 100)
        with open(os.path.join(root, "サブ", "data.bin"), "wb") as f:
            f.write(bytes(range(256)) * 100)
        return root

    def check_tree(self, root):
        with open(os.path.join(root, "メモ.txt")) as f:
            self.assertEqual(f.read(), "こんにちは\n" * 100)
        with open(os.path.join(root, "サブ", "data.bin"), "rb") as f:
            self.assertEqual(f.read(), bytes(range(256)) * 100)
        self.assertTrue(os.path.isdir(os.path.join(root, "空のフォルダー")))

    def leftovers(self):
        return [n for n in os.listdir(self.dir) if ".kiwami-" in n]

    def roundtrip(self, fmt, mime, password=None, **kw):
        root = self.make_tree()
        res = packer.create([root], f"資料.{packer.FORMATS[fmt][1]}", fmt, password=password, **kw)
        self.assertEqual(os.path.basename(res.path), f"資料.{packer.FORMATS[fmt][1]}")
        shutil.rmtree(root)
        out = packer.extract(res.path, mime, password=password)
        self.assertEqual(os.path.basename(out.path), "資料")
        self.check_tree(os.path.join(out.path, "資料"))
        self.assertEqual(self.leftovers(), [])
        return res.path


class RoundTripTest(Base):
    def test_zip(self):
        self.roundtrip("zip", "application/zip")

    def test_tar_gz(self):
        self.roundtrip("tar.gz", "application/gzip")

    def test_tar_xz(self):
        self.roundtrip("tar.xz", "application/x-xz")

    @unittest.skipUnless(HAS_7Z, "7z がありません")
    def test_7z(self):
        self.roundtrip("7z", "application/x-7z-compressed")

    def test_default_name(self):
        root = self.make_tree()
        self.assertEqual(packer.default_archive_name([root], "zip"), "資料.zip")
        f = os.path.join(root, "メモ.txt")
        self.assertEqual(packer.default_archive_name([f], "tar.gz"), "メモ.tar.gz")
        g = os.path.join(root, "サブ")
        self.assertEqual(packer.default_archive_name([f, g], "7z"), "資料.7z")


class PasswordTest(Base):
    def _expect_password(self, path, mime, wrong):
        before = sorted(os.listdir(self.dir))
        with self.assertRaises(packer.PasswordNeeded) as cm:
            packer.extract(path, mime, password=wrong)
        self.assertEqual(cm.exception.wrong, wrong is not None)
        # 作りかけのフォルダーは残さない
        self.assertEqual(sorted(os.listdir(self.dir)), before)

    @unittest.skipUnless(HAS_7Z, "7z がありません")
    def test_zip_aes(self):
        path = self.roundtrip("zip", "application/zip", password="Himitsu-123")
        self._expect_password(path, "application/zip", None)
        self._expect_password(path, "application/zip", "ちがう")

    @unittest.skipUnless(HAS_7Z, "7z がありません")
    def test_zip_crypto(self):
        path = self.roundtrip("zip", "application/zip", password="secret", zip_crypto=True)
        self._expect_password(path, "application/zip", None)
        self._expect_password(path, "application/zip", "wrong")

    @unittest.skipUnless(HAS_7Z, "7z がありません")
    def test_7z_hidden_names(self):
        path = self.roundtrip("7z", "application/x-7z-compressed", password="pw", hide_names=True)
        self._expect_password(path, "application/x-7z-compressed", None)
        self._expect_password(path, "application/x-7z-compressed", "nope")

    def test_zip_refuses_japanese_password(self):
        root = self.make_tree()
        with self.assertRaises(packer.PackError):
            packer.create([root], "a.zip", "zip", password="ひみつ")

    def test_tar_refuses_password(self):
        root = self.make_tree()
        with self.assertRaises(packer.PackError):
            packer.create([root], "a.tar.gz", "tar.gz", password="x")

    @unittest.skipUnless(HAS_RAR, "rar / unrar がありません")
    def test_rar(self):
        src = os.path.join(self.dir, "元")
        os.mkdir(src)
        with open(os.path.join(src, "日本語.txt"), "w") as f:
            f.write("rar の中身")
        arc = os.path.join(self.dir, "書類.rar")
        subprocess.run(["rar", "a", "-ep1", "-pかぎ", "-idq", arc, src], check=True)
        mime = "application/x-rar"
        self._expect_password(arc, mime, None)
        self._expect_password(arc, mime, "ちがう")
        out = packer.extract(arc, mime, password="かぎ")
        with open(os.path.join(out.path, "元", "日本語.txt")) as f:
            self.assertEqual(f.read(), "rar の中身")


class SafetyTest(Base):
    def test_same_name_gets_number(self):
        root = self.make_tree()
        first = packer.create([root], "資料.zip", "zip").path
        second = packer.create([root], "資料.zip", "zip").path
        self.assertEqual(os.path.basename(second), "資料 (2).zip")
        # 解凍先「資料」は元のフォルダーがあるので「資料 (2)」になる
        out = packer.extract(first, "application/zip")
        self.assertEqual(os.path.basename(out.path), "資料 (2)")
        self.check_tree(os.path.join(out.path, "資料"))
        self.check_tree(root)  # 元のフォルダーはそのまま

    def test_zip_slip_is_blocked(self):
        path = os.path.join(self.dir, "evil.zip")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("../外へ.txt", "bad")
            z.writestr("/etc/絶対パス.txt", "abs")
            z.writestr("ok/普通.txt", "good")
        out = packer.extract(path, "application/zip")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "外へ.txt")))
        self.assertIn("../外へ.txt", out.skipped)
        # 先頭の / は外し、解凍先の中に作る
        self.assertTrue(os.path.exists(os.path.join(out.path, "etc", "絶対パス.txt")))
        self.assertTrue(os.path.exists(os.path.join(out.path, "ok", "普通.txt")))

    def test_tar_unsafe_members_are_skipped(self):
        path = os.path.join(self.dir, "evil.tar.gz")
        with tarfile.open(path, "w:gz") as t:
            for name, data in (("../外へ.txt", b"bad"), ("ok.txt", b"good")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo("link")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            t.addfile(link)
        out = packer.extract(path, "application/gzip")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "外へ.txt")))
        self.assertTrue(os.path.exists(os.path.join(out.path, "ok.txt")))
        self.assertFalse(os.path.lexists(os.path.join(out.path, "link")))
        self.assertEqual(sorted(out.skipped), ["../外へ.txt", "link"])

    def test_windows_zip_names(self):
        real = "提出書類/申請書.txt".encode("cp932")
        dummy = b"A" * len(real)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(dummy.decode(), "hello")
        path = os.path.join(self.dir, "win.zip")
        with open(path, "wb") as f:
            f.write(buf.getvalue().replace(dummy, real))
        out = packer.extract(path, "application/zip")
        self.assertTrue(os.path.exists(os.path.join(out.path, "提出書類", "申請書.txt")))

    def test_single_gzip(self):
        path = os.path.join(self.dir, "log.txt.gz")
        with open(path, "wb") as f:
            f.write(gzip.compress(b"line\n" * 1000))
        out = packer.extract(path, "application/gzip")
        self.assertEqual(out.path, os.path.join(self.dir, "log.txt"))
        out2 = packer.extract(path, "application/gzip")
        self.assertEqual(os.path.basename(out2.path), "log (2).txt")
        with open(out2.path, "rb") as f:
            self.assertEqual(f.read(), b"line\n" * 1000)

    def test_symlinks_are_not_packed(self):
        root = self.make_tree()
        os.symlink("/etc/passwd", os.path.join(root, "リンク"))
        res = packer.create([root], "a.zip", "zip")
        with zipfile.ZipFile(res.path) as z:
            self.assertNotIn("資料/リンク", z.namelist())
        self.assertIn(os.path.join("資料", "リンク"), res.skipped)

    def test_different_folders_refused(self):
        root = self.make_tree()
        with self.assertRaises(packer.PackError):
            packer.create([os.path.join(root, "メモ.txt"), os.path.join(root, "サブ", "data.bin")], "a.zip", "zip")

    def test_cancel_extract_cleans_up(self):
        root = self.make_tree()
        arc = packer.create([root], "a.zip", "zip").path
        cancel = threading.Event()
        cancel.set()
        before = sorted(os.listdir(self.dir))
        with self.assertRaises(packer.Cancelled):
            packer.extract(arc, "application/zip", cancel=cancel)
        self.assertEqual(sorted(os.listdir(self.dir)), before)

    def test_cancel_create_cleans_up(self):
        root = self.make_tree()
        for fmt in ("zip", "tar.gz") + (("7z",) if HAS_7Z else ()):
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(packer.Cancelled):
                packer.create([root], f"a.{fmt}", fmt, cancel=cancel)
            self.assertEqual(self.leftovers(), [], fmt)
            self.assertFalse(os.path.exists(os.path.join(self.dir, f"a.{fmt}")))

    @unittest.skipUnless(HAS_7Z, "7z がありません")
    def test_zip_python_cannot_read_falls_back_to_7z(self):
        # Deflate64 は Python では読めないが 7z なら読める
        src = os.path.join(self.dir, "元")
        os.mkdir(src)
        with open(os.path.join(src, "a.txt"), "w") as f:
            f.write("deflate64 のデータ\n" * 2000)
        arc = os.path.join(self.dir, "d64.zip")
        subprocess.run([sevenzip_command(), "a", "-tzip", "-mm=Deflate64", "-bd", arc, src],
                       capture_output=True, check=True)
        out = packer.extract(arc, "application/zip")
        with open(os.path.join(out.path, "元", "a.txt")) as f:
            self.assertEqual(f.read(), "deflate64 のデータ\n" * 2000)
        self.assertIn("7z", out.note)

    def test_broken_zip(self):
        path = os.path.join(self.dir, "broken.zip")
        with open(path, "wb") as f:
            f.write(b"PK\x03\x04" + b"\0" * 20)
        before = sorted(os.listdir(self.dir))
        with self.assertRaises(packer.PackError):
            packer.extract(path, "application/zip")
        self.assertEqual(sorted(os.listdir(self.dir)), before)

    def test_progress_is_reported(self):
        root = self.make_tree()
        seen = []
        packer.create([root], "a.zip", "zip", progress=lambda f, t: seen.append(f))
        self.assertTrue(seen)
        self.assertAlmostEqual(seen[-1], 1.0)


class DamagedArchiveTest(Base):
    """一部が壊れた圧縮ファイルから、無事なものだけ取り出せるか。"""

    def make_photos(self):
        src = os.path.join(self.dir, "Camera")
        os.mkdir(src)
        self.contents = {}
        for i in range(6):
            name = f"PXL_{i:03d}.jpg"
            data = (f"<<photo-{i}>>".encode() + os.urandom(20000))
            with open(os.path.join(src, name), "wb") as f:
                f.write(data)
            self.contents[name] = data
        return src

    def corrupt(self, archive_path, name):
        """name の中身（圧縮せず入れたもの）の真ん中を壊す。"""
        with open(archive_path, "rb") as f:
            data = bytearray(f.read())
        content = self.contents[name]
        pos = data.find(content[:12])
        self.assertGreater(pos, 0, "中身の場所が見つからない")
        mid = pos + len(content) // 2
        for k in range(mid, mid + 32):
            data[k] ^= 0xFF
        with open(archive_path, "wb") as f:
            f.write(data)

    def check_salvaged(self, out, broken_name):
        root = os.path.join(out.path, "Camera")
        self.assertEqual(len(out.damaged), 1, out.damaged)
        self.assertTrue(out.damaged[0].endswith(broken_name))
        self.assertFalse(os.path.exists(os.path.join(root, broken_name)), "壊れたファイルが残っている")
        for name, data in self.contents.items():
            if name == broken_name:
                continue
            with open(os.path.join(root, name), "rb") as f:
                self.assertEqual(f.read(), data, name)

    def test_zip(self):
        src = self.make_photos()
        arc = os.path.join(self.dir, "Camera.zip")
        with zipfile.ZipFile(arc, "w", zipfile.ZIP_STORED) as z:
            for name in sorted(self.contents):
                z.write(os.path.join(src, name), f"Camera/{name}")
        shutil.rmtree(src)
        self.corrupt(arc, "PXL_003.jpg")
        out = packer.extract(arc, "application/zip")
        self.check_salvaged(out, "PXL_003.jpg")

    @unittest.skipUnless(HAS_7Z, "7z がありません")
    def test_7z(self):
        src = self.make_photos()
        arc = os.path.join(self.dir, "Camera.7z")
        subprocess.run([sevenzip_command(), "a", "-t7z", "-m0=Copy", "-ms=off", "-bd", arc, src],
                       capture_output=True, check=True)
        shutil.rmtree(src)
        self.corrupt(arc, "PXL_002.jpg")
        out = packer.extract(arc, "application/x-7z-compressed")
        self.check_salvaged(out, "PXL_002.jpg")

    @unittest.skipUnless(HAS_RAR, "rar / unrar がありません")
    def test_rar(self):
        src = self.make_photos()
        arc = os.path.join(self.dir, "Camera.rar")
        subprocess.run(["rar", "a", "-m0", "-ep1", "-idq", arc, src], check=True)
        shutil.rmtree(src)
        self.corrupt(arc, "PXL_004.jpg")
        out = packer.extract(arc, "application/x-rar")
        self.check_salvaged(out, "PXL_004.jpg")

    def test_tar_keeps_files_before_damage(self):
        src = self.make_photos()
        arc = os.path.join(self.dir, "Camera.tar.gz")
        with tarfile.open(arc, "w:gz") as t:
            for name in sorted(self.contents):
                t.add(os.path.join(src, name), f"Camera/{name}")
        shutil.rmtree(src)
        with open(arc, "rb") as f:
            data = bytearray(f.read())
        cut = len(data) * 2 // 3
        data[cut:cut + 64] = os.urandom(64)
        with open(arc, "wb") as f:
            f.write(data)
        before = sorted(os.listdir(self.dir))
        try:
            out = packer.extract(arc, "application/gzip")
        except packer.PackError:
            self.assertEqual(sorted(os.listdir(self.dir)), before)  # 何も取れなければ残さない
            return
        self.assertTrue(out.damaged)  # 壊れていることには必ず気づく
        if "特定できません" in out.damaged[0]:
            self.assertIn("確かめて", out.note)  # どれか分からない場合は、そう知らせる
            return
        root = os.path.join(out.path, "Camera")
        got = sorted(os.listdir(root))
        self.assertTrue(got)
        for name in got:  # 壊れた所より前のものは中身が正しい
            with open(os.path.join(root, name), "rb") as f:
                self.assertEqual(f.read(), self.contents[name], name)

    def test_parse_7z_damage(self):
        out = ("ERROR: Data Error : Camera/PXL_20251105_074627148.jpg\n"
               "ERROR: CRC Failed : Camera/PXL_20251105_075753085.jpg\n"
               "Sub items Errors: 2\n")
        self.assertEqual(packer._damaged_from_7z(out), [
            "Camera/PXL_20251105_074627148.jpg", "Camera/PXL_20251105_075753085.jpg"])


if __name__ == "__main__":
    unittest.main()
