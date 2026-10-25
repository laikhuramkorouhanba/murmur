#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

# Quick Look rasterizes the SVG. sips makes each size macOS expects.
qlmanage -t -s 1024 -o "$WORK_DIR" "$ROOT/assets/murmur.svg" >/dev/null
MASTER="$WORK_DIR/murmur.svg.png"
if [[ ! -f "$MASTER" ]]; then
  echo "Could not rasterize assets/murmur.svg" >&2
  exit 1
fi

ICONSET="$WORK_DIR/Murmur.iconset"
mkdir -p "$ICONSET"

make_icon() {
  local size="$1"
  local name="$2"
  sips -z "$size" "$size" "$MASTER" --out "$ICONSET/$name" >/dev/null
}

make_icon 16 icon_16x16.png
make_icon 32 icon_16x16@2x.png
make_icon 32 icon_32x32.png
make_icon 64 icon_32x32@2x.png
make_icon 128 icon_128x128.png
make_icon 256 icon_128x128@2x.png
make_icon 256 icon_256x256.png
make_icon 512 icon_256x256@2x.png
make_icon 512 icon_512x512.png
make_icon 1024 icon_512x512@2x.png

iconutil -c icns "$ICONSET" -o "$ROOT/assets/Murmur.icns"
echo "Built assets/Murmur.icns from assets/murmur.svg"
