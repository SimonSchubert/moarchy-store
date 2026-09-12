"""Screenshot fetching, and the small copies the grid reads.

Screenshots live in the GitHub repo rather than the package: 2.7 MB of PNGs in
a package that is otherwise ~40 KB would be a poor trade for something most
people never scroll to. They are fetched once and cached under
~/.cache/moarchy-store/.

Two things here exist only because the catalogue is now a grid of pictures
rather than a list of icons:

* A thumbnail, written once beside the original. A shot is 720x1440, and a
  category page asks for twenty-three of them at once -- decoded at full size
  that is 90 MB of texture and several seconds of an A53 on a page whose whole
  job is to scroll. The small copy is made on a worker, kept on disk, and read
  back on every later launch.
* A bounded pool. One thread per fetch was fine for the two shots a detail
  page wants and is not fine for twenty-three; three workers keep a slow
  network from becoming twenty-three stacks.

Everything here is best-effort. No screenshot must ever be the reason the app
fails to show you a page -- a phone is frequently offline, and the catalogue
entry is useful without a picture.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf  # noqa: E402

BASE_URL = "https://raw.githubusercontent.com/SimonSchubert/moarchy-store/main/screenshots"
CACHE = Path.home() / ".cache" / "moarchy-store" / "screenshots"
THUMBS = CACHE / "thumbs"

# The screenshots beside the source tree, for a checkout rather than a package.
# Same rule as catalogue.shipped_path: the installed app has no such directory
# and pays one stat for the check, while a checkout sees the shots the sweep
# has taken but not yet pushed -- which are exactly the ones being looked at
# when someone is running from a checkout at all.
LOCAL = Path(__file__).resolve().parent.parent / "screenshots"

# A phone screenshot is ~200 KB. Anything far larger is not what we asked for.
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 15

# Device pixels, not logical. A grid cell is ~148 logical pixels wide and this
# screen is 2x, so 300 is the first round number that is not upscaled.
THUMB_WIDTH = 300

# Three, not one per call. See the module docstring.
_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="moarchy-media")


def _safe(filename: str) -> bool:
    """Whether a name may be joined onto the cache directory.

    These come from a shipped TOML rather than user input, but a path traversal
    in something that writes to disk is not worth leaving open.
    """
    return bool(filename) and not (
        "/" in filename or "\\" in filename or filename.startswith(".")
    )


def cached_path(filename: str) -> Path | None:
    if not _safe(filename):
        return None
    path = CACHE / filename
    if path.is_file() and path.stat().st_size > 0:
        return path
    local = LOCAL / filename
    return local if local.is_file() and local.stat().st_size > 0 else None


def _download(filename: str) -> Path | None:
    """Blocking. Returns the cached file, fetching it if it is not there yet."""
    existing = cached_path(filename)
    if existing:
        return existing

    target = CACHE / filename
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            f"{BASE_URL}/{filename}",
            headers={"User-Agent": "moarchy-store"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            data = response.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            return None
        # Write then rename, so an interrupted download never leaves a
        # truncated file that later looks cached.
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
    except (urllib.error.URLError, OSError, ValueError):
        return None  # offline, 404, unwritable cache -- all equally fine
    return target


def fetch(filename: str, on_ready: Callable[[Path], None]) -> None:
    """Fetch in the background if not already cached. `on_ready` fires on a
    worker thread -- marshal to the main loop before touching a widget."""
    existing = cached_path(filename)
    if existing:
        on_ready(existing)
        return
    if not _safe(filename):
        return

    def worker() -> None:
        path = _download(filename)
        if path:
            on_ready(path)

    _POOL.submit(worker)


def _make_thumbnail(source: Path, target: Path) -> Path | None:
    """Scale a screenshot down and write it beside the cache. Blocking."""
    try:
        THUMBS.mkdir(parents=True, exist_ok=True)
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(source), THUMB_WIDTH, -1, True
        )
        tmp = target.with_suffix(target.suffix + ".part")
        pixbuf.savev(str(tmp), "png", [], [])
        tmp.replace(target)
    except Exception:
        # Deliberately broad. GdkPixbuf raises GLib.Error, which is not an
        # OSError and does not share an ancestor with one, and a corrupt PNG,
        # a full disk and an unwritable cache all have the same answer here:
        # show the full-size file rather than no file.
        return None
    return target


def thumbnail(filename: str, on_ready: Callable[[Path], None]) -> None:
    """A small copy of a screenshot, made once and kept.

    `on_ready` fires with the thumbnail, or with the full-size shot when one
    cannot be made -- so a caller never has to have a second path for that.
    It fires on a worker thread unless the thumbnail was already on disk.
    """
    if not _safe(filename):
        return

    existing = THUMBS / filename
    if existing.is_file() and existing.stat().st_size > 0:
        on_ready(existing)
        return

    def worker() -> None:
        source = _download(filename)
        if not source:
            return
        on_ready(_make_thumbnail(source, THUMBS / filename) or source)

    _POOL.submit(worker)
