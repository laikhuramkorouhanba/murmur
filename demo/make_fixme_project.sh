#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/fixme_project"
DEST_DIR="${HOME:?HOME is not set}/murmur-fixme-demo"

if [[ ! -d "${SOURCE_DIR}" ]]; then
    printf 'Demo template not found: %s\n' "${SOURCE_DIR}" >&2
    exit 1
fi

# This is a disposable demo folder. Remove the old copy so each run starts clean.
if [[ -e "${DEST_DIR}" || -L "${DEST_DIR}" ]]; then
    rm -rf "${DEST_DIR}"
fi

mkdir -p "${DEST_DIR}"
cp -R "${SOURCE_DIR}/." "${DEST_DIR}/"

git -C "${DEST_DIR}" init --quiet
git -C "${DEST_DIR}" add -- .
git -C "${DEST_DIR}" \
    -c user.name="Murmur demo" \
    -c user.email="murmur-demo@example.invalid" \
    commit --quiet -m "Create Fix it demo project"

printf 'Created the Fix it demo at %s\n' "${DEST_DIR}"
printf 'Run its failing test with: uv run --project "%s/.." pytest -q "%s/shop_demo_check.py"\n' \
    "${SCRIPT_DIR}" "${DEST_DIR}"
