"""Open an app the store has installed.

A store that can install something and then cannot open it sends you back to
the drawer to find it, which is a strange thing for the app that just put it
there. The two kinds open differently, and neither is a guess:

  packages  through their own desktop entry, found by asking pacman which files
            the package owns. Deriving the id from the package name would be
            wrong about half the catalogue -- `foliate` ships
            com.github.johnfactotum.Foliate.desktop.
  plugins   by asking the shell to summon them, which is the same thing the
            drawer entry the store wrote does, and the only thing that works:
            a plugin is not a process to spawn.

A terminal app ships no desktop entry at all, so it gets the third path: the
binary the package owns, run in the fullscreen terminal mobileomarchy provides
for exactly this. That launcher is looked up rather than depended on -- on a
plain Arch phone without it, Open simply does not appear for those entries.

Not everything installed can be opened, and the caller is expected to ask
first. A plugin of kind "service" has nothing to summon, and a package may own
several binaries with no way to tell which one is the app.
"""

from __future__ import annotations

import shutil
import subprocess

from . import plugins
from .catalogue import App

DESKTOP_DIRS = ("/usr/share/applications/", "/usr/local/share/applications/")
BIN_DIRS = ("/usr/bin/", "/usr/local/bin/")

# mobileomarchy's fullscreen terminal, sized so a TUI actually renders at 360px.
TUI_LAUNCHER = "mobileomarchy-launch-tui"


def _pacman_files(pkg: str) -> list[str]:
    if not shutil.which("pacman"):
        return []
    try:
        out = subprocess.run(
            ["pacman", "-Qlq", pkg],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return out.splitlines()


def desktop_id(app: App) -> str:
    """The desktop entry a package installed, or "" if it shipped none.

    Where a package ships several -- a main window plus, say, a separate
    "new document" action -- the one whose name matches the catalogue's icon is
    the app itself, because that icon was read from the app's own entry in the
    first place.
    """
    if app.is_plugin or not app.installed or not app.pkg:
        return ""

    ids = [
        path.rsplit("/", 1)[-1]
        for path in _pacman_files(app.pkg)
        if path.endswith(".desktop") and path.startswith(DESKTOP_DIRS)
    ]
    if not ids:
        return ""
    if app.icon:
        for candidate in ids:
            if candidate[: -len(".desktop")] == app.icon:
                return candidate
    return sorted(ids, key=len)[0]


def terminal_command(app: App) -> str:
    """The binary to run in a terminal, for a package that ships no entry.

    A package owning several binaries is ambiguous -- `bottom` ships `btm`, but
    something shipping five tools has no single "the app". Match the package
    name, else take the only one, else give up rather than guess wrong.
    """
    if app.is_plugin or not app.installed or not app.pkg:
        return ""
    if not shutil.which(TUI_LAUNCHER):
        return ""

    binaries = [
        path.rsplit("/", 1)[-1]
        for path in _pacman_files(app.pkg)
        if path.startswith(BIN_DIRS) and path.rsplit("/", 1)[-1]
    ]
    if app.pkg in binaries:
        return app.pkg
    return binaries[0] if len(binaries) == 1 else ""


def can_open(app: App) -> bool:
    if not app.installed:
        return False
    if app.is_plugin:
        kinds = {k for k in plugins.manifest(app.plugin_id).get("kinds", []) if isinstance(k, str)}
        # A service has no surface to summon; offering Open would do nothing.
        return bool(kinds & plugins.SUMMONABLE) and app.enabled
    return bool(desktop_id(app)) or bool(terminal_command(app))


def open_app(app: App) -> tuple[bool, str]:
    """Launch it. Returns (ok, message-if-not)."""
    if app.is_plugin:
        try:
            subprocess.Popen(
                ["omarchy-shell", "shell", "toggle", app.plugin_id],
                env=plugins.environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return False, f"could not ask the shell to open it: {exc}"
        return True, ""

    entry = desktop_id(app)
    if not entry:
        command = terminal_command(app)
        if not command:
            return False, "this package ships no desktop entry to open"
        try:
            subprocess.Popen(
                [TUI_LAUNCHER, command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return False, f"could not open a terminal: {exc}"
        return True, ""

    # Gio rather than a spawned gtk-launch: it is already here, it reads the
    # entry's own Exec and startup notification, and the launched app does not
    # end up a child of the store.
    from gi.repository import Gio, GLib

    info = Gio.DesktopAppInfo.new(entry)
    if info is None:
        return False, f"{entry} is installed but the desktop database has no record of it"
    try:
        info.launch([], None)
    except GLib.Error as exc:
        return False, f"could not launch {entry}: {exc.message}"
    return True, ""
