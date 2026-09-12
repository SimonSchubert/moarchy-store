#!/usr/bin/env python3
"""Cross-reference LinuxPhoneApps.org with the Arch repos.

    ./scripts/sweep-lpa.py                 # candidates not yet judged here
    ./scripts/sweep-lpa.py --all           # including ones already handled
    ./scripts/sweep-lpa.py --json
    ./scripts/sweep-lpa.py --refresh       # re-download the app database

linuxphoneapps.org is the closest thing to an authority on which Linux
applications actually work on a phone, and it publishes the judgement rather
than only the app list: every entry carries a `mobile_compatibility` rating
where 5 means "fits the screen and works fine with touch input", plus the
distributions it is packaged in.

That makes it a better first filter than either of the other two sweeps.
sweep-discover.py reads what upstream *claims* about itself; sweep-aur.py
*infers* from dependency lists. This reads what somebody concluded after
running the thing.

It is still not evidence for this device. 5 there means it fits some phone, at
some width, on some image -- usually Phosh or Plasma Mobile, neither of which
is what moarchy runs. KleverNotes is rated 5 and clips at 360x674 here. So this
produces a queue for scripts/sweep-measure.sh, not entries.

The join is on AppStream ids: LinuxPhoneApps records an app_id, and the repo
AppStream catalogue maps that id to a package name. Apps that ship no metainfo
cannot be matched this way and are reported separately rather than dropped
silently -- that blind spot is how komikku, marknote and filelight went missing
in the first place.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "moarchy-store-sweep"
LPA_ARCHIVE = ("https://framagit.org/linuxphoneapps/linuxphoneapps.frama.io/-/archive/"
               "main/linuxphoneapps.frama.io-main.tar.gz?path=content/apps")


def lpa_entries(refresh: bool = False) -> list[dict]:
    root = CACHE / "linuxphoneapps"
    if refresh or not root.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        archive = CACHE / "lpa.tar.gz"
        print("fetching the LinuxPhoneApps database ...", file=sys.stderr)
        with urllib.request.urlopen(LPA_ARCHIVE, timeout=120) as response:
            archive.write_bytes(response.read())
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["tar", "xzf", str(archive), "-C", str(root),
                        "--strip-components", "3"], check=True, capture_output=True)
        archive.unlink(missing_ok=True)

    def listed(text: str, key: str) -> list[str]:
        m = re.search(rf"^{key} = \[(.*?)\]", text, re.M | re.S)
        if not m:
            return []
        return [x.strip().strip('"') for x in m.group(1).split(",") if x.strip().strip('"')]

    def scalar(text: str, key: str) -> str:
        m = re.search(rf'^{key} = "(.*?)"', text, re.M)
        return m.group(1) if m else ""

    out = []
    for path in sorted(glob.glob(str(root / "*.md"))):
        text = Path(path).read_text()
        if not text.startswith("+++"):
            continue
        rating = listed(text, "mobile_compatibility")
        out.append({
            "title": scalar(text, "title"),
            "app_id": scalar(text, "app_id"),
            "description": scalar(text, "description"),
            "rating": rating[0] if rating else "",
            "categories": listed(text, "categories"),
            "frameworks": listed(text, "frameworks"),
            "packaged_in": listed(text, "packaged_in"),
            "status": (listed(text, "status") or [""])[0],
            "homepage": scalar(text, "homepage"),
            "repository": scalar(text, "repository"),
        })
    return out


def appstream_index() -> dict[str, str]:
    """AppStream component id -> package name, from the repo catalogue."""
    spec = importlib.util.spec_from_file_location(
        "sweep_discover", Path(__file__).resolve().parent / "sweep-discover.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    index = {}
    for row in module.collect():
        if row["id"]:
            index[row["id"]] = row["pkg"]
            index[row["id"].removesuffix(".desktop")] = row["pkg"]
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--min-rating", default="4")
    args = ap.parse_args()

    with (ROOT / "catalogue.toml").open("rb") as fh:
        cat = tomllib.load(fh)
    known = {a.get("pkg") or a.get("id") for a in cat.get("app", [])}
    verdicts = ROOT / "sweep" / "verdicts.toml"
    if verdicts.exists():
        with verdicts.open("rb") as fh:
            known |= set(tomllib.load(fh))

    index = appstream_index()
    matched, unmatched = [], []
    for entry in lpa_entries(args.refresh):
        if entry["rating"] not in ("5", "4") or args.min_rating == "5" and entry["rating"] != "5":
            continue
        if "arch" not in entry["packaged_in"]:
            continue
        if entry["status"] not in ("released", "pre-release"):
            continue
        pkg = index.get(entry["app_id"])
        if pkg:
            entry["pkg"] = pkg
            if args.all or pkg not in known:
                matched.append(entry)
        else:
            # No AppStream metainfo, so the id cannot be joined. Worth seeing:
            # this is exactly the gap that hid komikku, marknote and filelight.
            unmatched.append(entry)

    matched.sort(key=lambda r: (r["rating"] != "5", r["pkg"]))
    if args.json:
        json.dump({"matched": matched, "unmatched": unmatched}, sys.stdout, indent=2)
        print()
        return 0

    print(f"{'package':26} {'mc':>2} {'frameworks':22} categories")
    print("-" * 100)
    for r in matched:
        print(f"{r['pkg'][:26]:26} {r['rating']:>2} {','.join(r['frameworks'])[:22]:22} "
              f"{','.join(r['categories'])[:40]}")
    print(f"\n{len(matched)} not yet judged here")
    print(f"{len(unmatched)} rated apps in Arch could not be matched to a package "
          f"(no AppStream metainfo) -- run with --json to see them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
