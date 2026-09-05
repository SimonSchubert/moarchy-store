from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw  # noqa: E402

from .window import StoreWindow  # noqa: E402


class StoreApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.moarchy.Store")

    def do_activate(self) -> None:  # noqa: N802 (GObject naming)
        window = self.props.active_window or StoreWindow(application=self)
        window.present()


def main() -> int:
    return StoreApplication().run(sys.argv)
