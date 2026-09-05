"""Take the palette from the Omarchy theme, so the store looks like the phone.

Every other surface on this device -- the bar, the drawer, the shade, the
keyboard -- draws itself from the active theme's `colors.toml`. A stock
libadwaita app is the one thing that does not, and it shows: on tokyo-night the
shell is #1a1b26 with a #7aa2f7 accent while the store sits there in Adwaita
grey with a stock blue. Reading the same file is what makes it part of the
phone rather than a visitor on it.

`~/.local/state/omarchy/current/theme` is a staged *copy* of the theme rather
than a symlink to it, which is what omarchy-theme-set maintains and what
omarchy-theme-current reads. Following the copy means a theme switch is picked
up without knowing anything about where themes are installed.

Everything here degrades to stock libadwaita. On a desktop with no Omarchy, or
a theme with no colors.toml, or a malformed one, no palette is injected and the
widget classes below fall back to libadwaita's own named colours -- so the app
is themed, never broken, by the absence of a file.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CURRENT_THEME = Path.home() / ".local" / "state" / "omarchy" / "current" / "theme"
COLORS = CURRENT_THEME / "colors.toml"

# Hex only. These values are interpolated into a stylesheet, so anything that is
# not plainly a colour is not going into it -- a theme is data, not code.
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


@dataclass(frozen=True)
class Palette:
    accent: str
    background: str
    surface: str
    raised: str
    foreground: str
    dim: str
    green: str
    red: str
    dark: bool


def _colour(data: dict, *names: str) -> str:
    """First key that is present and is actually a colour."""
    for name in names:
        value = data.get(name)
        if isinstance(value, str) and HEX.match(value.strip()):
            return value.strip()
    return ""


def load(path: Path = COLORS) -> Palette | None:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    accent = _colour(data, "accent", "blue")
    background = _colour(data, "background")
    foreground = _colour(data, "bright_foreground", "foreground")
    if not (accent and background and foreground):
        # A palette missing any of these would leave half the app themed and
        # half not, which looks worse than not theming it at all.
        return None

    return Palette(
        accent=accent,
        background=background,
        # Rows and cards sit just off the background. lighter_background is the
        # theme's own answer for that; selection is the usual second choice.
        surface=_colour(data, "lighter_background", "selection") or background,
        raised=_colour(data, "selection", "lighter_background") or background,
        foreground=foreground,
        dim=_colour(data, "dark_foreground", "muted") or foreground,
        green=_colour(data, "green", "bright_green") or accent,
        red=_colour(data, "red", "bright_red") or accent,
        dark=str(data.get("mode", "dark")).lower() != "light",
    )


def palette_css(p: Palette) -> str:
    """Point libadwaita's named colours at the theme.

    Overriding the names rather than restyling widgets is what makes every
    stock widget -- rows, headers, entries, buttons -- follow the theme without
    this file having to know they exist.
    """
    return f"""
@define-color window_bg_color {p.background};
@define-color window_fg_color {p.foreground};
@define-color view_bg_color {p.background};
@define-color view_fg_color {p.foreground};
@define-color headerbar_bg_color {p.background};
@define-color headerbar_fg_color {p.foreground};
@define-color card_bg_color {p.surface};
@define-color card_fg_color {p.foreground};
@define-color dialog_bg_color {p.background};
@define-color dialog_fg_color {p.foreground};
@define-color popover_bg_color {p.surface};
@define-color popover_fg_color {p.foreground};
@define-color accent_bg_color {p.accent};
@define-color accent_fg_color {p.background};
@define-color accent_color {p.accent};
@define-color success_color {p.green};
@define-color destructive_bg_color {p.red};
@define-color destructive_color {p.red};
@define-color moarchy_dim {p.dim};
"""


# The stylesheet proper. It names only libadwaita colours, so it is correct with
# or without a palette above it.
#
# No shadows and no gradients anywhere, and that is a constraint rather than a
# preference: this device has no usable GL context (Mali-400 tops out at GLES
# 2.0, and GTK falls back to software rendering), so every blurred edge is
# CPU-blended on a 1.15GHz A53 while the user scrolls. Flat fills with radii
# cost nothing and, at this size, read as more deliberate anyway.
WIDGETS = """
/* The tile is what pulls a catalogue of mismatched icons together: app icons
 * are square-ish and full-colour, symbolic fallbacks are thin monochrome
 * glyphs, and at 32px loose on a row they read as a jumble of unrelated
 * shapes. One rounded container each, and they become a grid. */
.app-tile {
  background-color: alpha(currentColor, 0.07);
  border-radius: 13px;
}

.app-tile.hero {
  border-radius: 26px;
}

/* Symbolic icons only. A full-colour app icon must keep its own colours, but a
 * fallback glyph rendered in plain white is the starkest thing on the screen --
 * in the accent it reads as a deliberate placeholder instead. */
.tile-symbolic {
  color: @accent_color;
}

/* On the box around the glyph, never on the image: sizing rules applied to a
 * GtkImage fight the pixel size it was given, and GTK warns on every measure
 * pass for every row. */
.installed-badge {
  color: @success_color;
  background-color: @card_bg_color;
  border-radius: 999px;
  padding: 3px;
}

/* Category counts. The group description used to carry "2 of 2 installed" on
 * its own line, which cost a line of vertical space per category -- ten of them
 * on this catalogue -- to say something a chip says in the corner. */
.count-chip {
  color: @moarchy_dim;
  font-size: 0.82em;
  font-weight: bold;
  padding: 2px 9px;
  border-radius: 999px;
  background-color: alpha(currentColor, 0.10);
}

.meta-chip {
  font-size: 0.85em;
  padding: 5px 12px;
  border-radius: 999px;
  background-color: alpha(currentColor, 0.10);
}

.meta-chip.verified {
  color: @success_color;
  background-color: alpha(@success_color, 0.15);
}

.meta-chip.untested {
  color: @moarchy_dim;
}

.app-title {
  font-weight: 800;
  font-size: 1.6em;
}

.app-summary {
  color: @moarchy_dim;
}

/* Full width and tall enough to be a target rather than a link. The store's
 * one action should not be a small button floating in the middle. */
.pill-button {
  border-radius: 999px;
  font-weight: bold;
  min-height: 44px;
  padding-left: 28px;
  padding-right: 28px;
}

.shot-frame {
  border-radius: 18px;
  background-color: @card_bg_color;
}

.section-heading {
  font-weight: bold;
  font-size: 0.95em;
}
"""


def stylesheet() -> str:
    p = load()
    return (palette_css(p) if p else "") + WIDGETS
