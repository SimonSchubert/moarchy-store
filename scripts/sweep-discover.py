#!/usr/bin/env python3
"""Find likely phone apps in the Arch repos, without installing anything.

    ./scripts/sweep-discover.py                 # ranked candidates, as a table
    ./scripts/sweep-discover.py --json          # the same, as JSON
    ./scripts/sweep-discover.py --all           # every desktop app, scored
    ./scripts/sweep-discover.py --category Chat # one category
    ./scripts/sweep-discover.py --refresh       # re-download the AppStream data

`pacman -Ss` lists everything and says nothing about whether it fits. AppStream
does, in upstream's own words: an app that means to run on a phone declares a
minimum display length and touch support. Of 1379 desktop applications in the
repos, about 200 declare either -- so this is a filter that does most of the
work before anyone boots a VM.

It reads one file: the `archlinux-appstream-data` package, fetched from a
mirror and cached under ~/.cache. That is deliberate -- discovery needs no VM,
no lease and no waiting for whoever is using it. The VM is for the half of the
question AppStream cannot answer, which is whether the app is telling the truth.

What it reads per app:

    <requires|supports><display_length>   the narrowest screen upstream claims
    <supports|recommends><control>touch   whether it says it is touchable
    <releases><release date=>             when it was last released. The honest
                                          activity signal, and free. NOT
                                          pacman's Build Date, which Arch
                                          refreshes on every soname bump, so a
                                          long-dead upstream looks alive.
    <categories>                          freedesktop categories, for grouping
    <name> <summary> <description> <url>  prose to seed an entry with

Nothing here decides anything. It ranks by how strong upstream's own claim is;
scripts/sweep-measure.sh and a person still have to run it and look.

The AUR is deliberately absent, for a structural reason rather than a
preference: data/moarchy-store-helper execs `/usr/bin/pacman -S`, so an AUR
package cannot be installed through the store at all. An AUR entry would be a
row with a button that cannot work.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweepdb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "moarchy-store-sweep"
PKG_URL = "https://archlinux.org/packages/extra/any/archlinux-appstream-data/download/"

# Desktop-shaped by category: things nobody drives with a thumb however well
# they reflow.
DESKTOP_ONLY = {
    "IDE", "Debugger", "RevisionControl", "Profiling", "Engineering",
    "WebDevelopment", "Emulator", "DesktopSettings", "HardwareSettings",
    "PackageManager", "Printing", "Monitor", "TerminalEmulator", "Building",
}

# Not apps, whatever their AppStream record says: desktop shells, session
# components and the GNOME control centre all declare touch and 360 and none of
# them is a thing to install from a store.
NOT_APPS = {
    "gnome-shell", "gnome-control-center", "gnome-initial-setup", "gnome-session",
    "gnome-tour", "gnome-shell-extensions", "plasma-workspace", "plasma-desktop",
    "gdm", "sddm", "xdg-desktop-portal-gnome", "malcontent",
}

# AppStream allows names as well as pixel counts.
NAMED_WIDTHS = {"xsmall": 360, "small": 768, "medium": 1024, "large": 1280, "xlarge": 1920}


def appstream_files(refresh: bool = False) -> list[str]:
    """The repo-wide AppStream catalogue, downloaded once and cached."""
    xml_dir = CACHE / "usr" / "share" / "swcatalog" / "xml"
    found = sorted(glob.glob(str(xml_dir / "*.xml.gz")))
    if found and not refresh and not sweepdb.is_stale(xml_dir):
        sweepdb.note_age(xml_dir, "AppStream")
        return found

    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / "archlinux-appstream-data.pkg.tar.zst"
    print(f"fetching the AppStream catalogue into {CACHE} ...", file=sys.stderr)
    with urllib.request.urlopen(PKG_URL, timeout=60) as response:
        archive.write_bytes(response.read())

    # Python's tarfile cannot read zstd before 3.14, and the system tar can.
    subprocess.run(
        ["tar", "--use-compress-program=unzstd", "-xf", str(archive), "-C", str(CACHE),
         "usr/share/swcatalog"],
        check=True, capture_output=True,
    )
    archive.unlink(missing_ok=True)
    return sorted(glob.glob(str(xml_dir / "*.xml.gz")))


XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _text(node, path: str) -> str:
    """The untranslated value.

    Collection AppStream carries every translation inline, and the C locale is
    simply the one with no xml:lang. find() returns the first match and the
    translations come after it -- but only usually, so this picks the
    untranslated element explicitly rather than trusting document order. Getting
    this wrong fills the catalogue with whichever language sorts last, which is
    exactly what it did the first time.
    """
    chosen = None
    for found in node.findall(path):
        if XML_LANG not in found.attrib:
            chosen = found
            break
    if chosen is None:
        return ""
    return " ".join("".join(chosen.itertext()).split())


def _description(comp) -> str:
    """The first paragraph of the description, and only that.

    <description> is <p> and <ul> together, and flattening all of it produces a
    wall: the list items have no separator once the markup is gone, so Foliate
    came out as "...and PDF files Paginated mode and scrolled mode Customize
    font and line-spacing Light, sepia, dark, and invert mode". The first
    paragraph is the part that reads like prose; the bullets are a feature list,
    and `features` is where those belong -- picked by hand, from the vocabulary,
    rather than concatenated.
    """
    for node in comp.findall("description"):
        if XML_LANG in node.attrib:
            continue
        for para in node.findall("p"):
            if XML_LANG in para.attrib:
                continue
            text = " ".join("".join(para.itertext()).split())
            if text:
                return text
    return ""


def _min_width(comp) -> int | None:
    """The narrowest screen upstream says it runs on, or None if it is silent.

    <display_length compare="ge">360</> reads "needs 360 or more", so the number
    IS the minimum width. `compare` defaults to "ge" when absent, which is how
    almost every real component spells it.
    """
    best = None
    for parent in ("requires", "supports", "recommends"):
        for node in comp.findall(f"{parent}/display_length"):
            raw = (node.text or "").strip()
            value = int(raw) if raw.isdigit() else NAMED_WIDTHS.get(raw.lower())
            if value is None:
                continue
            if node.get("compare", "ge") in ("ge", "eq"):
                best = value if best is None else min(best, value)
    return best


def _touch(comp) -> bool:
    return any(
        (node.text or "").strip() == "touch"
        for parent in ("supports", "recommends")
        for node in comp.findall(f"{parent}/control")
    )


def _last_release(comp) -> str:
    best = ""
    for node in comp.findall("releases/release"):
        stamp = (node.get("date") or "")[:10]
        if not stamp and (node.get("timestamp") or "").isdigit():
            stamp = datetime.fromtimestamp(int(node.get("timestamp")), UTC).strftime("%Y-%m-%d")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp) and stamp > best:
            best = stamp
    return best


def known_packages() -> set[str]:
    """Listed or decided. NOT deferred -- see scripts/sweepdb.py."""
    return sweepdb.skip()


def collect(refresh: bool = False) -> list[dict]:
    today = date.today()
    rows: dict[str, dict] = {}
    for path in appstream_files(refresh):
        origin = Path(path).name.split(".")[0]
        with gzip.open(path, "rb") as fh:
            root = ET.parse(fh).getroot()
        for comp in root.findall("component"):
            if comp.get("type") != "desktop-application":
                continue
            pkg = _text(comp, "pkgname")
            if not pkg:
                continue

            cats = [c.text for c in comp.findall("categories/category") if c.text]
            width, touch = _min_width(comp), _touch(comp)
            released = _last_release(comp)
            age = None
            if released:
                try:
                    age = (today - date.fromisoformat(released)).days
                except ValueError:
                    pass

            # The score is upstream's confidence, not ours. Two independent
            # declarations beat one; a recent release beats a stale one.
            score = 0
            if width is not None and width <= 360:
                score += 50
            if touch:
                score += 30
            if width is not None and width <= 360 and touch:
                score += 10
            if age is not None:
                score += 15 if age <= 365 else 5 if age <= 1095 else -10
            if set(cats) & DESKTOP_ONLY:
                score -= 40

            row = {
                "pkg": pkg, "id": _text(comp, "id"), "origin": origin,
                "name": _text(comp, "name"), "summary": _text(comp, "summary"),
                "description": _description(comp),
                "categories": cats, "min_width": width, "touch": touch,
                "released": released, "age_days": age,
                "homepage": _text(comp, "url[@type='homepage']"),
                "score": score,
            }
            if pkg not in rows or score > rows[pkg]["score"]:
                rows[pkg] = row
    return sorted(rows.values(), key=lambda r: (-r["score"], r["pkg"]))



def report_deferred() -> None:
    """Deferred verdicts old enough to re-ask, printed after the candidates.

    A periodic sweep that only ever shows new packages slowly forgets the
    things it decided to come back to. These are not new and are not noise:
    each one was set aside with a written reason, and the reason may have
    expired.
    """
    due = sweepdb.deferred_due()
    if not due:
        pending = len([1 for v in sweepdb.verdicts().values()
                       if v.get("outcome") == "deferred"])
        if pending:
            print(f"\n{pending} deferred, none older than "
                  f"{sweepdb.DEFERRED_DAYS} days yet (--deferred to see them)")
        return
    print(f"\n{len(due)} deferred and worth re-asking (--deferred for the reasons):")
    for ident, when, _ in due[:10]:
        print(f"  {ident:28} deferred {when}")

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="JSON instead of a table")
    ap.add_argument("--all", action="store_true", help="every app, including known ones")
    ap.add_argument("--category", help="only apps in this freedesktop category")
    ap.add_argument("--refresh", action="store_true", help="re-download the AppStream data")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--deferred", action="store_true",
                    help="list deferred verdicts due for another look, and stop")
    args = ap.parse_args()

    if args.deferred:
        for ident, when, reason in sweepdb.deferred_due(0):
            print(f"{ident}  ({when})\n    {reason[:160]}")
        return 0

    rows = collect(args.refresh)
    total = len(rows)

    if not args.all:
        known = known_packages()
        rows = [
            r for r in rows
            if r["pkg"] not in known
            and r["pkg"] not in NOT_APPS
            and ((r["min_width"] is not None and r["min_width"] <= 360) or r["touch"])
            and not (set(r["categories"]) & DESKTOP_ONLY)
        ]
    if args.category:
        rows = [r for r in rows if args.category in r["categories"]]

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
        return 0

    print(f"{'package':30} {'sc':>3} {'width':>5} {'touch':>5} {'released':>10}  categories")
    print("-" * 96)
    for r in rows[: args.limit]:
        print(f"{r['pkg'][:30]:30} {r['score']:3} {str(r['min_width'] or '-'):>5} "
              f"{'yes' if r['touch'] else '-':>5} {r['released'] or '-':>10}  "
              f"{','.join(r['categories'][:3])[:40]}")
    shown = min(len(rows), args.limit)
    print(f"\n{shown} of {len(rows)} candidates ({total} desktop apps in the repos)")
    report_deferred()
    return 0


if __name__ == "__main__":
    sys.exit(main())
