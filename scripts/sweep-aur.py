#!/usr/bin/env python3
"""Survey the AUR for apps that might work on a phone.

    ./scripts/sweep-aur.py                  # ranked candidates, as a table
    ./scripts/sweep-aur.py --json
    ./scripts/sweep-aur.py --limit 200
    ./scripts/sweep-aur.py --refresh        # re-download the AUR metadata
    ./scripts/sweep-aur.py --why <pkg>      # why one package scored what it did

Read scripts/sweep-discover.py first: the repos are the better hunting ground
and this is the harder one, for two reasons worth stating before anyone trusts
the output.

**The AUR ships no AppStream data.** sweep-discover.py can ask upstream what
minimum width it claims, because `archlinux-appstream-data` carries the whole
repo's metainfo. Nothing equivalent exists here. So the signal has to be
inferred from what the AUR does publish -- dependencies, description, keywords
-- and inference is weaker than a declaration. A high score here means "worth
ten minutes in the VM", not "this fits".

**Nothing found here is installable through the store.** moarchy-store-helper
execs `/usr/bin/pacman -S`, and an AUR package is not in any sync database, so
the helper cannot install it and the allowlist cannot cover it. Building one
means makepkg running arbitrary PKGBUILD code, which is a different privilege
question entirely and not one the polkit action answers. Output from this
script therefore belongs in sweep/verdicts.toml as a survey. Promoting any of
it into catalogue.toml needs a decision about AUR support that has not been
taken.

The strongest inference available is the dependency list, which is the same
signal the serial-3 repo sweep used: libadwaita and libhandy are GNOME's
adaptive widget sets, Kirigami is Plasma Mobile's, and a package that links one
of them was written by somebody who had narrow screens in mind. After that come
the phone projects by name -- a package that says PinePhone, Phosh or
postmarketOS is telling you exactly what it is for.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweepdb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "moarchy-store-sweep"
AUR_META = "https://aur.archlinux.org/packages-meta-ext-v1.json.gz"

# The adaptive widget sets. Linking one of these is the single best mechanical
# hint the AUR offers, because neither is something you reach for by accident.
ADAPTIVE_DEPS = {
    "libadwaita": 50, "libadwaita-git": 50,
    "libhandy": 45, "libhandy1": 45, "libhandy-git": 45,
    "kirigami": 45, "kirigami2": 45, "kirigami6": 45,
    "kirigami-addons": 45, "kirigami-addons-git": 45,
}
# Qt Quick alone is not evidence of anything -- a QML desktop app looks the same
# from here -- but it is the substrate every Plasma Mobile app sits on, so it is
# worth a little when something else already points the right way.
QUICK_DEPS = {"qt6-declarative", "qt5-declarative", "qt6-quickcontrols2",
              "qt5-quickcontrols2", "qt6-5compat"}

# Naming the hardware or the distro is the clearest statement of intent there is.
PHONE_TERMS = {
    "pinephone": 60, "pine64": 30, "librem 5": 60, "librem5": 60,
    "phosh": 55, "plasma mobile": 55, "plasma-mobile": 55,
    "postmarketos": 55, "mobian": 50, "sailfish": 25, "ubuntu touch": 40,
    "furios": 45, "glacier": 30,
}
# Weaker, and easy to say about anything, so they are worth much less.
MOBILE_TERMS = {
    "convergent": 35, "convergence": 30, "adaptive": 25, "mobile-friendly": 35,
    "for mobile": 30, "mobile devices": 25, "touch-friendly": 30,
    "touchscreen": 25, "small screens": 35, "phone": 20, "handheld": 20,
}

# Not applications, whatever they depend on.
NOT_APPS = re.compile(
    r"(^|-)(theme|themes|icon-theme|icons|cursor|cursors|font|fonts|wallpaper|"
    r"wallpapers|sound-theme|kernel|linux|firmware|driver|drivers|dkms|"
    r"headers|docs|doc|lang|i18n|translations|sdk|devel|dev|debug|dbg|"
    r"plugin|plugins|extension|widget|applet|daemon|service|lib|libs)$"
)
NOT_APPS_PREFIX = re.compile(r"^(lib|python-|python2-|perl-|ruby-|haskell-|"
                             r"nodejs-|php-|rust-|golang-|ghc-|texlive-|"
                             r"r-|julia-|ocaml-|lua-|vim-|emacs-)")
# Desktop-shaped whatever else they say.
DESKTOP_WORDS = re.compile(
    r"\b(ide|integrated development|kernel module|window manager|compositor|"
    r"display manager|screensaver|bootloader|virtual machine|hypervisor|"
    r"cross-compil|toolchain|language server|build system|panel|taskbar|"
    r"systray|dock|conky|polybar|waybar|rofi|dmenu|wine|proton|"
    r"package manager|aur helper|game launcher|steam|openxr|monado|vr |racing|emulator|gamepad|joystick|rad tool|screencast|miracast|hamachi|vpn gateway|pipewire volume|noise reduction|soundboard|sound pad|audio router|surround|libalpm|patched to bring)\b", re.I)

# The thing this filter gets wrong if left alone. Searching for "phosh" and
# "Plasma Mobile" finds the phone software that scores highest of all -- and
# most of it is the shell, the keyboard, the greeter and the portal, which are
# what a phone image IS rather than things to install from a store. They score
# top precisely because they are the most mobile software in the AUR.
SHELL_WORDS = re.compile(
    r"\b(shell|greeter|display manager|on.?screen keyboard|osk|"
    r"xdg-desktop-portal|portal implementation|session|compositor|"
    r"shell components|prototype for gnome|homescreen|lockscreen|"
    r"kernel|u-boot|bootloader|initramfs|device tree|modem|"
    r"virtual mouse|virtual keyboard|touchpad emulator)\b", re.I)
SHELL_NAMES = re.compile(
    r"^(phosh|phoc|phog|squeekboard|stevia|plasma-mobile|plasma-settings|"
    r"maliit|mobile-broadband|eg25|modemmanager|callaudiod|feedbackd|"
    r"xdg-desktop-portal|linux-|uboot-|firmware-|casilda|buffyboard|"
    r"unl0kr|osk-sdl|moarchy-)")


def aur_packages(refresh: bool = False) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "aur-packages-meta.json.gz"
    sweepdb.note_age(path, "AUR")
    if refresh or sweepdb.is_stale(path):
        print(f"fetching the AUR package database into {CACHE} ...", file=sys.stderr)
        with urllib.request.urlopen(AUR_META, timeout=120) as response:
            path.write_bytes(response.read())
    with gzip.open(path, "rb") as fh:
        return json.load(fh)


def known() -> tuple[set[str], set[str]]:
    """Catalogued, and decided. Deferred is neither -- see scripts/sweepdb.py."""
    return sweepdb.catalogued(), sweepdb.skip()


def repo_packages() -> set[str]:
    """Everything the AppStream catalogue knows about, so an AUR package that
    duplicates a repo one can be dropped: the repo version installs through the
    helper and this one cannot."""
    try:
        spec = ROOT / "scripts" / "sweep-discover.py"
        import importlib.util
        s = importlib.util.spec_from_file_location("sweep_discover", spec)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        return {r["pkg"] for r in m.collect()}
    except Exception:
        return set()


def base_name(name: str) -> str:
    """`foo-git`, `foo-bin` and `foo` are one program with three packagings."""
    for suffix in ("-git", "-bin", "-beta", "-stable", "-appimage", "-nightly"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def score(pkg: dict) -> tuple[int, list[str]]:
    name = pkg["Name"]
    desc = (pkg.get("Description") or "").lower()
    keywords = " ".join(pkg.get("Keywords") or []).lower()
    url = (pkg.get("URL") or "").lower()
    deps = {re.split(r"[<>=]", d)[0] for d in (pkg.get("Depends") or [])}
    haystack = f"{desc} {keywords} {url}"

    points, why = 0, []

    best_dep = max((ADAPTIVE_DEPS.get(d, 0) for d in deps), default=0)
    if best_dep:
        hit = next(d for d in deps if ADAPTIVE_DEPS.get(d, 0) == best_dep)
        points += best_dep
        why.append(f"depends on {hit} (+{best_dep})")
    elif deps & QUICK_DEPS:
        points += 10
        why.append("depends on Qt Quick (+10)")

    for term, weight in PHONE_TERMS.items():
        if term in haystack:
            points += weight
            why.append(f"says {term!r} (+{weight})")
            break
    for term, weight in MOBILE_TERMS.items():
        if term in haystack:
            points += weight
            why.append(f"says {term!r} (+{weight})")
            break

    # Alive, and wanted by somebody.
    votes = pkg.get("NumVotes", 0)
    for threshold, weight in ((50, 15), (10, 8), (3, 3)):
        if votes >= threshold:
            points += weight
            why.append(f"{votes} votes (+{weight})")
            break

    modified = pkg.get("LastModified", 0)
    if modified:
        age = (datetime.now(UTC) - datetime.fromtimestamp(modified, UTC)).days
        weight = 12 if age <= 365 else 4 if age <= 1095 else -15
        points += weight
        why.append(f"last touched {age}d ago ({weight:+d})")

    for condition, weight, reason in (
        (pkg.get("OutOfDate"), -25, "flagged out of date"),
        (not pkg.get("Maintainer"), -20, "orphaned"),
        (NOT_APPS.search(name) or NOT_APPS_PREFIX.match(name), -60,
         "named like a library or asset"),
        (DESKTOP_WORDS.search(desc), -40, "described as desktop infrastructure"),
        (SHELL_WORDS.search(desc) or SHELL_NAMES.match(name), -80,
         "part of the phone image, not an app for it"),
    ):
        if condition:
            points += weight
            why.append(f"{reason} ({weight:+d})")

    return points, why



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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--min-score", type=int, default=45)
    ap.add_argument("--why", metavar="PKG")
    ap.add_argument("--all", action="store_true", help="skip the known/dupe filters")
    ap.add_argument("--deferred", action="store_true",
                    help="list deferred verdicts due for another look, and stop")
    args = ap.parse_args()

    if args.deferred:
        for ident, when, reason in sweepdb.deferred_due(0):
            print(f"{ident}  ({when})\n    {reason[:160]}")
        return 0

    packages = aur_packages(args.refresh)

    if args.why:
        for pkg in packages:
            if pkg["Name"] == args.why:
                points, why = score(pkg)
                print(f"{pkg['Name']}  score {points}")
                print(f"  {pkg.get('Description')}")
                print(f"  depends: {', '.join(pkg.get('Depends') or []) or '(none listed)'}")
                for line in why:
                    print(f"  - {line}")
                return 0
        print(f"no AUR package called {args.why}", file=sys.stderr)
        return 1

    listed, judged = known()
    in_repos = repo_packages() if not args.all else set()

    rows = []
    for pkg in packages:
        points, why = score(pkg)
        if points < args.min_score:
            continue
        name = pkg["Name"]
        if not args.all:
            stem = base_name(name)
            if {name, stem} & (listed | judged):
                continue
            if {name, stem} & in_repos:
                continue
        rows.append({
            "pkg": name, "score": points, "why": why,
            "description": pkg.get("Description") or "",
            "url": pkg.get("URL") or "", "votes": pkg.get("NumVotes", 0),
            "popularity": round(pkg.get("Popularity", 0), 3),
            "out_of_date": bool(pkg.get("OutOfDate")),
            "orphaned": not pkg.get("Maintainer"),
            "last_modified": datetime.fromtimestamp(
                pkg.get("LastModified", 0), UTC).strftime("%Y-%m-%d"),
            "depends": pkg.get("Depends") or [],
        })

    # One row per program: prefer the plainest packaging of it.
    best: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: (-r["score"], len(r["pkg"]))):
        best.setdefault(base_name(row["pkg"]), row)
    rows = sorted(best.values(), key=lambda r: (-r["score"], r["pkg"]))

    if args.json:
        json.dump(rows[: args.limit] if args.limit else rows, sys.stdout, indent=2)
        print()
        return 0

    print(f"{'package':34} {'sc':>3} {'votes':>5} {'updated':>10}  description")
    print("-" * 110)
    for row in rows[: args.limit]:
        flag = "!" if (row["out_of_date"] or row["orphaned"]) else " "
        print(f"{row['pkg'][:34]:34} {row['score']:3} {row['votes']:5} "
              f"{row['last_modified']:>10} {flag} {row['description'][:52]}")
    print(f"\n{min(len(rows), args.limit)} of {len(rows)} candidates "
          f"(from {len(packages):,} AUR packages)")
    print("! = orphaned or flagged out of date")
    report_deferred()
    return 0


if __name__ == "__main__":
    sys.exit(main())
