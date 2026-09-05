"""Run install/remove through pkexec, off the UI thread.

pacman on a 1.15GHz A53 is slow enough that doing this synchronously would
freeze the window for tens of seconds and make the app look crashed. Output is
streamed back line by line so the UI can show progress rather than a spinner
that might mean anything.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable

HELPER = "/usr/lib/moarchy-store/moarchy-store-helper"


class InstallerError(Exception):
    pass


def available() -> bool:
    return bool(shutil.which("pkexec")) and bool(shutil.which("pacman"))


def run(
    action: str,
    pkg: str,
    on_line: Callable[[str], None],
    on_done: Callable[[bool, str], None],
) -> threading.Thread:
    """Start `action` on `pkg`. Callbacks fire on a worker thread -- marshal to
    the main loop with GLib.idle_add before touching any widget."""
    if action not in ("install", "remove"):
        raise ValueError(f"unknown action {action!r}")

    def worker() -> None:
        try:
            proc = subprocess.Popen(
                ["pkexec", HELPER, action, pkg],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            on_done(False, f"could not start pkexec: {exc}")
            return

        tail: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                on_line(line)
                tail.append(line)
                del tail[:-40]

        code = proc.wait()
        if code == 0:
            on_done(True, "")
        elif code == 126:
            # pkexec's own code for "dismissed or not authorised". Worth
            # distinguishing: it is the difference between "you cancelled" and
            # "the package failed to install".
            on_done(False, "Authentication cancelled or refused")
        elif code == 127:
            on_done(False, f"Helper not found at {HELPER}")
        else:
            on_done(False, "\n".join(tail[-10:]) or f"pacman exited {code}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread
