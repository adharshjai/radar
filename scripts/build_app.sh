#!/bin/sh
# Build Radar.app: bundled Python engine (PyInstaller) + SwiftUI app (swift build) -> dist/Radar.app
set -e
set -o pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VERSION="$(python3 -c 'import radar; print(radar.__version__)')"
BUILD="$(date +%Y%m%d%H%M)"
SKIP_ENGINE="${SKIP_ENGINE:-0}"

echo "== engine (PyInstaller)"
if [ "$SKIP_ENGINE" != "1" ] || [ ! -x engine-build/dist/radar-engine/radar-engine ]; then
  python3 -m PyInstaller --noconfirm --clean scripts/radar-engine.spec --distpath engine-build/dist --workpath engine-build/work >engine-build.log 2>&1 \
    || { tail -40 engine-build.log; exit 1; }
fi
engine-build/dist/radar-engine/radar-engine --help >/dev/null

echo "== app (swift build)"
BIN="RadarApp/.build/release/Radar"
rm -f "$BIN"
( cd RadarApp && swift build -c release 2>&1 | tail -5 )
[ -x "$BIN" ] || { echo "swift build did not produce $BIN"; exit 1; }

echo "== bundle"
APP="dist/Radar.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/engine"
cp "$BIN" "$APP/Contents/MacOS/Radar"
sed -e "s/__VERSION__/$VERSION/" -e "s/__BUILD__/$BUILD/" scripts/Info.plist > "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
python3 scripts/make_icon.py "$APP/Contents/Resources/AppIcon.icns" >/dev/null
cp -R engine-build/dist/radar-engine/. "$APP/Contents/Resources/engine/"
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "(codesign skipped)"
du -sh "$APP" | awk '{print "built " $2 " (" $1 ")"}'
echo "run:  open $APP      demo data:  RADAR_DEMO=1 open $APP"
