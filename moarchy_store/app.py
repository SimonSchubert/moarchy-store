from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import theme  # noqa: E402
from .window import StoreWindow  # noqa: E402


class StoreApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.moarchy.Store")
        self._styles: Gtk.CssProvider | None = None

    def do_activate(self) -> None:  # noqa: N802 (GObject naming)
        self._load_styles()
        window = self.props.active_window or StoreWindow(application=self)
        window.present()

    def _load_styles(self) -> None:
        """Install the stylesheet, at APPLICATION priority.

        Not USER: that outranks a user's own gtk.css, and someone who has gone
        to the trouble of writing one means it.
        """
        display = Gdk.Display.get_default()
        if display is None:
            return
        if self._styles is None:
            self._styles = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                display, self._styles, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._styles.load_from_string(theme.stylesheet())


def main() -> int:
    return StoreApplication().run(sys.argv)
