#!/usr/bin/env bash
# render.sh PAGE[?query] OUT.png [WIDTH HEIGHT]: one screenshot at 1x with headless
# Chromium; 1440x900 unless a size is given (390 844 for the phone pages).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=$(realpath -m "$2")
profile=$(mktemp -d)
trap 'rm -rf "$profile"' EXIT
"${CHROMIUM:-chromium-browser}" --headless=new --disable-gpu --hide-scrollbars --no-first-run \
  --user-data-dir="$profile" --window-size="${3:-1440},${4:-900}" --force-device-scale-factor=1 \
  --virtual-time-budget=25000 --screenshot="$out" "file://$here/$1" 2>/dev/null
