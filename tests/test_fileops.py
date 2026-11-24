import os
import stat
import tempfile
import unittest

from kiwami.fileops import (
    GroupOp,
    History,
    bulk_change_extensions,
    bulk_set_mode,
    bulk_set_times,
    UndoConflict,
    UndoError,
    change_extension,
    check_name,
    format_mode,
    rename,
    rename_no_replace,
    set_mode,
    set_times,
)

NS = 1_000_000_000


class Base(unittest.TestCase):
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

    def _read(self, path):
        with open(path, "rb") as f:
            return f.read()


class RenameTest(Base):
    def test_change_extension(self):
        path = self._touch("写真.jpg", b"png-data")
        new, op = change_extension(path, "png")
        self.assertEqual(os.path.basename(new), "写真.png")
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self._read(new), b"png-data")
        self.assertIsNotNone(op)

    def test_add_and_remove_extension(self):
        path = self._touch("data")
        new, _ = change_extension(path, "zip")
        self.assertEqual(os.path.basename(new), "data.zip")
        back, _ = change_extension(new, "")
        self.assertEqual(os.path.basename(back), "data")

    def test_same_name_is_noop(self):
        path = self._touch("a.txt")
        self.assertEqual(rename(path, "a.txt"), (path, None))

    def test_does_not_overwrite(self):
        path = self._touch("a.jpg", b"one")
        other = self._touch("a.png", b"two")
        with self.assertRaises(FileExistsError):
            change_extension(path, "png")
        self.assertEqual(self._read(other), b"two")
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

    def test_bad_names(self):
        for bad in ("", "  ", ".", "..", "a/b", "a\0b", "あ" * 100):
            self.assertNotEqual(check_name(bad), "", repr(bad))
        self.assertEqual(check_name("ふつうの名前.txt"), "")
        path = self._touch("a.txt")
        with self.assertRaises(ValueError):
            rename(path, "../escape.txt")


class ModeAndTimeTest(Base):
    def test_format_mode(self):
        self.assertEqual(format_mode(0o754), "rwxr-xr--（754）")

    def test_set_mode_keeps_special_bits(self):
        path = self._touch("a.sh")
        os.chmod(path, 0o2644)
        op = set_mode(path, 0o755)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o2755)
        self.assertEqual(op.old_mode, 0o2644)

    def test_set_mode_unchanged(self):
        path = self._touch("a")
        os.chmod(path, 0o644)
        self.assertIsNone(set_mode(path, 0o644))

    def test_set_times(self):
        path = self._touch("a")
        op = set_times(path, 1_600_000_000 * NS, 1_500_000_000 * NS + 123)
        st = os.stat(path)
        self.assertEqual(st.st_mtime_ns, 1_500_000_000 * NS + 123)
        self.assertEqual(st.st_atime_ns, 1_600_000_000 * NS)
        self.assertIsNotNone(op)

    def test_symlink_refused(self):
        target = self._touch("t")
        link = os.path.join(self.dir, "link")
        os.symlink(target, link)
        with self.assertRaises(ValueError):
            set_mode(link, 0o600)
        with self.assertRaises(ValueError):
            set_times(link, 0, 0)


class HistoryTest(Base):
    def test_undo_rename(self):
        h = History()
        path = self._touch("a.jpg", b"data")
        new, op = change_extension(path, "png")
        h.push(op)
        _, back = h.undo()
        self.assertEqual(back, path)
        self.assertEqual(self._read(path), b"data")
        self.assertFalse(os.path.exists(new))
        self.assertEqual(len(h), 0)

    def test_undo_rename_refuses_when_old_name_taken(self):
        h = History()
        path = self._touch("a.jpg", b"data")
        _, op = change_extension(path, "png")
        h.push(op)
        self._touch("a.jpg", b"newcomer")
        with self.assertRaises(UndoError):
            h.undo()
        self.assertEqual(self._read(path), b"newcomer")

    def test_undo_rename_refuses_replaced_file(self):
        h = History()
        path = self._touch("a.jpg", b"data")
        new, op = change_extension(path, "png")
        h.push(op)
        os.remove(new)
        self._touch("a.png", b"other file")  # 同じ名前の別のファイル
        # 実体の番号（inode）が使い回されることもあるため、中身の違いでも止める
        with self.assertRaises((UndoError, UndoConflict)):
            h.undo()
        self.assertFalse(os.path.exists(path))

    def test_undo_rename_conflict_when_edited(self):
        h = History()
        path = self._touch("a.jpg", b"data")
        new, op = change_extension(path, "png")
        h.push(op)
        with open(new, "ab") as f:
            f.write(b"more")
        with self.assertRaises(UndoConflict):
            h.undo()
        h.undo(force=True)
        self.assertEqual(self._read(path), b"datamore")

    def test_undo_mode_and_conflict(self):
        h = History()
        path = self._touch("a")
        os.chmod(path, 0o644)
        h.push(set_mode(path, 0o600))
        os.chmod(path, 0o640)  # 別のソフトがさらに変えた
        with self.assertRaises(UndoConflict):
            h.undo()
        self.assertEqual(len(h), 1)
        h.undo(force=True)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)

    def test_undo_times(self):
        h = History()
        path = self._touch("a")
        before = os.stat(path)
        h.push(set_times(path, 1_000 * NS, 2_000 * NS))
        h.undo()
        after = os.stat(path)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(after.st_atime_ns, before.st_atime_ns)

    def test_undo_times_conflict_when_modified(self):
        h = History()
        path = self._touch("a")
        h.push(set_times(path, 1_000 * NS, 2_000 * NS))
        with open(path, "ab") as f:
            f.write(b"more")
        with self.assertRaises(UndoConflict):
            h.undo()

    def test_folder_rename_relocates_earlier_ops(self):
        h = History()
        folder = os.path.join(self.dir, "旧フォルダー")
        os.mkdir(folder)
        inner = os.path.join(folder, "a")
        with open(inner, "wb") as f:
            f.write(b"x")
        os.chmod(inner, 0o644)
        h.push(set_mode(inner, 0o600))
        new_folder, op = rename(folder, "新フォルダー")
        h.push(op)
        # フォルダー名を変えたままでも、中のファイルの属性を戻せる
        first = h._ops[0]
        self.assertTrue(first.path.startswith(new_folder))
        h.undo()  # フォルダー名を戻す
        h.undo()  # 属性を戻す
        self.assertEqual(stat.S_IMODE(os.stat(inner).st_mode), 0o644)


