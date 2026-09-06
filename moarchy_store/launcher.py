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

The desktop entry is handed to the shell rather than started here, and that is
the whole difference between Open feeling like a launch and feeling like a dead
button. See _shell_launch below.

A terminal app ships no desktop entry at all, so it gets the third path: the
binary the package owns, run in the terminal moarchy provides for exactly this,
sized for a 360px screen. That launcher is looked up rather than depended on --
on a plain Arch phone without it, Open simply does not appear for those
entries.

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

# moarchy's terminal, sized so a TUI actually renders at 360px -- foot at font
# size 7, which is the ~60 columns btop refuses to draw below.
#
# This read `mobileomarchy-launch-tui` until now, a name the rename left behind
# and that nothing on the image has answered to since. It failed in the worst
# available way: `shutil.which` returned None, so `terminal_command` returned
# "", so `can_open` said no and the button was *hidden*. Open did not break for
# terminal apps, it silently stopped existing for them -- and the hiding is
# deliberate elsewhere, for packages that genuinely own no single binary, so
# nothing about the screen looked wrong.
#
# Absolute rather than a bare name, now that it is being fixed. The directory
# reaches PATH only through /etc/profile.d/zz-moarchy.sh, so a name resolves
# only for a process that inherited a login shell's environment. The store does
# today -- drawer, sway, login shell -- but plugins.environment() exists
# precisely because it cannot count on that, and what it repairs is
# OMARCHY_PATH/bin, not this directory. A path needs no inheritance.
#
# The near-miss to avoid is /usr/bin/omarchy-launch-tui, which does exist and
# is always on PATH. It is upstream's: xdg-terminal-exec at the default font
# size, sized for a desktop. moarchy's own scripts shadow upstream's by PATH
# order where the 19 shared names collide, but this is not one of them -- ours
# is `moarchy-`, theirs is `omarchy-`, and both resolve.
TUI_LAUNCHER = "/usr/lib/moarchy/bin/moarchy-launch-tui"

# The phone shell's IPC client. Present on a mobileomarchy phone, absent on a
# plain Arch one, which is what picks between the two package launch paths.
SHELL_CLI = "omarchy-shell"


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

    if _shell_launch(entry):
        return True, ""
    return _gio_launch(entry)


def _shell_launch(entry: str) -> bool:
    """Hand the entry to the phone shell, so the launch splash covers it.

    Starting the app here works and looks broken. The shell puts the app's own
    icon on the wallpaper from the tap until its window maps -- mobileomarchy's
    docs/windows.md L1-L9 -- and it hangs that off AppLibrary.launch(). An app
    the store starts itself never goes through there, so Open acknowledged
    nothing: you tapped it and the store sat in front of you until the window
    appeared and the workspace switched, which on a PinePhone is seconds. The
    one moment someone least believes their tap registered is the one right
    after an install finished.

    So the launch goes down the drawer's path instead. `drawer launch` is the
    same entry point a tap on the drawer's grid uses; the shell resolves the
    icon, raises the splash and starts the app under app-graphical.slice rather
    than as a child of this process, which is what the Gio call bought.

    The id goes bare. AppLibrary keys its entries without the .desktop suffix,
    and the suffixed form still launches -- it just matches no entry, and the
    splash falls back to a generic outline instead of showing the icon of the
    thing that was just installed.

    Synchronous, because the answer decides whether Gio still has to run and a
    launch that happens twice is worse than one that blocks. The round trip is
    ~0.4s on a PinePhone, failure included: `omarchy-shell` answers "Target not
    found" just as fast when the shell is down or the drawer is disabled.
    """
    env = plugins.environment()
    if not shutil.which(SHELL_CLI, path=env.get("PATH")):
        return False
    try:
        done = subprocess.run(
            [SHELL_CLI, "drawer", "launch", entry.removesuffix(".desktop")],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    # "no-entry" is a success: the shell launched it anyway and raised the
    # splash. Only a non-zero exit means nothing was started.
    return done.returncode == 0


def _gio_launch(entry: str) -> tuple[bool, str]:
    """The fallback, for a machine with no shell to ask.

    Gio rather than a spawned gtk-launch: it is already here, it reads the
    entry's own Exec and startup notification, and the launched app does not
    end up a child of the store.
    """
    from gi.repository import Gio, GLib

    info = Gio.DesktopAppInfo.new(entry)
    if info is None:
        return False, f"{entry} is installed but the desktop database has no record of it"
    try:
        info.launch([], None)
    except GLib.Error as exc:
        return False, f"could not launch {entry}: {exc.message}"
    return True, ""
