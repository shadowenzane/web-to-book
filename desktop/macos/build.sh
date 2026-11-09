#!/bin/bash
# Build 网页成书.app (arm64). Requires Xcode Command Line Tools.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$ROOT/dist/网页成书.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/converter"

MODCACHE="$(mktemp -d)"
swiftc -swift-version 5 -O -target arm64-apple-macos13.0 \
  -module-cache-path "$MODCACHE" \
  -framework AppKit -framework WebKit -framework PDFKit \
  -o "$APP/Contents/MacOS/webtobook" "$ROOT/desktop/macos/main.swift"

cp "$ROOT/desktop/macos/Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/core/converter.py" "$ROOT/core/dom.py" "$APP/Contents/Resources/converter/"
[ -f "$ROOT/desktop/macos/AppIcon.icns" ] && cp "$ROOT/desktop/macos/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
codesign --force --deep --sign - "$APP"
echo "built: $APP"