class GroupTest(Base):
    def test_bulk_extensions_and_undo(self):
        h = History()
        a = self._touch("a.jpg", b"1")
        b = self._touch("b", b"2")
        c = self._touch("c.jpg", b"3")
        self._touch("c.png", b"taken")  # c は名前がぶつかる
        group, moved, failed = bulk_change_extensions([(a, "png"), (b, "zip"), (c, "png")])
        self.assertEqual(sorted(os.path.basename(p) for p in moved.values()), ["a.png", "b.zip"])
        self.assertEqual([os.path.basename(p) for p, _ in failed], ["c.jpg"])
        h.push(group)
        op, done = h.undo()
        self.assertIs(op, group)
        self.assertEqual(len(done), 2)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))
        self.assertEqual(self._read(os.path.join(self.dir, "c.png")), b"taken")

    def test_group_conflict_changes_nothing(self):
        h = History()
        paths = [self._touch(n) for n in ("x", "y")]
        for p in paths:
            os.chmod(p, 0o644)
        group, failed = bulk_set_mode(paths, 0o600)
        self.assertEqual(failed, [])
        h.push(group)
        os.chmod(paths[1], 0o640)  # 片方だけ別のソフトが変えた
        with self.assertRaises(UndoConflict):
            h.undo()
        # 何も戻していない（中途半端にしない）
        self.assertEqual(stat.S_IMODE(os.stat(paths[0]).st_mode), 0o600)
        h.undo(force=True)
        self.assertEqual([stat.S_IMODE(os.stat(p).st_mode) for p in paths], [0o644, 0o644])

    def test_group_partial_failure_is_reported(self):
        h = History()
        paths = [self._touch(n) for n in ("p", "q")]
        group, _ = bulk_set_mode(paths, 0o600)
        h.push(group)
        os.remove(paths[0])
        _, done = h.undo()
        self.assertEqual(len(done), 1)
        self.assertEqual(len(group.errors), 1)

    def test_bulk_times_keeps_untouched_value(self):
        p1, p2 = self._touch("1"), self._touch("2")
        os.utime(p1, ns=(5 * NS, 10 * NS))
        os.utime(p2, ns=(6 * NS, 20 * NS))
        group, failed = bulk_set_times([p1, p2], mtime_ns=99 * NS)
        self.assertEqual([os.stat(p).st_mtime_ns for p in (p1, p2)], [99 * NS, 99 * NS])
        self.assertEqual([os.stat(p).st_atime_ns for p in (p1, p2)], [5 * NS, 6 * NS])
        group.undo()
        self.assertEqual([os.stat(p).st_mtime_ns for p in (p1, p2)], [10 * NS, 20 * NS])

    def test_folder_rename_inside_group_relocates(self):
        h = History()
        folder = os.path.join(self.dir, "旧")
        os.mkdir(folder)
        inner = os.path.join(folder, "a.jpg")
        with open(inner, "wb") as f:
            f.write(b"x")
        group, moved, _ = bulk_change_extensions([(inner, "png")])
        h.push(group)
        _, op = rename(folder, "新")
        h.push(op)
        h.undo()
        h.undo()
        self.assertTrue(os.path.exists(inner))

    def test_empty_group_is_not_recorded(self):
        h = History()
        h.push(GroupOp("なにもしない", []))
        self.assertEqual(len(h), 0)


if __name__ == "__main__":
    unittest.main()
