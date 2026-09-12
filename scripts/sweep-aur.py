#!/usr/bin/env python3
"""Survey the AUR for apps that might work on a phone.

    ./scripts/sweep-aur.py                  # ranked candidates, as a table
    ./scripts/sweep-aur.py --json
    ./scripts/sweep-aur.py --limit 200
    ./scripts/sweep-aur.py --refresh        # re-download the AUR metadata
    ./scripts/sweep-aur.py --why <pkg>      # why one package scored what it did
    ./scripts/sweep-aur.py --limit 0        # all of them, not the first 80

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
adaptive widget sets, Kirigami is Plasma Mobile's, MauiKit is Nitrux's, and a
package that links one of them was written by somebody who had narrow screens
in mind. After that come the phone projects by name -- a package that says
PinePhone, Phosh or postmarketOS is telling you exactly what it is for.

**The R column is the reason to run this.** A candidate that is also in the
aarch64 repos installs through the helper, and `sweep-discover.py` cannot see
it if it ships no AppStream metainfo. That is where Komikku, Marknote and
Filelight came from, and it is the one thing this script produces that can
become an entry. Everything else is a survey.

A second pass over 119,292 packages found that vein close to worked out: 27
never-judged repo packages carry a mobile signal and 26 are libraries, daemons,
Plasma components or flashing tools. The one app is satty. What the second pass
did find was five holes in this filter, each now fixed and commented where it
sits -- the `--limit` that hid 90% of its own output, AUR liveness flags
sinking repo packages, `MakeDepends` never read, MauiKit missing from the
table, and a `DESKTOP_WORDS` regex that scored every terminal emulator -40.
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
import syncdb  # noqa: E402

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
    # MauiKit is the third one, and it was missing: Nitrux's convergent set,
    # built on Kirigami, and the reason maui-station and maui-pix were only
    # ever found by their descriptions.
    "mauikit": 45, "mauikit-filebrowsing": 45, "mauikit-texteditor": 45,
    "kirigami-app-components": 45,
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
    # The projects the first pass could not say. Worth ~15 rows between them,
    # which is honest about how small these communities are in the AUR.
    "linux phone": 50, "mobile linux": 50, "linux mobile": 50,
    "droidian": 45, "pinetab": 45, "nemomobile": 45, "nemo mobile": 45,
    "sxmo": 50, "lomiri": 40, "ubports": 40,
}
# Weaker, and easy to say about anything, so they are worth much less.
MOBILE_TERMS = {
    "convergent": 35, "convergence": 30, "adaptive": 25, "mobile-friendly": 35,
    "for mobile": 30, "mobile devices": 25, "touch-friendly": 30,
    "touchscreen": 25, "small screens": 35, "phone": 20, "handheld": 20,
    "one-handed": 30, "narrow screen": 35, "small screen": 35,
}
# Deliberately absent, having been measured: "thumb" matches 141 packages and
# every one of them is a thumbnailer, and "tablet" matches 138, mostly drawing
# tablets and their DKMS modules. A term that mostly fires on the wrong thing
# costs more reviewer time than it saves.

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
    r"package manager|aur helper|game launcher|steam|openxr|monado|vr |racing|game emulator|console emulator|gamepad|joystick|rad tool|screencast|miracast|hamachi|vpn gateway|pipewire volume|noise reduction|soundboard|sound pad|audio router|surround|libalpm|patched to bring)\b", re.I)

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


def appstream_packages() -> set[str]:
    """Everything `sweep-discover.py` can already see, so an AUR package that
    duplicates one is dropped: the repo version installs through the helper,
    this one cannot, and the other sweep is already offering it."""
    try:
        spec = ROOT / "scripts" / "sweep-discover.py"
        import importlib.util
        s = importlib.util.spec_from_file_location("sweep_discover", spec)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        return {r["pkg"] for r in m.collect()}
    except Exception:
        return set()


def repo_packages() -> set[str]:
    """Everything installable on aarch64, which is a different question.

    These two sets were one set, and conflating them cost the first run its
    main finding. A name in the repos but not in AppStream is not a duplicate
    to drop -- it is the single most valuable row this script produces, because
    it installs through the helper and the other sweep is structurally blind to
    it. Komikku, Marknote and Filelight were all of this shape.
    """
    try:
        return syncdb.names()
    except Exception as exc:
        print(f"no sync database ({exc}); "
              f"repo cross-reference is off", file=sys.stderr)
        return set()


def base_name(name: str) -> str:
    """`foo-git`, `foo-bin` and `foo` are one program with three packagings."""
    for suffix in ("-git", "-bin", "-beta", "-stable", "-appimage", "-nightly"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def score(pkg: dict, in_repos: frozenset[str] = frozenset()) -> tuple[int, list[str]]:
    name = pkg["Name"]
    desc = (pkg.get("Description") or "").lower()
    keywords = " ".join(pkg.get("Keywords") or []).lower()
    url = (pkg.get("URL") or "").lower()
    deps = {re.split(r"[<>=]", d)[0] for d in (pkg.get("Depends") or [])}
    # Read at half weight, because they are a weaker statement: a package can
    # build against libadwaita and ship a CLI. 46 packages name an adaptive
    # toolkit here and nowhere else, and the first pass could not see any.
    build = {re.split(r"[<>=]", d.split(":")[0])[0]
             for d in (pkg.get("MakeDepends") or []) + (pkg.get("OptDepends") or [])}
    haystack = f"{desc} {keywords} {url}"

    points, why = 0, []

    best_dep = max((ADAPTIVE_DEPS.get(d, 0) for d in deps), default=0)
    if best_dep:
        hit = next(d for d in deps if ADAPTIVE_DEPS.get(d, 0) == best_dep)
        points += best_dep
        why.append(f"depends on {hit} (+{best_dep})")
    elif best_build := max((ADAPTIVE_DEPS.get(d, 0) for d in build), default=0):
        hit = next(d for d in build if ADAPTIVE_DEPS.get(d, 0) == best_build)
        points += best_build // 2
        why.append(f"builds against {hit} (+{best_build // 2})")
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

    # An orphaned, out-of-date AUR submission says nothing whatever about the
    # repo package of the same name, which somebody else maintains. 162
    # candidates were sunk by orphaning and 85 by the out-of-date flag, and the
    # ones that matter here are exactly the ones the repos also carry.
    packaged = base_name(name) in in_repos or name in in_repos
    if packaged:
        why.append("in the aarch64 repos (AUR liveness ignored)")

    for condition, weight, reason in (
        (pkg.get("OutOfDate") and not packaged, -25, "flagged out of date"),
        (not pkg.get("Maintainer") and not packaged, -20, "orphaned"),
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
        in_repos = frozenset(repo_packages())
        for pkg in packages:
            if pkg["Name"] == args.why:
                points, why = score(pkg, in_repos)
                print(f"{pkg['Name']}  score {points}")
                print(f"  {pkg.get('Description')}")
                print(f"  depends: {', '.join(pkg.get('Depends') or []) or '(none listed)'}")
                for line in why:
                    print(f"  - {line}")
                return 0
        print(f"no AUR package called {args.why}", file=sys.stderr)
        return 1

    listed, judged = known()
    in_repos = frozenset(repo_packages())
    visible = appstream_packages() if not args.all else set()

    rows = []
    for pkg in packages:
        points, why = score(pkg, in_repos)
        if points < args.min_score:
            continue
        name = pkg["Name"]
        stem = base_name(name)
        if not args.all:
            if {name, stem} & (listed | judged):
                continue
            # Dropped because the other sweep already offers it, NOT because it
            # is in the repos -- being in the repos is the interesting case.
            if {name, stem} & visible:
                continue
        rows.append({
            "pkg": name, "score": points, "why": why,
            "in_repos": bool({name, stem} & in_repos),
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

    shown = rows[: args.limit] if args.limit else rows
    print(f"{'package':34} {'sc':>3} {'votes':>5} {'updated':>10}  description")
    print("-" * 110)
    for row in shown:
        flag = ("R" if row["in_repos"] else
                "!" if (row["out_of_date"] or row["orphaned"]) else " ")
        print(f"{row['pkg'][:34]:34} {row['score']:3} {row['votes']:5} "
              f"{row['last_modified']:>10} {flag} {row['description'][:52]}")
    print(f"\n{len(shown)} of {len(rows)} candidates "
          f"(from {len(packages):,} AUR packages)")
    if len(rows) > len(shown):
        # The quiet failure of the first run: 980 rows cleared the bar, 80 were
        # printed, and nothing said so. A sweep that hides 90% of its own
        # output reads exactly like a sweep that found nothing.
        print(f"{len(rows) - len(shown)} more not shown -- --limit 0 for all")
    print("! = orphaned or flagged out of date")

    # The rows worth the reviewer's time, and the reason to run this at all.
    packaged = [r for r in rows if r["in_repos"]]
    if packaged:
        print(f"\nR = in the aarch64 repos and not in AppStream, so it installs "
              f"through the helper\n    and sweep-discover.py cannot see it "
              f"({len(packaged)} of them, the listable half):")
        for row in packaged[:20]:
            # The repo name, not the AUR one: `satty-git` is what the AUR calls
            # it and `satty` is what goes in the queue and the allowlist.
            print(f"  {base_name(row['pkg'])[:30]:30} {row['description'][:60]}")
    report_deferred()
    return 0


if __name__ == "__main__":
    sys.exit(main())
