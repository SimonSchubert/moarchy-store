"""Run install/remove off the UI thread, whichever kind of app it is.

pacman on a 1.15GHz A53 is slow enough that doing this synchronously would
freeze the window for tens of seconds and make the app look crashed. Output is
streamed back line by line so the UI can show progress rather than a spinner
that might mean anything. Cloning a plugin is quicker but not instant, and it
is several commands rather than one, so it gets the same treatment.

The two backends differ in one way that is not cosmetic: a package goes through
pkexec and the privileged helper, and a plugin does not go near either. See
plugins.py for why asking for a password to write to the user's own config
directory would be theatre.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable

from . import plugins
from .catalogue import App

HELPER = "/usr/lib/moarchy-store/moarchy-store-helper"


class InstallerError(Exception):
    pass


def available(app: App) -> bool:
    if app.is_plugin:
        return plugins.available()
    return bool(shutil.which("pkexec")) and bool(shutil.which("pacman"))


def unavailable_reason(app: App) -> str:
    if app.is_plugin:
        return "the omarchy command is missing; cannot manage plugins"
    return "pkexec or pacman is missing; cannot manage packages"


def helper_missing() -> bool:
    """Checked up front so a genuinely absent helper is reported as such,
    rather than being inferred from an exit code that means something else."""
    return not os.access(HELPER, os.X_OK)


def run(
    action: str,
    app: App,
    on_line: Callable[[str], None],
    on_done: Callable[[bool, str], None],
) -> threading.Thread:
    """Start `action` on `app`. Callbacks fire on a worker thread -- marshal to
    the main loop with GLib.idle_add before touching any widget."""
    if action not in ("install", "remove"):
        raise ValueError(f"unknown action {action!r}")

    worker = _plugin_worker if app.is_plugin else _package_worker
    thread = threading.Thread(
        target=worker, args=(action, app, on_line, on_done), daemon=True
    )
    thread.start()
    return thread


def _stream(
    argv: list[str],
    on_line: Callable[[str], None],
    env: dict[str, str] | None = None,
) -> tuple[int, list[str]]:
    """Run one command, relaying its output. Returns (exit code, last lines).

    A code of -1 means it could not be started at all, which is a different
    failure from a command that ran and refused.
    """
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            # Nothing here is interactive, and a child that inherits a terminal
            # can decide to prompt. Closing stdin makes that impossible rather
            # than unlikely.
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        return -1, [f"could not start {argv[0]}: {exc}"]

    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            on_line(line)
            tail.append(line)
            del tail[:-40]
    return proc.wait(), tail


def _package_worker(
    action: str,
    app: App,
    on_line: Callable[[str], None],
    on_done: Callable[[bool, str], None],
) -> None:
    code, tail = _stream(["pkexec", HELPER, action, app.pkg], on_line)

    if code == 0:
        on_done(True, "")
    elif code == -1:
        on_done(False, "\n".join(tail))
    elif code == 126:
        # pkexec(1): 126 means the user dismissed the dialog.
        on_done(False, "Cancelled")
    elif code == 127:
        # pkexec(1): 127 means the authorisation could not be obtained -- in
        # practice, a wrong password. It does NOT mean the helper is missing,
        # which is what this used to say; that message sent people hunting for
        # a file that was there all along.
        on_done(False, "Wrong password, or not authorised")
    else:
        on_done(False, "\n".join(tail[-10:]) or f"pacman exited {code}")


def _plugin_worker(
    action: str,
    app: App,
    on_line: Callable[[str], None],
    on_done: Callable[[bool, str], None],
) -> None:
    env = plugins.environment()

    if action == "install":
        # `--yes` is not a shortcut past upstream's confirmation, it is us
        # taking responsibility for it: the CLI's prompt exists to say that
        # plugins are unsandboxed code in your shell, and the detail page says
        # so before the button is ever pressed.
        #
        # Deliberately NOT `--enable`. Add clones and validates; enable is what
        # loads the QML. Keeping them apart buys the identity check below, and
        # that check is only worth anything if it happens before the code runs.
        before = plugins.installed_ids()
        code, tail = _stream(
            ["omarchy", "plugin", "add", app.repo, "--yes"], on_line, env
        )
        if code != 0:
            on_done(False, "\n".join(tail[-10:]) or f"omarchy plugin add exited {code}")
            return

        # The id comes from the manifest inside the repo, not from the URL, so
        # a repo can declare an id other than the one the catalogue named. That
        # is the one way the catalogue could be talked past, and the plugin is
        # on disk but inert at this point -- enable is what would run it -- so
        # refusing here costs nothing.
        if not plugins.is_installed(app.plugin_id):
            # Whatever did arrive is this operation's mess to clear up. Only
            # directories that appeared during the add are touched, so a plugin
            # the user already had is never caught up in it.
            strays = plugins.installed_ids() - before
            for stray in sorted(strays):
                _stream(["omarchy", "plugin", "remove", stray, "--yes"], on_line, env)
            landed = ", ".join(sorted(strays)) or "nothing"
            on_done(
                False,
                f"Refused: {app.repo} declares {landed}, not the "
                f"{app.plugin_id} the catalogue names. It was never enabled"
                + (", and has been removed." if strays else "."),
            )
            return

        code, tail = _stream(
            ["omarchy", "plugin", "enable", app.plugin_id], on_line, env
        )
        if code != 0:
            on_done(
                False,
                "\n".join(tail[-10:]) or f"omarchy plugin enable exited {code}",
            )
            return

        # Last, because it is the only step whose failure is cosmetic: the
        # plugin already works from a keybinding, it just would not be in the
        # drawer.
        try:
            if plugins.write_desktop_entry(
                app.plugin_id, app.name, app.summary, app.icon
            ):
                on_line(f"Added {app.name} to the app drawer")
        except OSError as exc:
            on_line(f"installed, but could not write a drawer entry: {exc}")

        on_done(True, "")
        return

    # Remove: the plugin first, the launcher after. The other order leaves a
    # working plugin with no way to open it whenever removal fails.
    code, tail = _stream(
        ["omarchy", "plugin", "remove", app.plugin_id, "--yes"], on_line, env
    )
    if code != 0:
        on_done(False, "\n".join(tail[-10:]) or f"omarchy plugin remove exited {code}")
        return

    plugins.remove_desktop_entry(app.plugin_id)
    on_done(True, "")
