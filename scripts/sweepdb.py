"""What the three sweeps agree about: what to skip, and how old the data is.

Both questions only matter when the sweep is run more than once, which is the
normal case and was the one the scripts got wrong.

**Skipping.** A verdict is not a single thing. `listed` and `rejected` are
answers -- offering them again wastes the reviewer's time on a decision already
made. `deferred` is not an answer: it means "worth another look when something
changes", and the whole point of writing one down is to come back to it. The
first version of these scripts skipped all three, so 36 apps -- Merkuro for
Mail, goguma for IRC, pika-backup for the D-Bus workaround serial 7 already
found -- were invisible to every future run.

**Freshness.** The catalogues are cached under ~/.cache and were only ever
fetched when missing. A sweep run a month later therefore re-read a month-old
snapshot of the repos and found nothing new, which looks exactly like there
being nothing new. Age is now checked and reported, and a stale cache refreshes
itself.
"""

from __future__ import annotations

import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Past this, a cached catalogue is re-fetched rather than trusted. A week is
# roughly how long it takes the repos to move enough to matter.
STALE_DAYS = 7

# Past this, a deferred verdict is worth re-asking: the app has had a release
# or two, and the reason it was deferred may not hold any more.
DEFERRED_DAYS = 90


def verdicts() -> dict[str, dict]:
    path = ROOT / "sweep" / "verdicts.toml"
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return {k: v for k, v in tomllib.load(fh).items() if isinstance(v, dict)}


def catalogued() -> set[str]:
    with (ROOT / "catalogue.toml").open("rb") as fh:
        cat = tomllib.load(fh)
    return {a.get("pkg") or a.get("id") for a in cat.get("app", []) if a.get("pkg") or a.get("id")}


def skip() -> set[str]:
    """Everything a fresh sweep should not offer again.

    Deliberately excludes `deferred`; see the module docstring.
    """
    decided = {k for k, v in verdicts().items()
               if v.get("outcome") in ("listed", "rejected")}
    return catalogued() | decided


def deferred_due(days: int = DEFERRED_DAYS) -> list[tuple[str, str, str]]:
    """Deferred verdicts old enough to re-ask, newest first.

    Returns (ident, date, reason). A deferred entry with no date is due: it
    predates the field, so nobody knows when it was last considered.
    """
    import datetime
    today = datetime.date.today()
    out = []
    for ident, v in verdicts().items():
        if v.get("outcome") != "deferred":
            continue
        when = v.get("date")
        if isinstance(when, datetime.date):
            if (today - when).days < days:
                continue
            stamp = when.isoformat()
        else:
            stamp = str(when or "undated")
        out.append((ident, stamp, " ".join(str(v.get("reason", "")).split())))
    return sorted(out, key=lambda r: r[1], reverse=True)


def needs_measure() -> list[tuple[str, str, str]]:
    """Catalogued apps that have never been through sweep-measure.sh.

    The test is the absence of `measured`, which only that script writes. An
    entry can carry `tested` and a screenshot and still be here: the first 16
    screenshots were taken on a PinePhone by hand, before there was a harness,
    so those entries have a picture and a device string and none of the fields
    the harness produces -- no themed, no adaptive, no install cost. They are
    therefore invisible to everything the store learned to show.

    Returns (ident, tested, screenshot), worst first: no picture at all before
    an old picture, since an entry with neither is the emptier page.
    """
    import tomllib
    with (ROOT / "catalogue.toml").open("rb") as fh:
        cat = tomllib.load(fh)
    meta_path = ROOT / "metadata.toml"
    meta = {}
    if meta_path.exists():
        with meta_path.open("rb") as fh:
            meta = {k: v for k, v in tomllib.load(fh).items() if isinstance(v, dict)}
    out = []
    for a in cat.get("app", []):
        ident = a.get("pkg") or a.get("id")
        if not ident or meta.get(ident, {}).get("measured"):
            continue
        out.append((ident, a.get("tested", ""), a.get("screenshot", "")))
    return sorted(out, key=lambda r: (bool(r[2]), r[0]))


def legacy_shots() -> list[str]:
    """Screenshots named before the dark/light pair existed.

    `<pkg>.png` rather than `<pkg>-dark.png`. Worth knowing separately from
    needs_measure(), because one of them -- a PinePhone capture -- is better
    evidence than anything the VM can produce, and should not simply be
    replaced.
    """
    import tomllib
    with (ROOT / "catalogue.toml").open("rb") as fh:
        cat = tomllib.load(fh)
    return sorted(a["screenshot"] for a in cat.get("app", [])
                  if a.get("screenshot")
                  and "-dark" not in a["screenshot"]
                  and "-light" not in a["screenshot"])


def cache_age_days(path: Path) -> float | None:
    """How old a cached file or directory is, or None if it is not there."""
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 86400


def is_stale(path: Path, days: int = STALE_DAYS) -> bool:
    age = cache_age_days(path)
    return age is None or age > days


def note_age(path: Path, what: str) -> None:
    """Say how old the data is, on stderr, every run.

    A sweep that silently reads a month-old snapshot and reports no new
    candidates is indistinguishable from one that read today's and found none.
    """
    import sys
    age = cache_age_days(path)
    if age is None:
        return
    if age > STALE_DAYS:
        print(f"{what} cache is {age:.0f} days old -- refreshing", file=sys.stderr)
    else:
        print(f"{what} cache is {age:.1f} days old", file=sys.stderr)
