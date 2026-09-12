#!/usr/bin/env python3
"""Fill metadata.toml's AppStream-derived fields for apps already catalogued.

    ./scripts/sweep-backfill.py --dry-run     # what would change
    ./scripts/sweep-backfill.py               # write metadata.toml

Four of metadata.toml's fields are facts upstream already publishes -- what the
app calls itself, what it does, when it was last released, where it lives. There
is no reason to type those by hand or to boot a VM for them, and every reason
not to guess them.

It never overwrites. A field already present in metadata.toml is left exactly as
it is, because the fields worth writing by hand are the ones this would
otherwise trample: `description` especially, which upstream writes as desktop
marketing and we rewrite as an opinion about using it with a thumb. Backfill
seeds an entry; it does not maintain one.

`subtitle` is the exception worth stating: it IS upstream's own <summary>,
verbatim, and it is labelled as what the app calls itself. `summary` in
catalogue.toml stays ours.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _collect():
    """sweep-discover.py's reader. Imported by path because the hyphen in its
    name is not a legal module name, and renaming it would make the two scripts
    look unrelated on the command line, which is where people meet them."""
    path = Path(__file__).resolve().parent / "sweep-discover.py"
    spec = importlib.util.spec_from_file_location("sweep_discover", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.collect

# Only these. Everything else in metadata.toml is either measured in the VM or
# is a judgement, and neither is in AppStream.
FILLS = ("subtitle", "description", "homepage", "released", "subcategory")

# freedesktop's categories are only sometimes specific enough to be a
# subcategory. "InstantMessaging" is; "Viewer" and "Utility" are not -- Foliate
# is tagged Office,Viewer, and "Viewers" tells a reader nothing that "Reading"
# did not. So this maps only the unambiguous tags and leaves the rest empty,
# because a blank field asks for judgement while a vague one looks answered.
# The 26 apps sitting in Utilities are exactly the ones freedesktop cannot
# help with; splitting them is the sweep's job, not this script's.
SUBCATEGORY = [
    ("InstantMessaging", "Messaging"), ("IRCClient", "Messaging"),
    ("Telephony", "Calls"), ("Email", "Mail"),
    ("Feed", "Feeds"), ("News", "Feeds"), ("WebBrowser", "Browser"),
    ("RemoteAccess", "Remote access"), ("P2P", "File sharing"),
    ("Music", "Music"), ("Midi", "Music"), ("Recorder", "Recording"),
    ("Photography", "Photos"), ("Scanning", "Scanning"),
    ("TextEditor", "Text"), ("WordProcessor", "Text"), ("Spreadsheet", "Spreadsheets"),
    ("Dictionary", "Reference"), ("Translation", "Translation"),
    ("Calendar", "Calendar"), ("ContactManagement", "Contacts"),
    ("ProjectManagement", "Tasks"), ("Maps", "Maps"),
    ("Security", "Security"), ("Archiving", "Backup"), ("Compression", "Archives"),
    ("FileManager", "Files"), ("Calculator", "Calculator"), ("Clock", "Clock"),
    ("BoardGame", "Board games"), ("LogicGame", "Puzzles"), ("CardGame", "Card games"),
    ("ArcadeGame", "Arcade"), ("Astronomy", "Astronomy"), ("Math", "Maths"),
]


def subcategory_for(cats: list[str]) -> str:
    for tag, label in SUBCATEGORY:
        if tag in cats:
            return label
    return ""


def quote(value: str) -> str:
    """A TOML string. Multi-line prose gets a block, which keeps apostrophes
    and quotes readable in a file people review in diffs.

    The backslash before the closing delimiter is load-bearing. TOML drops the
    newline that follows the opening `\"\"\"` and keeps the one before the
    closing it, so `f'\"\"\"\\n{body}\\n\"\"\"'` round-trips to `body + "\\n"` --
    and this script reads its own output, so every run appended one more.
    Telly Skout's description had accumulated eight, which a wrapping GtkLabel
    renders as eight empty lines between About and Details. A trailing
    backslash swallows that newline, so the block means exactly its text and
    the delimiter still gets a line of its own.
    """
    if len(value) > 78 or "\n" in value:
        body = value.replace('"""', '\\"\\"\\"')
        return f'"""\n{body}\\\n"""'
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def emit(entries: dict[str, dict], header: str) -> str:
    out = [header.rstrip(), ""]
    for ident in sorted(entries):
        fields = entries[ident]
        if not fields:
            continue
        out.append(f"[{ident}]" if _bare(ident) else f'["{ident}"]')
        width = max((len(k) for k in fields), default=0)
        for key, value in fields.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, (int, float)):
                rendered = str(value)
            elif isinstance(value, (list, tuple)):
                rendered = "[" + ", ".join(quote(str(v)) for v in value) + "]"
            else:
                rendered = quote(str(value))
            out.append(f"{key.ljust(width)} = {rendered}")
        out.append("")
    return "\n".join(out) + "\n"


def _bare(ident: str) -> bool:
    return all(c.isalnum() or c in "-_" for c in ident)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with (ROOT / "catalogue.toml").open("rb") as fh:
        cat = tomllib.load(fh)
    apps = {a.get("pkg") or a.get("id"): a for a in cat.get("app", [])}

    meta_path = ROOT / "metadata.toml"
    raw = meta_path.read_text() if meta_path.exists() else "version = 1\n"
    header = raw.split("\nversion = 1", 1)[0] + "\nversion = 1\n"
    existing: dict[str, dict] = {}
    if meta_path.exists():
        with meta_path.open("rb") as fh:
            existing = {k: v for k, v in tomllib.load(fh).items() if isinstance(v, dict)}

    upstream = {r["pkg"]: r for r in _collect()()}

    changed, untouched, missing = 0, 0, []
    merged: dict[str, dict] = {k: dict(v) for k, v in existing.items()}

    for ident, app in apps.items():
        if app.get("source") == "plugin":
            continue  # plugins have no AppStream record; their prose is ours
        row = upstream.get(ident)
        if not row:
            missing.append(ident)
            continue
        entry = merged.setdefault(ident, {})
        candidates = {
            "subtitle": row["summary"],
            "description": row["description"],
            "homepage": row["homepage"],
            "released": row["released"],
            "subcategory": subcategory_for(row["categories"]),
        }
        for key in FILLS:
            value = candidates.get(key, "")
            if value and not entry.get(key):
                entry[key] = value
                changed += 1
            elif entry.get(key):
                untouched += 1
        # Keep the file's field order stable and readable.
        merged[ident] = {k: entry[k] for k in
                         ("subtitle", "description", "subcategory", "features", "homepage",
                          "released", "releases_1y", "cost_pkgs", "cost_mb",
                          "adaptive", "themed", "measured", "shots")
                         if k in entry}

    text = emit(merged, header)
    print(f"{changed} fields filled, {untouched} left as they were, "
          f"{len(merged)} entries", file=sys.stderr)
    if missing:
        print(f"no AppStream record for {len(missing)}: {', '.join(sorted(missing))}",
              file=sys.stderr)
    if args.dry_run:
        sys.stdout.write(text)
    else:
        meta_path.write_text(text)
        print(f"wrote {meta_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
