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


def check_repos(packages: set[str]) -> None:
    """Ask the guest whether every catalogued package still exists.

    Not a style check. plasma-dialer, spacebar and livi sat in the catalogue
    long after Arch Linux ARM stopped building them for aarch64, so three rows
    offered an Install button that could not work -- and two of them were the
    dialer and the SMS app, which commit 13bf411 added saying a phone store
    without calls and SMS was missing the point.

    Needs the VM, so it is behind a flag rather than run every time.
    """
    vm = Path(os.environ.get("MOARCHY_VM", Path.home() / "Projects" / "omarchy-mobile"))
    ssh = vm / "scripts" / "vm-ssh.sh"
    if not ssh.exists():
        warn(f"--check-repos needs the VM at {vm}; skipped")
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
        err(f"{missing} is in the catalogue and not in the repos -- its Install "
            "button cannot work")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-repos", action="store_true",
                    help="ask the VM whether every package still exists (slow)")
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

    # AppRow gives the summary two lines and then ellipsises it (window.py's
    # set_subtitle_lines(2)). At 360px that is about 80 characters -- measured,
    # not guessed: a 79-character summary renders in full and an 82 loses its
    # last word. A
    # summary longer than that loses its last clause on the one surface most
    # people ever read.
    for entry in entries:
        summary = entry.get("summary", "")
        if len(summary) > 80:
            warn(f"{entry.get('name')}: summary is {len(summary)} chars and the "
                 "list row shows about 80")

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

    if args.check_repos:
        check_repos(packages)

    # --- report ------------------------------------------------------------
    for line in warnings:
        print(f"warn: {line}")
    for line in errors:
        print(f"ERROR: {line}")

    print(f"\n{len(entries)} apps, {len([m for m in meta.values() if isinstance(m, dict)])} "
          f"with metadata, {len(list(shots_dir.glob('*.png')))} screenshots "
          f"({total / 1024 / 1024:.1f} MB)")
    print(f"catalogue.toml {size:,} bytes, {(size / REMOTE_MAX) * 100:.1f}% of the "
          f"remote cap ({REMOTE_MAX - size:,} bytes of headroom)")
    print(f"{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
