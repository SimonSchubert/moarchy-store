"""The pacman sync database, for the architecture the phone actually runs.

`sweepdb.py` answers what we have already decided. This answers what can be
installed at all, and it exists because the other sweeps were quietly asking
about the wrong machine.

**The architecture.** `archlinux-appstream-data` is Arch's x86_64 catalogue, and
every sweep so far has read it while the device installs from Arch Linux ARM.
2,343 packages exist on x86_64 and not on aarch64. That gap is not theoretical:
plasma-dialer, spacebar, portfolio-file-manager and livi sat in `catalogue.toml`
as ordinary rows with Install buttons that could not work, because Arch Linux ARM
had stopped building them and nothing on this side noticed. AppStream stays the
source for what an app *claims*; this is the source for whether it is there.

**The dependency list.** `sweep-aur.py` infers from dependencies because the AUR
publishes no declarations. The repos publish both and only the declarations were
ever read, so an app that links libadwaita and says nothing about display width
was invisible to `sweep-discover.py` -- a blind spot 56 packages wide. The sync
database carries `%DEPENDS%` for everything, free, with no VM and no lease.

Cached beside the other catalogues under `~/.cache/moarchy-store-sweep` and
re-fetched on the same one-week rule, for the same reason: a sweep that silently
reads a month-old snapshot and reports nothing new looks exactly like one that
read today's and found nothing new.
"""

from __future__ import annotations

import io
import os
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweepdb  # noqa: E402

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "moarchy-store-sweep"

# Arch Linux ARM, not archlinux.org. This is the whole point of the module.
MIRROR = os.environ.get("MOARCHY_ARM_MIRROR", "http://mirror.archlinuxarm.org/aarch64")
REPOS = ("core", "extra")

_CACHED: dict[str, dict] | None = None


def _parse(blob: bytes, repo: str, into: dict[str, dict]) -> None:
    """One `<repo>.db`, which is a tar of `<pkg>-<ver>-<rel>/{desc,depends}`.

    The two files are parsed together because neither is complete: `desc` has
    the name and the size, `depends` has what it links. A package with no
    `depends` member is normal -- it depends on nothing.
    """
    fields: dict[str, dict[str, list[str]]] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or member.name.count("/") != 1:
                continue
            stem, kind = member.name.split("/")
            if kind not in ("desc", "depends"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            text = handle.read().decode("utf-8", "replace")
            block = fields.setdefault(stem, {})
            for key, body in re.findall(r"^%([A-Z0-9]+)%\n((?:[^\n]+\n)*)", text, re.M):
                block[key] = body.split()

    for block in fields.values():
        name = (block.get("NAME") or [""])[0]
        if not name:
            continue
        into[name] = {
            "repo": repo,
            "version": (block.get("VERSION") or [""])[0],
            "desc": " ".join(block.get("DESC") or []),
            "url": (block.get("URL") or [""])[0],
            "isize": int((block.get("ISIZE") or ["0"])[0] or 0),
            # `libadwaita>=1.6` and `libadwaita` are the same statement of intent.
            "depends": {re.split(r"[<>=:]", d)[0] for d in (block.get("DEPENDS") or [])},
            "provides": {re.split(r"[<>=:]", d)[0] for d in (block.get("PROVIDES") or [])},
        }


def packages(refresh: bool = False) -> dict[str, dict]:
    """Every aarch64 package in core and extra, by name.

    Memoised: a sweep asks this three or four times in one run and the parse is
    the expensive part.
    """
    global _CACHED
    if _CACHED is not None and not refresh:
        return _CACHED

    CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    for repo in REPOS:
        path = CACHE / f"{repo}.db"
        sweepdb.note_age(path, f"aarch64 {repo}")
        if refresh or sweepdb.is_stale(path):
            url = f"{MIRROR}/{repo}/{repo}.db"
            print(f"fetching {url} into {CACHE} ...", file=sys.stderr)
            with urllib.request.urlopen(url, timeout=180) as response:
                path.write_bytes(response.read())
        _parse(path.read_bytes(), repo, out)
    _CACHED = out
    return out


def names(refresh: bool = False) -> set[str]:
    """Just the names, for `is it installable at all`."""
    return set(packages(refresh))


def depends(pkg: str) -> set[str]:
    """What one package links, or an empty set if it is not in the repos."""
    return packages().get(pkg, {}).get("depends", set())


def missing(idents) -> list[str]:
    """Which of these are not on aarch64 -- the offline `--check-repos`.

    Plugin idents are reverse-DNS and are not packages at all, so they are not
    asked about.
    """
    have = names()
    return sorted(i for i in idents if i and "." not in i and i not in have)
