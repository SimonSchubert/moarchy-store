#!/usr/bin/env bash
# What each package would cost to install, all measured against one image state.
#
#   ./scripts/sweep-cost.sh foliate kaidan tokodon
#   ./scripts/sweep-cost.sh --queue sweep/queue.txt
#
# Install cost is relative: it is the packages you do not already have. So it is
# only meaningful when every number in a comparison was taken against the same
# image, and sweep-measure.sh cannot give you that -- it installs each app to
# photograph it, and even with `pacman -Rns` afterwards the image drifts.
# Measured: klevernotes cost 38 packages on a fresh image and 25 an hour later,
# for no reason except that other apps had been installed and removed in
# between. The same app, the same day, a third smaller.
#
# So costs are taken here, in one pass, before a batch installs anything. The
# header records how many packages were installed at the time, because that is
# what the numbers are relative to and a future run on a cleaner image should
# say so rather than silently disagree.
#
# Nothing is installed and no privilege is needed: `pacman -S --print` resolves
# against the sync database and expac reads sizes out of it.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO_ROOT"

VM=${MOARCHY_VM:-$HOME/Projects/omarchy-mobile}
SSH="$VM/scripts/vm-ssh.sh"
[ -x "$SSH" ] || { echo "!! no VM at $VM -- set MOARCHY_VM" >&2; exit 1; }

if [ "${1:-}" = "--queue" ]; then
  [ -f "${2:-}" ] || { echo "!! no such queue file: ${2:-}" >&2; exit 1; }
  mapfile -t PKGS < <(grep -vE '^\s*(#|$)' "$2")
else
  PKGS=("$@")
fi
[ "${#PKGS[@]}" -gt 0 ] || { echo "usage: sweep-cost.sh <pkg>... | --queue <file>" >&2; exit 1; }

INSTALLED=$("$SSH" "pacman -Qq | wc -l" 2>/dev/null | tr -d ' ')
echo "# measured against an image with $INSTALLED packages installed, $(date +%F)"
printf '%-28s %8s %10s\n' "package" "packages" "MB"

for pkg in "${PKGS[@]}"; do
  names=$("$SSH" "pacman -S --print --print-format '%n' -- $pkg 2>/dev/null" || true)
  count=$(printf '%s\n' "$names" | grep -c . || true)
  if [ "$count" -eq 0 ]; then
    printf '%-28s %8s %10s\n' "$pkg" "-" "not in the repos"
    continue
  fi
  bytes=$("$SSH" "expac -S '%k' -- $(printf '%s' "$names" | tr '\n' ' ') 2>/dev/null" \
          | awk '{s+=$1} END{print s+0}')
  printf '%-28s %8d %10.2f\n' "$pkg" "$count" "$(python3 -c "print($bytes/1048576)")"
done
