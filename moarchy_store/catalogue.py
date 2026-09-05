"""Load the curated catalogue and enrich it from pacman.

The split matters: the TOML carries the *judgement* (does this app work on a
phone), pacman carries the *facts* (version, size, installed or not). Neither
duplicates the other, so the catalogue never goes stale about versions and
pacman is never asked to have an opinion.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Shipped, root-owned. This same file is the install allowlist -- see
# data/moarchy-store-helper. A user-writable copy must never be substituted
# here, or the allowlist stops meaning anything.
SYSTEM_CATALOGUE = Path("/usr/share/moarchy-store/catalogue.toml")


@dataclass
class App:
    pkg: str
    name: str
    category: str
    toolkit: str
    summary: str
    icon: str = ""
    screenshot: str = ""
    tested: str = ""

    # Filled in from pacman, not from the catalogue.
    installed: bool = False
    version: str = ""
    installed_version: str = ""
    size: str = ""
    available: bool = True

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


def catalogue_path() -> Path:
    """Prefer the installed catalogue; fall back to the source tree when
    running from a checkout."""
    if SYSTEM_CATALOGUE.exists():
        return SYSTEM_CATALOGUE
    local = Path(__file__).resolve().parent.parent / "catalogue.toml"
    if local.exists():
        return local
    raise FileNotFoundError(
        f"No catalogue at {SYSTEM_CATALOGUE} and none beside the source tree"
    )


def load_apps() -> list[App]:
    with catalogue_path().open("rb") as fh:
        data = tomllib.load(fh)

    apps = []
    for entry in data.get("app", []):
        try:
            apps.append(
                App(
                    pkg=entry["pkg"],
                    name=entry["name"],
                    category=entry.get("category", "Other"),
                    toolkit=entry.get("toolkit", ""),
                    summary=entry.get("summary", ""),
                    icon=entry.get("icon", ""),
                    screenshot=entry.get("screenshot", ""),
                    tested=entry.get("tested", ""),
                )
            )
        except KeyError as exc:
            # One malformed entry should not blank the whole store.
            print(f"moarchy-store: skipping catalogue entry missing {exc}")
    return apps


def allowed_packages() -> set[str]:
    """Every package the store is willing to act on."""
    return {app.pkg for app in load_apps()}


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
    """Annotate with installed state, versions and download size.

    Two bulk pacman calls rather than one per app: on a 1.15GHz A53 the
    per-process cost dominates, and a 20-app catalogue would otherwise mean 40
    process spawns every time the window opens.
    """
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

    return apps


def by_category(apps: list[App]) -> dict[str, list[App]]:
    groups: dict[str, list[App]] = {}
    for app in apps:
        groups.setdefault(app.category, []).append(app)
    for group in groups.values():
        group.sort(key=lambda a: a.name.lower())
    return dict(sorted(groups.items()))
