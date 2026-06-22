#!/usr/bin/env bash
# Build the FlowMind Agent standalone executable on macOS (requires Python 3.9+).
# Cannot cross-compile — run this ON macOS to produce the macOS build.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
echo ">> Python: $($PY --version)"

# Don't bundle a local agent/.env — it would override the user's config.
rm -f agent/.env

$PY -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm packaging/flowmind-agent.spec

OUT="dist/flowmind-agent-macos"
rm -rf "$OUT"; mkdir -p "$OUT"
cp dist/flowmind-agent "$OUT/flowmind-agent"
chmod +x "$OUT/flowmind-agent"
cp -R browser_bridge/extension "$OUT/extension"
cp packaging/USAGE.txt "$OUT/USAGE.txt"
echo ">> Done: $OUT/  (executable + browser extension)"
