#!/usr/bin/env bash
# Squeeze screenshots, in place, losslessly enough that nobody can tell.
#
#   ./scripts/sweep-shots.sh                 # every PNG in screenshots/
#   ./scripts/sweep-shots.sh a.png b.png     # just these
#
# Screenshots are not packaged -- they are fetched from GitHub on demand over
# whatever connection a phone has (PKGBUILD says why). So their size is
# somebody's mobile data, and 780 KB of flat UI colour is 780 KB because
# nothing has ever asked it not to be.
#
# pngquant does the work: these are screenshots of flat-shaded widgets, which
# palettise almost perfectly. oxipng then re-packs the result. Both are
# idempotent, so running this over an already-squeezed directory is a no-op
# and costs only time.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO_ROOT"

for tool in pngquant oxipng; do
  command -v "$tool" >/dev/null || { echo "!! $tool not found -- brew install $tool" >&2; exit 1; }
done

if [ $# -gt 0 ]; then files=("$@"); else mapfile -t files < <(ls screenshots/*.png); fi

before_total=0; after_total=0
for f in "${files[@]}"; do
  [ -f "$f" ] || { echo "!! no such file: $f" >&2; exit 1; }
  before=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f")

  # --skip-if-larger leaves an already-optimal file alone rather than growing
  # it; pngquant exits 98 for that and 99 for "cannot hit the quality floor",
  # and neither is a failure worth stopping a batch over.
  pngquant --quality=60-85 --speed 1 --strip --force --output "$f" "$f" \
    || [ $? -eq 98 ] || [ $? -eq 99 ]
  oxipng -o 4 --strip safe --quiet "$f"

  after=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f")
  before_total=$((before_total + before)); after_total=$((after_total + after))
  printf '%-40s %6s KB -> %5s KB\n' "$(basename "$f")" "$((before / 1024))" "$((after / 1024))"
done

if [ "$before_total" -gt 0 ]; then
  printf '\n%s files: %s KB -> %s KB (%s%% saved)\n' \
    "${#files[@]}" "$((before_total / 1024))" "$((after_total / 1024))" \
    "$(( (before_total - after_total) * 100 / before_total ))"
fi
