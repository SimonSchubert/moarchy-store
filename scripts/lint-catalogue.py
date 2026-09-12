#!/usr/bin/env python3
"""Check the catalogue and its metadata before publishing.

Three kinds of check, and only the first is about tidiness:

  * the allowlist invariants, because catalogue.toml is what the privileged
    helper parses as root and a plugin id that leaks into it would be a
    package name root is willing to install;
  * the byte budget, because remote.py refuses a fetched catalogue over 1 MB
    and does it silently -- an oversized catalogue does not fail loudly, it
    just stops reaching everyone who already installed the package;
  * the metadata's own consistency: every screenshot exists, every feature is
    a word we have agreed on, every entry names an app that is really there.

Run it from the repo root. Exits non-zero on an error, zero on warnings.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweepdb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# remote.py:31. Not a style rule: a published catalogue past this is fetched
# by nobody, and update() returns False without saying why.
REMOTE_MAX = 1024 * 1024
CATALOGUE_WARN = 500_000

SHOT_WARN = 120 * 1024
SHOT_MAX = 200 * 1024
SHOTS_TOTAL_WARN = 25 * 1024 * 1024

# data/moarchy-store-helper's own validation, restated so a bad name is caught
# here rather than by a refusal on the device.
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9@._+-]*$")

ADAPTIVE = {"fits", "tight", "clipped", "no-window", "unknown", ""}
THEMED = {"yes", "no", "partial", "unknown", ""}

# The per-category vocabulary. One flat list of strings in the TOML whatever
# the category, so the schema never varies -- only this table does, and this is
# what keeps "Matrix" from also being spelled "matrix" and "matrix-protocol".
VOCAB: dict[str, set[str]] = {
    "Chat": {"SMS", "MMS", "XMPP", "Matrix", "IRC", "Signal", "Telegram",
             "Mastodon", "OMEMO", "E2EE", "File transfer", "Group chats",
             "Voice calls", "Video calls"},
    "Reading": {"EPUB", "Mobi", "FB2", "PDF", "CBZ", "RSS", "Atom",
                "Annotations", "TTS", "Offline"},
    "Media": {"MP3", "FLAC", "Ogg", "Opus", "Podcasts", "Radio", "YouTube",
              "Subtitles", "Playlists", "Gapless", "ReplayGain", "Offline"},
    "Travel": {"Offline maps", "Routing", "Departures", "Boarding passes",
               "Timetables", "GPS"},
    "Files": {"Archives", "Search", "Thumbnails", "Network shares", "Trash"},
    "Utilities": {"TOTP", "HOTP", "Backup", "Sync", "Offline", "Encryption",
                  "QR codes", "Unit conversion", "Alarms", "Timers",
                  "Disk usage", "Remote desktop", "File transfer"},
    "Web": {"Ad blocking", "Reader mode", "Sync", "Extensions"},
    "Phone": {"SMS", "MMS", "Calls", "Contacts", "Voicemail", "CardDAV", "Sync"},
    "Notes": {"Markdown", "Sync", "Encryption", "Offline", "Tags"},
    "Terminal": {"Tabs", "Sixel", "Ligatures"},
    "Security": {"TOTP", "HOTP", "KeePass", "GPG", "Keyring", "Encryption",
                 "Backup", "Sync", "Offline", "Shredding"},
    "Time": {"Alarms", "Timers", "Stopwatch", "World clocks", "CalDAV", "Sync",
             "Offline", "Pomodoro", "Recurring", "Reminders"},
    "System": {"Disk usage", "Remote desktop", "VNC", "RDP", "File transfer",
               "Clipboard", "Process list", "Offline"},
    "Games": {"Offline", "Single player", "Multiplayer", "Puzzles", "Touch controls"},
}

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)



def check_serial_rose(serial: int, cat_path: Path) -> None:
    """A changed catalogue must carry a higher serial than the published one.

    remote.py refuses a fetched catalogue whose serial is *lower* than the one
    it already trusts, which stops a replayed old catalogue re-adding something
    removed. It cannot stop two different catalogues sharing a serial, and that
    is the mistake a human makes: edit a summary, re-sign, publish, and every
    client that already has that serial is holding different bytes under the
    same name.

    Compared against origin/main because that is what clients actually fetch.
    """
    try:
        published = subprocess.run(
            ["git", "show", "origin/main:catalogue.toml"],
            cwd=ROOT, capture_output=True, timeout=30)
        if published.returncode != 0:
            return
        old = tomllib.loads(published.stdout.decode())
    except Exception:
        return
    if published.stdout == cat_path.read_bytes():
        return   # unchanged; the serial is allowed to stay put
    old_serial = old.get("serial")
    if isinstance(old_serial, int) and serial <= old_serial:
        err(f"catalogue.toml differs from origin/main but serial is still "
            f"{serial} -- bump it, or two different catalogues share a serial")


def check_repos(packages: set[str], vm: bool = False) -> None:
    """Ask the guest whether every catalogued package still exists.

    Not a style check. plasma-dialer, spacebar and livi sat in the catalogue
    long after Arch Linux ARM stopped building them for aarch64, so three rows
    offered an Install button that could not work -- and two of them were the
    dialer and the SMS app, which commit 13bf411 added saying a phone store
    without calls and SMS was missing the point.

    Two ways of asking, and they are not equivalent. The guest's own `pacman
    -Si` is the authority: it is the machine that will run the install, with
    the mirrors it actually has. The sync database read by scripts/syncdb.py is
    the same question put to a mirror, needs no VM and no lease, and therefore
    can run on every lint rather than only before a publish -- which matters,
    because the four dead entries sat there for weeks and the flag that would
    have caught them was the one nobody ran.

    So: the offline check always runs, and the VM check runs when asked and
    overrules it.
    """
    try:
        import syncdb
        gone = syncdb.missing(packages)
    except Exception as exc:
        warn(f"offline repo check unavailable: {exc}")
        gone = []
    for missing in gone:
        err(f"{missing} is in the catalogue and not in the aarch64 sync "
            "database -- its Install button cannot work")

    if not vm:
        return
    vm_dir = Path(os.environ.get("MOARCHY_VM", Path.home() / "Projects" / "omarchy-mobile"))
    ssh = vm_dir / "scripts" / "vm-ssh.sh"
    if not ssh.exists():
        warn(f"--check-repos needs the VM at {vm_dir}; skipped the authoritative half")
        return
    names = " ".join(sorted(packages))
    try:
        out = subprocess.run(
            [str(ssh), f"for p in {names}; do pacman -Si $p >/dev/null 2>&1 || echo $p; done"],
            capture_output=True, text=True, timeout=300)
    except Exception as exc:
        warn(f"--check-repos could not reach the VM: {exc}")
        return
    for missing in out.stdout.split():
        if missing in gone:
            continue          # already reported, offline
        err(f"{missing} is in the catalogue and not in the repos -- its Install "
            "button cannot work")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-repos", action="store_true",
                    help="also ask the VM itself, which is the authority (slow)")
    args = ap.parse_args()

    cat_path = ROOT / "catalogue.toml"
    with cat_path.open("rb") as fh:
        cat = tomllib.load(fh)

    entries = cat.get("app", [])
    idents: dict[str, dict] = {}
    packages: set[str] = set()

    for entry in entries:
        source = entry.get("source", "pacman")
        name = entry.get("name", "<unnamed>")
        if source == "pacman":
            pkg = entry.get("pkg", "")
            if not pkg:
                err(f"{name}: a pacman entry with no pkg")
                continue
            if not PKG_RE.match(pkg):
                err(f"{pkg}: not a legal package name for the helper")
            if pkg in packages:
                err(f"{pkg}: listed twice")
            packages.add(pkg)
            idents[pkg] = entry
        elif source == "plugin":
            ident = entry.get("id", "")
            if not ident:
                err(f"{name}: a plugin entry with no id")
                continue
            if entry.get("pkg"):
                err(f"{ident}: a plugin entry carrying a pkg -- it would enter the allowlist")
            if ident in idents:
                err(f"{ident}: listed twice")
            idents[ident] = entry
        else:
            err(f"{name}: unknown source {source!r}")

    # The allowlist is what a password authorises. A plugin id in it would be a
    # package name root is willing to hand to pacman.
    for entry in entries:
        if entry.get("source") == "plugin" and entry.get("id") in packages:
            err(f"{entry['id']}: a plugin id also appears as a package")

    # The summary is the detail page's one-liner, under the name and above the
    # chips, and it is no longer cut off -- the grid replaced the list row that
    # used to give it two lines and ellipsise the rest. The budget survives the
    # change with a different reason: at 360px about 80 characters is two lines
    # of that blurb, and every line past them pushes Install further below the
    # fold on a 720px screen.
    for entry in entries:
        summary = entry.get("summary", "")
        if len(summary) > 80:
            warn(f"{entry.get('name')}: summary is {len(summary)} chars and two "
                 "lines under the app's name is about 80")

    serial = cat.get("serial")
    if not isinstance(serial, int):
        err("catalogue.toml has no integer serial")
    else:
        check_serial_rose(serial, cat_path)

    size = cat_path.stat().st_size
    if size > REMOTE_MAX:
        err(f"catalogue.toml is {size:,} bytes -- over remote.py's {REMOTE_MAX:,} "
            "cap, so no installed client will ever fetch it again")
    elif size > CATALOGUE_WARN:
        warn(f"catalogue.toml is {size:,} bytes, {REMOTE_MAX - size:,} from the cap")

    # --- metadata.toml -----------------------------------------------------
    meta_path = ROOT / "metadata.toml"
    meta: dict = {}
    if meta_path.exists():
        with meta_path.open("rb") as fh:
            meta = tomllib.load(fh)

    shots_dir = ROOT / "screenshots"
    referenced: set[str] = set()

    for ident, entry in meta.items():
        if not isinstance(entry, dict):
            continue  # version, and anything else scalar at the top level
        if ident not in idents:
            err(f"metadata.toml [{ident}]: no such app in catalogue.toml")
            continue
        app = idents[ident]
        category = app.get("category", "Other")

        adaptive = entry.get("adaptive", "")
        if adaptive not in ADAPTIVE:
            err(f"{ident}: adaptive = {adaptive!r} is not one of {sorted(ADAPTIVE - {''})}")
        themed = entry.get("themed", "")
        if themed not in THEMED:
            err(f"{ident}: themed = {themed!r} is not one of {sorted(THEMED - {''})}")

        # A verdict nobody can check is not a verdict.
        if adaptive in ("clipped", "no-window") and not entry.get("measured"):
            err(f"{ident}: adaptive = {adaptive!r} without a measured field to back it")

        known = VOCAB.get(category, set())
        for feature in entry.get("features", []):
            if known and feature not in known:
                warn(f"{ident}: feature {feature!r} is not in the {category} vocabulary")

        for shot in entry.get("shots", []):
            referenced.add(shot)
            if not (shots_dir / shot).exists():
                err(f"{ident}: shots names {shot}, which is not in screenshots/")

    for entry in entries:
        shot = entry.get("screenshot", "")
        if shot:
            referenced.add(shot)
            if not (shots_dir / shot).exists():
                err(f"{entry.get('name')}: screenshot {shot} is not in screenshots/")

    # --- the Editor's Choice shelf ----------------------------------------
    # The front page tells everyone who opens the store that each pick was
    # measured at 360px, follows their theme, and costs a few megabytes. That
    # sentence lives in window.py, so what it asserts has to be checkable
    # here -- otherwise the one screen nobody can avoid is the one making a
    # claim nobody verified.
    featured = []
    for entry in entries:
        flag = entry.get("featured")
        if flag is None:
            continue
        if not isinstance(flag, bool):
            err(f"{entry.get('name')}: featured = {flag!r} is not a boolean")
        elif flag:
            featured.append(entry)

    for entry in featured:
        ident = entry.get("pkg") or entry.get("id", "")
        name = entry.get("name", ident)
        measurements = meta.get(ident, {})
        if not entry.get("tested"):
            err(f"{name}: featured without a tested device")
        adaptive = measurements.get("adaptive", "")
        if adaptive != "fits":
            err(f"{name}: featured, but adaptive = {adaptive!r} -- the shelf "
                "says every pick was measured at 360px")
        themed = measurements.get("themed", "")
        if themed != "yes":
            err(f"{name}: featured, but themed = {themed!r} -- the shelf says "
                "every pick follows your theme")

    if not featured:
        warn("nothing is featured, so the front page has no Editor's Choice shelf")
    elif len(featured) > 12:
        warn(f"{len(featured)} apps are featured; the shelf is meant to be about "
             "ten, and one that lists everything recommends nothing")

    # --- sweep/verdicts.toml ----------------------------------------------
    # The notebook and the catalogue have to agree about what shipped, or the
    # notebook stops being usable as a reason to skip re-testing something.
    verdicts_path = ROOT / "sweep" / "verdicts.toml"
    if verdicts_path.exists():
        with verdicts_path.open("rb") as fh:
            verdicts = tomllib.load(fh)
        for ident, entry in verdicts.items():
            if not isinstance(entry, dict):
                continue
            outcome = entry.get("outcome", "")
            if outcome not in ("listed", "rejected", "deferred"):
                err(f"verdicts.toml [{ident}]: outcome {outcome!r} is not "
                    "listed, rejected or deferred")
            if outcome == "listed" and ident not in idents:
                err(f"verdicts.toml says {ident} is listed, but it is not in catalogue.toml")
            if outcome in ("rejected", "deferred") and ident in idents:
                err(f"verdicts.toml says {ident} is {outcome}, but catalogue.toml lists it")
            if outcome == "rejected" and not entry.get("reason", "").strip():
                err(f"verdicts.toml [{ident}]: rejected without a reason")
        # Most of the catalogue predates the notebook, so this is a count
        # rather than 62 separate warnings. It should fall over time.
        missing = [i for i in idents if i not in verdicts]
        if missing:
            warn(f"{len(missing)} catalogued apps have no verdict recorded yet "
                 f"(e.g. {', '.join(sorted(missing)[:3])})")

    total = 0
    for path in sorted(shots_dir.glob("*.png")):
        total += path.stat().st_size
        if path.name not in referenced:
            warn(f"screenshots/{path.name} is referenced by nothing")
        if path.stat().st_size > SHOT_MAX:
            err(f"screenshots/{path.name} is {path.stat().st_size // 1024} KB -- "
                "run scripts/sweep-shots.sh over it")
        elif path.stat().st_size > SHOT_WARN:
            warn(f"screenshots/{path.name} is {path.stat().st_size // 1024} KB")
    if total > SHOTS_TOTAL_WARN:
        warn(f"screenshots/ is {total / 1024 / 1024:.1f} MB")

    check_repos(packages, vm=args.check_repos)

    stale = sweepdb.needs_measure()
    if stale:
        without = [i for i, _, shot in stale if not shot]
        legacy = [i for i, _, shot in stale if shot]
        if legacy:
            warn(f"{len(legacy)} apps have a screenshot from before the harness and "
                 f"no themed/adaptive/cost (e.g. {', '.join(legacy[:3])}) -- "
                 "./scripts/sweep-measure.sh each, keeping the old picture")
        if without:
            warn(f"{len(without)} apps have never been measured and have no screenshot "
                 f"(e.g. {', '.join(without[:3])})")

    # --- report ------------------------------------------------------------
    for line in warnings:
        print(f"warn: {line}")
    for line in errors:
        print(f"ERROR: {line}")

    print(f"\n{len(entries)} apps, {len([m for m in meta.values() if isinstance(m, dict)])} "
          f"with metadata, {len(featured)} featured, "
          f"{len(list(shots_dir.glob('*.png')))} screenshots "
          f"({total / 1024 / 1024:.1f} MB)")
    print(f"catalogue.toml {size:,} bytes, {(size / REMOTE_MAX) * 100:.1f}% of the "
          f"remote cap ({REMOTE_MAX - size:,} bytes of headroom)")
    print(f"{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
