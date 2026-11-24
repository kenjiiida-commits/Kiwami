import os
import tempfile
import unittest

from kiwami.fileops import change_extension, rename_no_replace


class ChangeExtensionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, name, data=b"x"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_change(self):
        path = self._touch("写真.jpg", b"png-data")
        new = change_extension(path, "png")
        self.assertEqual(os.path.basename(new), "写真.png")
        self.assertFalse(os.path.exists(path))
        with open(new, "rb") as f:
            self.assertEqual(f.read(), b"png-data")

    def test_add_and_remove(self):
        path = self._touch("data")
        new = change_extension(path, "zip")
        self.assertEqual(os.path.basename(new), "data.zip")
        back = change_extension(new, "")
        self.assertEqual(os.path.basename(back), "data")

    def test_does_not_overwrite(self):
        path = self._touch("a.jpg", b"one")
        other = self._touch("a.png", b"two")
        with self.assertRaises(FileExistsError):
            change_extension(path, "png")
        with open(other, "rb") as f:
            self.assertEqual(f.read(), b"two")
        self.assertTrue(os.path.exists(path))

    def test_does_not_overwrite_directory(self):
        path = self._touch("a.jpg")
        os.mkdir(os.path.join(self.dir, "a.png"))
        with self.assertRaises(FileExistsError):
            change_extension(path, "png")

    def test_does_not_replace_symlink(self):
        path = self._touch("a.jpg")
        os.symlink("/nonexistent", os.path.join(self.dir, "a.png"))
        with self.assertRaises(FileExistsError):
            rename_no_replace(path, os.path.join(self.dir, "a.png"))


if __name__ == "__main__":
    unittest.main()
