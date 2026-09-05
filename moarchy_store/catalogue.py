"""Load the curated catalogue and enrich it from the system.

The split matters: the TOML carries the *judgement* (does this app work on a
phone), the system carries the *facts* (version, size, installed or not).
Neither duplicates the other, so the catalogue never goes stale about versions
and pacman is never asked to have an opinion.

An app comes from one of two sources, and the catalogue says which:

  source = "pacman"   an Arch package, installed with privilege through the
                      helper. The default, so existing entries need no field.
  source = "plugin"   an Omarchy shell plugin, installed as the user with
                      `omarchy plugin add`. See moarchy_store/plugins.py.

They are deliberately not two lists. A phone user looking for a notes app does
not care which mechanism delivers it, so both kinds sit in the same categories,
answer the same search, and use the same detail page. Only the machinery below
distinguishes them.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Shipped, root-owned. This same file is the install allowlist -- see
# data/moarchy-store-helper. A user-writable copy must never be substituted
# here, or the allowlist stops meaning anything.
SYSTEM_CATALOGUE = Path("/usr/share/moarchy-store/catalogue.toml")


@dataclass
class App:
    name: str
    category: str
    toolkit: str
    summary: str
    source: str = "pacman"

    # Exactly one of these identifies the app: pkg for a package, plugin_id
    # plus repo for a plugin. load_apps refuses an entry that has neither.
    pkg: str = ""
    plugin_id: str = ""
    repo: str = ""

    icon: str = ""
    screenshot: str = ""
    tested: str = ""

    # Filled in from the system, not from the catalogue.
    installed: bool = False
    version: str = ""
    installed_version: str = ""
    size: str = ""
    available: bool = True
    # Plugins only: installed but not loaded by the shell is a real state, and
    # an invisible one unless it is said out loud.
    enabled: bool = True

    @property
    def is_plugin(self) -> bool:
        return self.source == "plugin"

    @property
    def ident(self) -> str:
        """What this app is called, whichever kind it is. Used for search, for
        MOARCHY_STORE_DETAIL, and anywhere else the distinction is noise."""
        return self.plugin_id if self.is_plugin else self.pkg

    @property
    def verified(self) -> bool:
        """True when someone actually ran this on a device and said so."""
        return bool(self.tested)

    @property
    def update_available(self) -> bool:
        return (
            self.installed
            and bool(self.version)
            and bool(self.installed_version)
            and self.version != self.installed_version
        )


def shipped_path() -> Path:
    """The catalogue installed by the package, or the one beside the source
    tree when running from a checkout."""
    if SYSTEM_CATALOGUE.exists():
        return SYSTEM_CATALOGUE
    local = Path(__file__).resolve().parent.parent / "catalogue.toml"
    if local.exists():
        return local
    raise FileNotFoundError(
        f"No catalogue at {SYSTEM_CATALOGUE} and none beside the source tree"
    )


def catalogue_path() -> Path:
    """The catalogue to display: a verified remote one if we have it, else the
    shipped one.

    Only a signed catalogue is ever preferred. An unverified download is not a
    degraded catalogue, it is an untrusted one, so it is discarded rather than
    shown with a warning.
    """
    from . import remote

    shipped = shipped_path()
    verified = remote.verified_catalogue(shipped)
    if verified is not None:
        return verified
    return shipped


def refresh_remote() -> bool:
    """Try to pull a newer signed catalogue. Safe to call on every launch."""
    from . import remote

    try:
        return remote.update(shipped_path())
    except Exception:
        return False


def _app_from(entry: dict) -> App:
    """One catalogue entry, validated.

    Raises ValueError rather than returning something half-built: an entry the
    store cannot act on is worse than an absent one, because it renders as a
    working row with a button that fails.
    """
    source = entry.get("source", "pacman")
    if source not in ("pacman", "plugin"):
        raise ValueError(f"unknown source {source!r}")

    common = dict(
        name=entry["name"],
        category=entry.get("category", "Other"),
        toolkit=entry.get("toolkit", ""),
        summary=entry.get("summary", ""),
        icon=entry.get("icon", ""),
        screenshot=entry.get("screenshot", ""),
        tested=entry.get("tested", ""),
    )

    if source == "pacman":
        return App(source=source, pkg=entry["pkg"], **common)

    from . import plugins

    plugin_id, repo = entry["id"], entry["repo"]
    if not plugins.valid_id(plugin_id):
        raise ValueError(f"invalid plugin id {plugin_id!r}")
    if not plugins.valid_repo(repo):
        raise ValueError(f"invalid repo URL {repo!r} for {plugin_id}")
    return App(source=source, plugin_id=plugin_id, repo=repo, **common)


def load_apps() -> list[App]:
    with catalogue_path().open("rb") as fh:
        data = tomllib.load(fh)

    apps = []
    for entry in data.get("app", []):
        try:
            apps.append(_app_from(entry))
        except (KeyError, ValueError, TypeError) as exc:
            # One malformed entry should not blank the whole store.
            print(f"moarchy-store: skipping catalogue entry: {exc}")
    return apps


def allowed_packages() -> set[str]:
    """Every *package* the store is willing to act on.

    Plugins are deliberately absent. They never reach the privileged helper --
    they need no privilege -- and a plugin id like "shell.settings" is a
    perfectly valid pacman package name, so letting one into this set would
    quietly widen an allowlist whose whole point is being narrow.
    """
    return {app.pkg for app in load_apps() if not app.is_plugin}


def _pacman(*args: str) -> str:
    if not shutil.which("pacman"):
        return ""
    try:
        return subprocess.run(
            ["pacman", *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def enrich(apps: list[App]) -> list[App]:
    """Annotate every app with installed state and versions, whatever it is."""
    _enrich_packages([a for a in apps if not a.is_plugin])
    _enrich_plugins([a for a in apps if a.is_plugin])
    return apps


def _enrich_plugins(apps: list[App]) -> None:
    """Read plugin state off the filesystem rather than asking the shell.

    `omarchy plugin list` would answer this, but it needs jq, a correct
    OMARCHY_PATH and a *running* shell to talk to over IPC. The manifest and
    shell.json are the same facts without any of those preconditions, so the
    store still renders honestly on a device where the shell has crashed --
    which is exactly when someone opens a store to remove the plugin that did
    it.
    """
    if not apps:
        return

    from . import plugins

    enabled = plugins.enabled_ids()
    for app in apps:
        app.installed = plugins.is_installed(app.plugin_id)
        version = str(plugins.manifest(app.plugin_id).get("version", ""))
        # A plugin is a git checkout, not a repository index: the only version
        # knowable without a network fetch is the one on disk. Reporting it as
        # both stops update_available inventing an update that was never seen.
        app.version = version
        app.installed_version = version if app.installed else ""
        app.enabled = app.plugin_id in enabled if app.installed else True
        app.size = ""
        app.available = True


def _enrich_packages(apps: list[App]) -> None:
    """Annotate with installed state, versions and download size.

    Two bulk pacman calls rather than one per app: on a 1.15GHz A53 the
    per-process cost dominates, and a 20-app catalogue would otherwise mean 40
    process spawns every time the window opens.
    """
    # `pacman -Si` with no arguments describes every package in every sync
    # database. On this device that is tens of thousands of records and several
    # seconds of parsing, to answer a question nobody asked.
    if not apps:
        return

    installed: dict[str, str] = {}
    for line in _pacman("-Q").splitlines():
        parts = line.split()
        if len(parts) == 2:
            installed[parts[0]] = parts[1]

    repo: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in _pacman("-Si", *[a.pkg for a in apps]).splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "Name":
            current = value
            repo[current] = {}
        elif current and key in ("Version", "Installed Size", "Download Size"):
            repo[current][key] = value

    for app in apps:
        app.installed = app.pkg in installed
        app.installed_version = installed.get(app.pkg, "")
        info = repo.get(app.pkg, {})
        app.available = bool(info) or app.installed
        app.version = info.get("Version", app.installed_version)
        app.size = info.get("Download Size", info.get("Installed Size", ""))


def by_category(apps: list[App]) -> dict[str, list[App]]:
    groups: dict[str, list[App]] = {}
    for app in apps:
        groups.setdefault(app.category, []).append(app)
    for group in groups.values():
        group.sort(key=lambda a: a.name.lower())
    return dict(sorted(groups.items()))
