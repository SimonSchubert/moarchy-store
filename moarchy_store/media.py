"""Screenshot fetching.

Screenshots live in the GitHub repo rather than the package: 2.7 MB of PNGs in
a package that is otherwise ~40 KB would be a poor trade for something most
people never scroll to. They are fetched once and cached under
~/.cache/moarchy-store/.

Everything here is best-effort. No screenshot must ever be the reason the app
fails to show you a page -- a phone is frequently offline, and the catalogue
entry is useful without a picture.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/SimonSchubert/moarchy-store/main/screenshots"
CACHE = Path.home() / ".cache" / "moarchy-store" / "screenshots"

# A phone screenshot is ~200 KB. Anything far larger is not what we asked for.
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 15


def cached_path(filename: str) -> Path | None:
    path = CACHE / filename
    return path if path.is_file() and path.stat().st_size > 0 else None


def fetch(filename: str, on_ready: Callable[[Path], None]) -> None:
    """Fetch in the background if not already cached. `on_ready` fires on a
    worker thread -- marshal to the main loop before touching a widget."""
    existing = cached_path(filename)
    if existing:
        on_ready(existing)
        return

    def worker() -> None:
        # Guard against a filename escaping the cache directory. These come
        # from a shipped TOML rather than user input, but a path traversal in
        # something that writes to disk is not worth leaving open.
        if "/" in filename or "\\" in filename or filename.startswith("."):
            return

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
                return
            # Write then rename, so an interrupted download never leaves a
            # truncated file that later looks cached.
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(target)
        except (urllib.error.URLError, OSError, ValueError):
            return  # offline, 404, unwritable cache -- all equally fine
        on_ready(target)

    threading.Thread(target=worker, daemon=True).start()
