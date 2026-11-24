"""ファイル画面（Nautilus）の右クリック →「スクリプト」→「極み窓で調べる」の登録。

Nautilus のスクリプトは人ごとのフォルダー（~/.local/share/nautilus/scripts）に置く決まりなので、
DEB パッケージでは入れられない。そのため、ソフトの中から登録・解除できるようにする。
"""

import os
import shlex
import shutil
import sys
from pathlib import Path

SCRIPT_NAME = "極み窓で調べる"


def scripts_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "nautilus" / "scripts"


def script_path():
    return scripts_dir() / SCRIPT_NAME


def _launch_command():
    """極み窓を起動するコマンド。DEB などで kiwami コマンドがあればそれを使う。"""
    found = shutil.which("kiwami")
    if found:
        return shlex.quote(found)
    # ソースから動かしているときは、このフォルダーを Python に教えて起動する
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return (f"env PYTHONPATH={shlex.quote(package_parent)}${{PYTHONPATH:+:$PYTHONPATH}} "
            f"{shlex.quote(sys.executable)} -m kiwami")


def is_installed():
    return script_path().exists()


def install():
    path = script_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{SCRIPT_NAME}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n# 極み窓が作ったスクリプト（極み窓の「その他」メニューから外せます）\n")
        f.write(f'exec {_launch_command()} "$@"\n')
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    return path


def uninstall():
    try:
        script_path().unlink()
    except FileNotFoundError:
        pass
