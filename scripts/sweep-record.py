#!/usr/bin/env python3
"""Write sweep-measure.sh results into metadata.toml.

    ./scripts/sweep-record.py --from /tmp/measured --adaptive fits foliate kalk
    ./scripts/sweep-record.py --from /tmp/measured --dry-run kalk

The last step of a batch, and the one that was done by hand every time until
now. It takes what the harness measured -- install cost, the theme diff, the
screenshots -- and merges it into the entry, leaving everything else alone.

It will not write `adaptive` unless you pass it, and that is the point. The
harness reports "mapped", never "fits": Hyprland tiles, so it forces 360x674
onto an app that cannot cope and a clipped app reports the same geometry as a
perfect one. Whether it *fits* is a judgement someone makes by looking at the
picture, so this script cannot make it, and an entry stays unjudged until a
person passes --adaptive.

It also never touches `tested`. A VM run does not overwrite a hardware claim:
an entry measured on a PinePhone keeps saying so, and gains the fields the
harness produces alongside.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTIVE = ("fits", "tight", "clipped", "no-window", "unknown")


def result(out_dir: Path, pkg: str) -> dict | None:
    path = out_dir / f"{pkg}.json"
    if not path.exists():
        return None
    raw = path.read_text()
    try:
        return json.loads(raw[raw.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return None


def render(pkg: str, d: dict, adaptive: str | None, date: str) -> str | None:
    if d.get("launch") != "ok":
        return None
    shots = [s for s in d.get("shots", []) if (ROOT / "screenshots" / s).exists()]
    lines = [f'cost_pkgs   = {d["cost_pkgs"]}', f'cost_mb     = {d["cost_mb"]}']
    if adaptive:
        lines.append(f'adaptive    = "{adaptive}"')
    themed = d.get("themed")
    if themed and themed != "unknown":
        lines.append(f'themed      = "{themed}"')
    lines.append(f'measured    = "omarchy-mobile-vm {date}"')
    if shots:
        rendered = ", ".join(f'"{s}"' for s in shots)
        lines.append(f"shots       = [{rendered}]")
    return "\n".join(lines) + "\n"


def merge(text: str, pkg: str, block: str) -> str:
    """Replace the fields this script owns, leave the rest of the table alone."""
    owned = ("cost_pkgs", "cost_mb", "adaptive", "themed", "measured", "shots")
    m = re.search(rf'(\[{re.escape(pkg)}\]\n(?:.*\n)*?)(?=\n\[|\Z)', text)
    if not m:
        return text.rstrip("\n") + f"\n\n[{pkg}]\n" + block
    kept = [ln for ln in m.group(1).splitlines()
            if not any(ln.startswith(f"{k} ") or ln.startswith(f"{k}=") for k in owned)]
    return text.replace(m.group(1), "\n".join(kept).rstrip("\n") + "\n" + block + "\n", 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("packages", nargs="+")
    ap.add_argument("--from", dest="out_dir", required=True,
                    help="directory of sweep-measure.sh JSON output")
    ap.add_argument("--adaptive", choices=ADAPTIVE,
                    help="the verdict you reached by looking at the screenshots")
    ap.add_argument("--date", default="2026-09-12")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    path = ROOT / "metadata.toml"
    text = path.read_text()
    written, skipped = [], []

    for pkg in args.packages:
        d = result(out_dir, pkg)
        if d is None:
            skipped.append((pkg, "no result file"))
            continue
        block = render(pkg, d, args.adaptive, args.date)
        if block is None:
            skipped.append((pkg, f"launch={d.get('launch')}"))
            continue
        text = merge(text, pkg, block)
        written.append(pkg)

    if args.dry_run:
        for pkg in written:
            m = re.search(rf'\[{re.escape(pkg)}\]\n(?:.*\n)*?(?=\n\[|\Z)', text)
            print(m.group(0) if m else f"[{pkg}] ?")
    else:
        path.write_text(text)

    print(f"{len(written)} recorded: {' '.join(written)}", file=sys.stderr)
    for pkg, why in skipped:
        print(f"  skipped {pkg}: {why}", file=sys.stderr)
    if not args.adaptive and written:
        print("  adaptive left unset -- look at the shots, then re-run with "
              "--adaptive", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
