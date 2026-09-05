"""Omarchy shell plugins, treated as apps.

A plugin is not a package. It is a git repo of QML that `omarchy-shell` loads
into its own long-lived process, so the differences that matter are:

  * **No root, and therefore no polkit gate.** pacman needs privilege, so the
    store's other half authenticates. A plugin installs into the calling user's
    own `~/.config/omarchy/plugins/`, which they can already write. Asking for a
    password here would be theatre.
  * **The trust story is different, not weaker-or-stronger.** A repo package is
    signed by a distribution and sandboxed by nothing; a plugin is unsigned and
    sandboxed by nothing, and runs *inside* the shell rather than beside it. The
    catalogue is what stands in for review, so the git URL comes from the
    catalogue and never from anything the user typed.
  * **Installing is not enabling.** `omarchy plugin add` clones and validates;
    `omarchy plugin enable` is what actually loads the code. Splitting those two
    is what lets us check the plugin is the one the catalogue named *before* any
    of its QML runs -- see installer._plugin_worker.

The other half of "first class" is being launchable. The drawer lists desktop
entries, not plugins, so a plugin with no `.desktop` can only be reached from a
keybinding -- which is no way to open an app on a device with no keyboard. So
the store writes an entry on install and removes it again on uninstall.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

# Where `omarchy plugin add` puts things. Hardcoded upstream too, in every
# omarchy-plugin-* script, so following it is not a guess.
PLUGIN_DIR = Path.home() / ".config" / "omarchy" / "plugins"
SHELL_JSON = Path.home() / ".config" / "omarchy" / "shell.json"
APPLICATIONS = Path.home() / ".local" / "share" / "applications"

DEFAULT_OMARCHY_PATH = Path.home() / ".local" / "share" / "omarchy"

# Same shape omarchy-plugin-remove enforces, and for the same reason: the id
# becomes a path segment and a shell-visible argument.
PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Kinds that put something on the screen. A pure "service" has nothing to
# summon, so it gets no launcher -- an icon that opens nothing is worse than no
# icon.
SUMMONABLE = {"overlay", "panel", "menu"}

# Ownership marker on the entries we write. mobileomarchy sweeps entries by its
# own X-MobileOmarchy-Plugin marker, so a separate key keeps the two from
# tidying up after each other.
MARKER = "X-MoarchyStore-Plugin"


def valid_id(plugin_id: str) -> bool:
    return bool(PLUGIN_ID.match(plugin_id)) and ".." not in plugin_id


def valid_repo(url: str) -> bool:
    """Only plain https git URLs.

    The catalogue is the only source of these, so this is belt-and-braces
    rather than the main defence -- but a URL that starts with a dash becomes a
    git *option*, and one with whitespace becomes two arguments. Upstream's
    omarchy-git-url-check refuses those too; refusing them here as well means a
    malformed catalogue entry fails before it reaches git.
    """
    return (
        url.startswith("https://")
        and not url.startswith("https://-")
        and len(url.split()) == 1
        and "\n" not in url
    )


def omarchy_path() -> Path:
    """Where the vendored Omarchy tree lives.

    omarchy-plugin-catalog and omarchy-shell both read OMARCHY_PATH, and a
    plugin action with it unset half-works in confusing ways. The store may be
    launched from the drawer without a login shell's environment, so resolve it
    rather than assuming it was inherited.
    """
    env = os.environ.get("OMARCHY_PATH")
    if env:
        return Path(env)
    return DEFAULT_OMARCHY_PATH


def environment() -> dict[str, str]:
    """The environment plugin commands need, repaired if the session did not
    provide it."""
    env = dict(os.environ)
    path = omarchy_path()
    env["OMARCHY_PATH"] = str(path)

    bin_dir = str(path / "bin")
    parts = env.get("PATH", "").split(os.pathsep)
    if bin_dir not in parts:
        env["PATH"] = os.pathsep.join([*parts, bin_dir]) if parts else bin_dir
    return env


def available() -> bool:
    """True when the `omarchy` CLI can be found -- with the repaired PATH, not
    merely the inherited one."""
    return bool(shutil.which("omarchy", path=environment().get("PATH")))


def manifest(plugin_id: str) -> dict:
    if not valid_id(plugin_id):
        return {}
    try:
        with (PLUGIN_DIR / plugin_id / "manifest.json").open("rb") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_installed(plugin_id: str) -> bool:
    return valid_id(plugin_id) and (PLUGIN_DIR / plugin_id).is_dir()


def installed_ids() -> set[str]:
    """Every plugin directory on disk, ours or not.

    Snapshotted around an install so that a clone which turns out to declare
    the wrong id can be identified precisely, rather than by guessing at a
    name.
    """
    try:
        return {
            d.name
            for d in PLUGIN_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".") and valid_id(d.name)
        }
    except OSError:
        return set()


def enabled_ids() -> set[str]:
    """Ids the shell is actually loading.

    Two keys, because the shell treats them differently: `plugins[]` holds
    `{"id": ...}` objects for everything ordinary, while a plugin of kind "bar"
    is enabled only by being named in `bar.id` and never appears in `plugins[]`.
    """
    try:
        with SHELL_JSON.open("rb") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()

    ids = {
        entry["id"]
        for entry in data.get("plugins", [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }

    bar = data.get("bar")
    if isinstance(bar, dict) and isinstance(bar.get("id"), str):
        ids.add(bar["id"])
    return ids


def desktop_entry(plugin_id: str) -> Path:
    return APPLICATIONS / f"moarchy-store-{plugin_id}.desktop"


def _escape(value: str) -> str:
    """Desktop-entry value escaping, per the spec's Value Types section."""
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def write_desktop_entry(plugin_id: str, name: str, summary: str, icon: str) -> Path | None:
    """Give the plugin a place in the app drawer.

    Returns the path written, or None when the plugin has nothing to summon.
    """
    if not valid_id(plugin_id):
        return None

    kinds = {k for k in manifest(plugin_id).get("kinds", []) if isinstance(k, str)}
    if not kinds & SUMMONABLE:
        return None

    path = desktop_entry(plugin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_escape(name)}\n"
        f"Comment={_escape(summary)}\n"
        # A plugin is not a process to spawn, it is a screen the shell already
        # holds; asking the shell to summon it is what makes it behave like any
        # other app. TryExec keeps the entry hidden if the shell is not here.
        f"Exec=omarchy-shell shell toggle {plugin_id}\n"
        "TryExec=omarchy-shell\n"
        f"Icon={_escape(icon or 'application-x-executable-symbolic')}\n"
        "Terminal=false\n"
        # This Exec talks to an already-running shell and never produces a
        # toplevel of its own, so a launcher that waits for one sits on
        # "Launching..." for its full timeout before giving up.
        "StartupNotify=false\n"
        f"{MARKER}={plugin_id}\n",
        encoding="utf-8",
    )
    return path


def remove_desktop_entry(plugin_id: str) -> None:
    """Remove our entry, and only ours.

    The marker check matters: the plugin may ship its own `.desktop` that
    mobileomarchy moved into the same directory, and deleting that would be
    reaching into another package's files.
    """
    if not valid_id(plugin_id):
        return
    path = desktop_entry(plugin_id)
    try:
        if MARKER in path.read_text(encoding="utf-8"):
            path.unlink()
    except OSError:
        pass
