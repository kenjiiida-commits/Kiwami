#!/bin/bash
# 極み窓を、アプリ一覧とファイル画面の右クリック（スクリプト）に登録する。
# 管理者権限は使わず、自分のホームフォルダーの中だけに入れる。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
SCRIPTS="$HOME/.local/share/nautilus/scripts"

mkdir -p "$BIN" "$APPS" "$ICONS" "$SCRIPTS"

# 起動用のコマンド（どこからでも kiwami で起動できる）
cat > "$BIN/kiwami" <<LAUNCHER
#!/bin/sh
export PYTHONPATH="$DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m kiwami "\$@"
LAUNCHER
chmod 755 "$BIN/kiwami"

# アイコン
cp "$DIR/kiwami/data/kiwami.svg" "$ICONS/kiwami.svg"

# アプリ一覧
cat > "$APPS/kiwami.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=極み窓
Name[en]=Kiwami
Comment=ファイルの本当の種類を見分け、名前・属性・日時の変更や圧縮・解凍をする小窓
Exec="$BIN/kiwami" %F
Icon=kiwami
Terminal=false
Categories=Utility;FileTools;
Keywords=file;extension;zip;拡張子;判別;解凍;圧縮;
StartupWMClass=kiwami
DESKTOP

# ファイル画面（Nautilus）の右クリック → スクリプト → 極み窓で調べる
cat > "$SCRIPTS/極み窓で調べる" <<SCRIPT
#!/bin/sh
exec "$BIN/kiwami" "\$@"
SCRIPT
chmod 755 "$SCRIPTS/極み窓で調べる"

update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "極み窓を登録しました。"
echo "・アプリ一覧に「極み窓」が出ます（出ないときは一度ログアウトしてください）"
echo "・ファイル画面でファイルを右クリック →「スクリプト」→「極み窓で調べる」"
echo "・端末からは kiwami で起動できます（~/.local/bin が PATH にある場合）"
echo "このフォルダー（$DIR）を移動したら、もう一度 bash install.sh を実行してください。"
