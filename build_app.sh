#!/usr/bin/env bash
# Build Murmur.app into dist/. Run: ./build_app.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
"$ROOT/assets/generate_icon.sh"
exec uv run --group build pyinstaller --noconfirm --clean --log-level WARN "$ROOT/packaging/Murmur.spec"
