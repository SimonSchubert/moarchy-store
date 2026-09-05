"""Fetch and verify the remotely published catalogue.

The catalogue is also the install allowlist, so it cannot simply be downloaded
and trusted -- anyone able to tamper with it in transit could make the
privileged helper install any package in the official repos, and install hooks
run as root. So it is signed, and both halves of the app verify independently:

    the UI      verifies before showing you entries
    the helper  verifies again, as root, before acting on one

The helper never trusts the UI's word for it. Verification uses `gpgv` against
a keyring shipped in the package, so the trust root is whatever was installed,
not anything fetched.
"""

from __future__ import annotations

import subprocess
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/SimonSchubert/moarchy-store/main"
KEYRING = Path("/usr/share/moarchy-store/catalogue-signing-key.gpg")
CACHE = Path.home() / ".cache" / "moarchy-store"

CATALOGUE = CACHE / "catalogue.toml"
SIGNATURE = CACHE / "catalogue.toml.sig"

MAX_BYTES = 1024 * 1024
TIMEOUT = 15


def _download(name: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(
            f"{BASE_URL}/{name}", headers={"User-Agent": "moarchy-store"}
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            data = response.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def verify(catalogue: Path = CATALOGUE, signature: Path = SIGNATURE) -> bool:
    """True only if `signature` is a good signature over `catalogue` by a key in
    the shipped keyring. Any doubt -- missing gpgv, missing keyring, bad
    signature -- is False."""
    if not (catalogue.is_file() and signature.is_file() and KEYRING.is_file()):
        return False
    try:
        result = subprocess.run(
            ["gpgv", "--keyring", str(KEYRING), str(signature), str(catalogue)],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _serial(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return int(tomllib.load(fh).get("serial", 0))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return 0


def update(shipped: Path) -> bool:
    """Fetch a newer signed catalogue if there is one.

    Returns True when the cache now holds a verified catalogue newer than
    `shipped`. Everything is best-effort: offline, unsigned, tampered or simply
    older all end the same way -- the shipped catalogue keeps being used.
    """
    if not _download("catalogue.toml", CATALOGUE):
        return False
    if not _download("catalogue.toml.sig", SIGNATURE):
        return False

    if not verify():
        # Do not keep a catalogue that failed verification: a later run must not
        # find it sitting in the cache and wonder.
        CATALOGUE.unlink(missing_ok=True)
        SIGNATURE.unlink(missing_ok=True)
        return False

    # Refuse a rollback. A signature stays valid forever, so without this an
    # older catalogue could be replayed to reinstate a removed entry.
    if _serial(CATALOGUE) < _serial(shipped):
        CATALOGUE.unlink(missing_ok=True)
        SIGNATURE.unlink(missing_ok=True)
        return False

    return True


def verified_catalogue(shipped: Path) -> Path | None:
    """The cached catalogue, if it verifies *and* is not older than `shipped`.

    Verifying is not sufficient on its own. A signature never expires, so a
    cache fetched before a package upgrade keeps verifying happily after one --
    and would then be preferred over the newer catalogue the upgrade just
    installed, hiding every entry added since. `update()` refuses that rollback
    when fetching; this is the same rule applied when choosing, which is the
    half that was missing: after an upgrade the app showed the old catalogue
    for one launch, because the cache was only discarded as a side effect of
    the next fetch.

    The helper has always compared serials here, so this was never a hole in
    the allowlist -- an entry the UI would not show could not be installed
    either. It was the reverse: entries that were legitimately there stayed
    invisible.
    """
    if not verify():
        return None
    if _serial(CATALOGUE) < _serial(shipped):
        return None
    return CATALOGUE
