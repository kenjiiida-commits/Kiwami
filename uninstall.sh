#!/bin/bash
# install.sh で登録したものを外す（極み窓のフォルダーそのものや設定は消さない）。
set -euo pipefail
rm -f "$HOME/.local/bin/kiwami" \
      "$HOME/.local/share/applications/kiwami.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/kiwami.svg" \
      "$HOME/.local/share/nautilus/scripts/極み窓で調べる"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "極み窓の登録を外しました。"
echo "設定（~/.config/kenjiiida/kiwami.conf）・自分用辞書（~/.config/kiwami）・履歴（~/.local/share/kiwami）は残しています。"
