#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/fixme_project"
DEST_DIR="${HOME:?HOME is not set}/murmur-fixme-demo"

if [[ ! -d "${SOURCE_DIR}" ]]; then
    printf 'Demo template not found: %s\n' "${SOURCE_DIR}" >&2
    exit 1
fi

# Safety: only ever delete a folder this script made. It has a .murmur-demo marker
# (or, for copies made before the marker existed, this script's own first commit).
is_our_demo() {
    [[ -d "${DEST_DIR}" && ! -L "${DEST_DIR}" ]] || return 1
    [[ -f "${DEST_DIR}/.murmur-demo" ]] && return 0
    [[ -f "${DEST_DIR}/shop_demo_check.py" ]] || return 1
    local first
    first="$(git -C "${DEST_DIR}" log --reverse --format=%s 2>/dev/null | head -n 1)"
    [[ "${first}" == "Create Fix it demo project" ]]
}

if [[ -e "${DEST_DIR}" || -L "${DEST_DIR}" ]]; then
    if ! is_our_demo; then
        printf 'Refusing to delete %s: it was not made by Murmur (no .murmur-demo marker).\n' "${DEST_DIR}" >&2
        printf 'Move or rename that folder yourself, then run this again.\n' >&2
        exit 2
    fi
    # Our own disposable demo folder: remove the old copy so each run starts clean.
    rm -rf "${DEST_DIR}"
fi

mkdir -p "${DEST_DIR}"
cp -R "${SOURCE_DIR}/." "${DEST_DIR}/"
find "${DEST_DIR}" -name __pycache__ -type d -prune -exec rm -rf {} +  # leftovers from earlier runs

git -C "${DEST_DIR}" init --quiet
git -C "${DEST_DIR}" add -- .
git -C "${DEST_DIR}" \
    -c user.name="Murmur demo" \
    -c user.email="murmur-demo@example.invalid" \
    commit --quiet -m "Create Fix it demo project"

printf 'Created the Fix it demo at %s\n' "${DEST_DIR}"
printf 'Run its failing test with: uv run --project "%s/.." pytest -q "%s/shop_demo_check.py"\n' \
    "${SCRIPT_DIR}" "${DEST_DIR}"
