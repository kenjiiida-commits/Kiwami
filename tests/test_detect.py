import os
import tempfile
import unittest
import zipfile

from kiwami.detect import current_extension, inspect, parse_extensions

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class CurrentExtensionTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(current_extension("a.JPG"), "jpg")

    def test_none(self):
        self.assertEqual(current_extension("README"), "")

    def test_hidden_file(self):
        self.assertEqual(current_extension(".bashrc"), "")

    def test_double(self):
        self.assertEqual(current_extension("backup.tar.gz"), "tar.gz")


class ParseExtensionsTest(unittest.TestCase):
    def test_multi(self):
        self.assertEqual(parse_extensions("jpeg/jpg/jpe/jfif"), ["jpeg", "jpg", "jpe", "jfif"])

    def test_unknown(self):
        self.assertEqual(parse_extensions("???"), [])


class InspectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_png_with_wrong_extension(self):
        info = inspect(self._write("写真.jpg", PNG_1PX))
        self.assertEqual(info.kind, "file")
        self.assertEqual(info.mime, "image/png")
        self.assertIn("png", info.candidate_exts)
        self.assertEqual(info.current_ext, "jpg")
        self.assertEqual(info.name, "写真.jpg")

    def test_zip(self):
        path = os.path.join(self.dir, "書類")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("a.txt", "hello")
        info = inspect(path)
        self.assertEqual(info.mime, "application/zip")
        self.assertEqual(info.current_ext, "")

    def test_shift_jis_text(self):
        info = inspect(self._write("memo.txt", "日本語の文章です。\r\n".encode("cp932") * 5))
        self.assertTrue(info.mime.startswith("text/"))

    def test_empty(self):
        info = inspect(self._write("empty.dat", b""))
        self.assertEqual(info.size, 0)
        self.assertEqual(info.error, "")

    def test_directory(self):
        info = inspect(self.dir)
        self.assertEqual(info.kind, "dir")

    def test_symlink(self):
        target = self._write("t.txt", b"x")
        link = os.path.join(self.dir, "link")
        os.symlink(target, link)
        self.assertEqual(inspect(link).kind, "symlink")

    def test_missing(self):
        self.assertEqual(inspect(os.path.join(self.dir, "nothing")).kind, "missing")


if __name__ == "__main__":
    unittest.main()
