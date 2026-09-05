#!/usr/bin/env bash
# Sign catalogue.toml so clients will trust it as an install allowlist.
#
#   ./scripts/sign-catalogue.sh
#   git add catalogue.toml catalogue.toml.sig && git commit && git push
#
# After pushing, every installed copy picks up the new catalogue on next launch.
# No package update, no AUR push -- that is the whole point of signing it.
#
# The private key lives at ~/.config/moarchy-store/signing-key.asc, deliberately
# outside the repo. Whoever holds it can add apps to the allowlist on every
# installation, so treat it like a release key: back it up, never commit it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

KEY="${MOARCHY_SIGNING_KEY:-$HOME/.config/moarchy-store/signing-key.asc}"
[[ -f $KEY ]] || { echo "No signing key at $KEY" >&2; exit 1; }

# `serial` must increase with every publish. Clients refuse a remote catalogue
# whose serial is lower than the one they already trust, so someone who can
# replay traffic cannot roll you back to an older signed catalogue that still
# lists something you have since removed.
serial=$(awk -F' *= *' '/^serial *=/{print $2; exit}' catalogue.toml)
[[ -n $serial ]] || { echo "catalogue.toml has no 'serial' field" >&2; exit 1; }
echo "==> signing catalogue serial $serial"

GNUPGHOME=$(mktemp -d)
chmod 700 "$GNUPGHOME"
export GNUPGHOME
trap 'rm -rf "$GNUPGHOME"' EXIT

gpg --batch --quiet --import "$KEY"
fpr=$(gpg --list-secret-keys --with-colons | awk -F: '/^fpr:/{print $10; exit}')

rm -f catalogue.toml.sig
gpg --batch --yes --detach-sign --local-user "$fpr" \
    --output catalogue.toml.sig catalogue.toml

# Verify with gpgv against the shipped public keyring -- exactly what the client
# does. Signing with a key clients do not trust would otherwise go unnoticed
# until someone's install quietly stopped seeing new apps.
if gpgv --keyring "$REPO_ROOT/data/catalogue-signing-key.gpg" \
        catalogue.toml.sig catalogue.toml 2>/dev/null; then
  echo "    verifies against the shipped public key"
else
  echo "!! does NOT verify against data/catalogue-signing-key.gpg" >&2
  exit 1
fi
