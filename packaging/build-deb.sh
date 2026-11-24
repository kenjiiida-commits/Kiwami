#!/bin/bash
# 極み窓の DEB パッケージを作る。使い方: bash packaging/build-deb.sh
# できあがったものは dist/kiwami_<版>_all.deb に置かれる。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -c "import re,sys; print(re.search(r'__version__ = \"([^\"]+)\"', open(sys.argv[1]).read()).group(1))" "$ROOT/kiwami/__init__.py")"
NAME=kiwami
MAINTAINER="Kenji Iida <kenjiiida-commits@users.noreply.github.com>"
BUILD="$ROOT/build/deb"
PKG="$BUILD/${NAME}_${VERSION}_all"
DIST="$ROOT/dist"

echo "極み窓 ${VERSION} の DEB を作ります"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/usr/bin" "$PKG/usr/lib/$NAME" \
         "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/scalable/apps" \
         "$PKG/usr/share/doc/$NAME" "$PKG/usr/share/man/man1" "$DIST"

# プログラム本体（テストやキャッシュは入れない）
cp -r "$ROOT/kiwami" "$PKG/usr/lib/$NAME/"
find "$PKG/usr/lib/$NAME" -name "__pycache__" -prune -exec rm -rf {} +

# 起動コマンド
cat > "$PKG/usr/bin/$NAME" <<'LAUNCHER'
#!/bin/sh
export PYTHONPATH="/usr/lib/kiwami${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m kiwami "$@"
LAUNCHER

# アプリ一覧・アイコン
cat > "$PKG/usr/share/applications/$NAME.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Kiwami
Name[ja]=極み窓
GenericName=File inspector
GenericName[ja]=ファイル判別・操作
Comment=Identify file types and rename, change attributes, compress or extract
Comment[ja]=ファイルの本当の種類を見分け、名前・属性・日時の変更や圧縮・解凍をする小窓
Exec=kiwami %F
Icon=kiwami
Terminal=false
Categories=Utility;FileTools;
Keywords=file;extension;type;zip;archive;拡張子;判別;解凍;圧縮;
StartupWMClass=kiwami
DESKTOP
cp "$ROOT/kiwami/data/kiwami.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/$NAME.svg"

# 説明書（man）・著作権・変更履歴
gzip -9n -c "$ROOT/packaging/kiwami.1" > "$PKG/usr/share/man/man1/$NAME.1.gz"
cp "$ROOT/README.md" "$PKG/usr/share/doc/$NAME/README.md"
cat > "$PKG/usr/share/doc/$NAME/copyright" <<COPYRIGHT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Kiwami
Upstream-Contact: $MAINTAINER
Source: https://github.com/kenjiiida-commits/Kiwami

Files: *
Copyright: 2026 Kenji Iida (飯田顕治)
License: Expat

License: Expat
$(sed -e 's/^$/./' -e 's/^/ /' "$ROOT/LICENSE" | tail -n +3)
COPYRIGHT
DATE="$(date -R)"
gzip -9n -c > "$PKG/usr/share/doc/$NAME/changelog.gz" <<CHANGELOG
$NAME ($VERSION) unstable; urgency=medium

  * Release $VERSION. See https://github.com/kenjiiida-commits/Kiwami for details.

 -- $MAINTAINER  $DATE
CHANGELOG

# インストール・削除のときの処理（Python の読み込みを速くするための下準備と、その片付け）
cat > "$PKG/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    python3 -m compileall -q /usr/lib/kiwami >/dev/null 2>&1 || true
fi
POSTINST
cat > "$PKG/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e
find /usr/lib/kiwami -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
PRERM

# 権限をそろえる（フォルダー 755・ファイル 644・実行するもの 755）
find "$PKG" -type d -exec chmod 755 {} +
find "$PKG" -type f -exec chmod 644 {} +
chmod 755 "$PKG/usr/bin/$NAME" "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

SIZE="$(du -sk --exclude=DEBIAN "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: $NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.12), python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, libmagic1t64 | libmagic1
Recommends: python3-pyside6.qtnetwork, 7zip, qt6-qpa-plugins
Suggests: unrar, qt6-image-formats-plugins
Maintainer: $MAINTAINER
Homepage: https://github.com/kenjiiida-commits/Kiwami
Description: small window that identifies file types and fixes extensions
 Kiwami (極み窓) is a small, always-on-top window for Ubuntu. Drop files on it
 to see what they really contain, judged from their content rather than their
 name, and whether the extension matches. Wrong extensions can be fixed with
 one click, one by one or in bulk.
 .
 It can also rename files, change permissions and timestamps (with undo),
 show text with Japanese encoding detection, images, hex dumps and archive
 listings, and create or extract zip, 7z, tar.gz and tar.xz archives,
 including password-protected ones. Damaged archives are extracted as far
 as possible.
CONTROL

( cd "$PKG" && find usr -type f -exec md5sum {} + | sort -k2 > DEBIAN/md5sums )
chmod 644 "$PKG/DEBIAN/md5sums" "$PKG/DEBIAN/control"

OUT="$DIST/${NAME}_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT" >/dev/null
echo "できました: $OUT"
