import json
import os
import stat
import tempfile
import unittest

from kiwami.recent import LIMIT, RecentFiles


class RecentFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "sub", "history.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_and_reload(self):
        r = RecentFiles(self.file)
        r.add(["/a/1.txt", "/a/2.txt"])
        r.add(["/a/1.txt"])  # 同じものは先頭に移るだけ
        again = RecentFiles(self.file)
        self.assertEqual([e["path"] for e in again.entries], ["/a/1.txt", "/a/2.txt"])
        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)

    def test_limit(self):
        r = RecentFiles(self.file)
        r.add([f"/x/{i}" for i in range(LIMIT + 50)])
        self.assertEqual(len(RecentFiles(self.file).entries), LIMIT)

    def test_rename_follows_folder(self):
        r = RecentFiles(self.file)
        r.add(["/d/旧/a.txt", "/d/旧", "/d/other"])
        r.rename("/d/旧", "/d/新")
        self.assertEqual(sorted(e["path"] for e in r.entries), ["/d/other", "/d/新", "/d/新/a.txt"])

    def test_remove_and_clear(self):
        r = RecentFiles(self.file)
        r.add(["/a", "/b"])
        r.remove(["/a"])
        self.assertEqual([e["path"] for e in RecentFiles(self.file).entries], ["/b"])
        r.clear()
        self.assertFalse(os.path.exists(self.file))
        self.assertEqual(RecentFiles(self.file).entries, [])

    def test_broken_file_is_ignored(self):
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w") as f:
            f.write("{ こわれている")
        r = RecentFiles(self.file)
        self.assertEqual(r.entries, [])
        r.add(["/ok"])
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["entries"][0]["path"], "/ok")


if __name__ == "__main__":
    unittest.main()
