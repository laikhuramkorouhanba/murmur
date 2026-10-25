#!/usr/bin/env bash
# Build Murmur.app into dist/. Run: ./build_app.sh
exec uv run --group build pyinstaller --noconfirm --clean --log-level WARN "$(dirname "$0")/packaging/Murmur.spec"
