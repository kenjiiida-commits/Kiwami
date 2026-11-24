"""最近扱ったファイルの履歴（~/.local/share/kiwami/history.json）。"""

import json
import os
import tempfile
import time
from pathlib import Path

LIMIT = 300


def history_path():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "kiwami" / "history.json"


class RecentFiles:
    def __init__(self, path=None):
        self.path = Path(path) if path else history_path()
        self.entries = []  # [{"path": ..., "time": 秒}, ...] 新しいものが先頭
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.entries = [
                {"path": str(e["path"]), "time": float(e["time"])}
                for e in data.get("entries", [])
                if isinstance(e, dict) and "path" in e and "time" in e
            ][:LIMIT]
        except (OSError, ValueError, TypeError, AttributeError):
            self.entries = []  # 無い・壊れているときは空から始める

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 書きかけで壊れないよう、別のファイルに書いてから置き換える
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".history-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"entries": self.entries}, f, ensure_ascii=False, indent=1)
            os.chmod(tmp, 0o600)  # 自分だけが読める
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def add(self, paths):
        now = time.time()
        new = [os.path.abspath(p) for p in paths]
        keep = [e for e in self.entries if e["path"] not in set(new)]
        self.entries = ([{"path": p, "time": now} for p in reversed(new)] + keep)[:LIMIT]
        try:
            self._save()
        except OSError:
            pass  # 履歴が保存できなくても本来の作業は続ける

    def rename(self, old, new):
        changed = False
        for e in self.entries:
            if e["path"] == old:
                e["path"] = new
                changed = True
            elif e["path"].startswith(old.rstrip("/") + "/"):
                e["path"] = new + e["path"][len(old.rstrip("/")):]
                changed = True
        if changed:
            try:
                self._save()
            except OSError:
                pass

    def remove(self, paths):
        drop = set(paths)
        self.entries = [e for e in self.entries if e["path"] not in drop]
        try:
            self._save()
        except OSError:
            pass

    def clear(self):
        self.entries = []
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            self._save()
