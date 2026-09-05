"""The window. Built for 360x720 logical pixels first, not scaled down to it.

Adw.NavigationView gives list -> detail push/pop with a back gesture, which is
the only navigation model that works when the window is one column wide and
there is no keyboard.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import installer, media  # noqa: E402
from . import catalogue  # noqa: E402
from .catalogue import App, by_category, enrich, load_apps  # noqa: E402


def icon_name_for(app: App) -> str:
    """The app's own icon if the theme really has it, else the first generic
    fallback that does.

    Two traps here, both hit in practice:

    * Falling back only when the catalogue field is empty is not enough. An app
      that is not installed has no icon on disk -- icons ship with the package --
      so GTK renders "image-missing", a broken-image glyph that reads as a bug.
    * The obvious fallback names mostly do not resolve either. On this device
      `package-x-generic` and `application-x-executable` exist only in
      AdwaitaLegacy, which is not the active theme, so naming one of those just
      moves the broken glyph rather than removing it. Hence a chain, checked
      against the live theme rather than assumed.
    """
    display = Gdk.Display.get_default()
    if display is None:
        return "application-x-executable-symbolic"

    theme = Gtk.IconTheme.get_for_display(display)
    if app.icon and theme.has_icon(app.icon):
        return app.icon

    for candidate in (
        "application-x-executable-symbolic",
        "package-x-generic-symbolic",
        "application-x-executable",
        "package-x-generic",
        "dialog-information-symbolic",
    ):
        if theme.has_icon(candidate):
            return candidate
    return "application-x-executable-symbolic"


class AppRow(Adw.ActionRow):
    def __init__(self, app: App, on_activate):
        super().__init__()
        self.app = app
        self.set_title(GLib.markup_escape_text(app.name))
        self.set_subtitle(GLib.markup_escape_text(app.summary))
        self.set_subtitle_lines(2)
        self.set_activatable(True)
        self.connect("activated", lambda *_: on_activate(app))

        icon = Gtk.Image.new_from_icon_name(icon_name_for(app))
        icon.set_pixel_size(32)
        icon.set_margin_top(6)
        icon.set_margin_bottom(6)
        self.add_prefix(icon)

        if app.installed:
            tick = Gtk.Image.new_from_icon_name("object-select-symbolic")
            tick.add_css_class("success")
            tick.set_tooltip_text("Installed")
            self.add_suffix(tick)
        elif not app.available:
            warn = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            warn.set_tooltip_text("Not found in the repositories")
            self.add_suffix(warn)

        self.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))


class DetailPage(Adw.NavigationPage):
    def __init__(self, app: App, refresh):
        super().__init__(title=app.name)
        self.app = app
        self.refresh = refresh

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        page = Adw.PreferencesPage()

        # Deliberately not Adw.StatusPage. Its icon scales to the space it is
        # given, so as soon as the content below overflows the screen the icon
        # is the first thing squashed -- and set_vexpand(False) does not stop
        # it. A Gtk.Image with an explicit pixel_size cannot be resized by its
        # parent, which is exactly what a fixed app icon wants.
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        header.set_margin_top(12)
        header.set_margin_bottom(6)

        icon = Gtk.Image.new_from_icon_name(icon_name_for(app))
        icon.set_pixel_size(96)
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_vexpand(False)
        header.append(icon)

        title = Gtk.Label(label=app.name)
        title.add_css_class("title-1")
        title.set_wrap(True)
        title.set_justify(Gtk.Justification.CENTER)
        header.append(title)

        blurb = Gtk.Label(label=app.summary)
        blurb.add_css_class("dim-label")
        blurb.set_wrap(True)
        blurb.set_justify(Gtk.Justification.CENTER)
        blurb.set_margin_start(12)
        blurb.set_margin_end(12)
        header.append(blurb)

        group_intro = Adw.PreferencesGroup()
        group_intro.add(header)
        page.add(group_intro)

        facts = Adw.PreferencesGroup(title="Details")
        if app.is_plugin:
            facts.add(self._row("Plugin", app.plugin_id))
            facts.add(self._row("Source", app.repo))
        else:
            facts.add(self._row("Package", app.pkg))
        if app.version:
            facts.add(self._row("Version", app.version))
        if app.installed and app.update_available:
            facts.add(self._row("Installed", app.installed_version))
        if app.size:
            facts.add(self._row("Download", app.size))
        if app.is_plugin and app.installed and not app.enabled:
            # Installed but not loaded is invisible from the outside: the app
            # drawer entry is there and opens nothing. Say it rather than let
            # someone conclude the plugin is broken.
            facts.add(self._row("Loaded", "No — installed but not enabled"))
        if app.toolkit:
            facts.add(self._row("Toolkit", self._toolkit_label(app.toolkit)))
        facts.add(
            self._row(
                "Verified",
                f"Tested on {app.tested}" if app.verified else "Not yet tested on a device",
            )
        )
        page.add(facts)

        if app.is_plugin:
            # The store passes --yes to `omarchy plugin add`, which is where
            # this warning would otherwise have been shown. Suppressing a
            # prompt is only defensible if what the prompt said still gets
            # said, and before the button rather than after it.
            #
            # A wrapping label rather than a _row: a row puts a long value in
            # the subtitle, which caps at three lines and would ellipsise the
            # one paragraph on this page that must be read in full.
            trust = Adw.PreferencesGroup(title="Runs in your shell")
            caution = Gtk.Label(
                label=(
                    "A plugin is unsandboxed QML loaded into omarchy-shell "
                    "itself, not a sandboxed app in a window of its own. It "
                    "asks for no password because it needs no privilege — it "
                    "can already do anything you can."
                )
            )
            caution.set_wrap(True)
            caution.set_xalign(0)
            caution.add_css_class("dim-label")
            caution.set_margin_top(6)
            caution.set_margin_bottom(6)
            trust.add(caution)
            page.add(trust)

        # Screenshots are fetched from GitHub and cached. The group only
        # appears once an image actually arrives, so an offline phone shows a
        # clean page rather than a broken-image placeholder.
        self.shot_group = Adw.PreferencesGroup(title="Screenshot")
        self.shot_picture = Gtk.Picture()
        self.shot_picture.set_can_shrink(True)
        self.shot_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.shot_picture.set_size_request(-1, 420)
        self.shot_picture.add_css_class("card")
        self.shot_group.add(self.shot_picture)
        self.shot_group.set_visible(False)
        page.add(self.shot_group)

        if app.screenshot:
            media.fetch(
                app.screenshot,
                lambda path: GLib.idle_add(self._show_screenshot, path),
            )

        actions = Adw.PreferencesGroup()
        self.button = Gtk.Button()
        self.button.set_halign(Gtk.Align.CENTER)
        self.button.set_margin_top(12)
        self._set_button_state()
        self.button.connect("clicked", self._on_clicked)
        actions.add(self.button)

        self.status_label = Gtk.Label()
        self.status_label.add_css_class("dim-label")
        self.status_label.set_wrap(True)
        self.status_label.set_margin_top(8)
        self.status_label.set_visible(False)
        actions.add(self.status_label)
        page.add(actions)

        toolbar.set_content(page)
        self.set_child(toolbar)

    def _show_screenshot(self, path) -> bool:
        try:
            self.shot_picture.set_filename(str(path))
            self.shot_group.set_visible(True)
        except Exception:
            pass  # a corrupt cache entry is not worth breaking the page over
        return False

    @staticmethod
    def _row(title: str, value: str) -> Adw.ActionRow:
        """Short values sit beside the title; long ones go underneath.

        A suffix label competes with the title for a 360px row, and the title
        loses -- "Toolkit" was being hyphenated to "Toolk-it" to make room for
        the value. Below a threshold there is room for both; above it, the
        subtitle wraps cleanly instead.
        """
        if len(value) <= 20:
            row = Adw.ActionRow(title=title)
            label = Gtk.Label(label=value)
            label.add_css_class("dim-label")
            label.set_selectable(True)
            row.add_suffix(label)
            return row

        row = Adw.ActionRow(title=title, subtitle=value)
        row.set_subtitle_lines(3)
        return row

    @staticmethod
    def _toolkit_label(toolkit: str) -> str:
        return {
            "libadwaita": "libadwaita — adapts to phone widths",
            "kirigami": "Kirigami — built for Plasma Mobile",
            # (both are long by design; _row moves them to the subtitle)
            "quickshell": "Quickshell/QML — a plugin drawn by the shell itself",
            "tui": "Terminal app",
            "gtk": "GTK",
            "qt": "Qt",
        }.get(toolkit, toolkit)

    def _set_button_state(self) -> None:
        self.button.set_sensitive(True)
        for css in ("suggested-action", "destructive-action"):
            self.button.remove_css_class(css)

        if not self.app.available and not self.app.installed:
            self.button.set_label("Not available")
            self.button.set_sensitive(False)
        elif self.app.installed:
            self.button.set_label("Remove")
            self.button.add_css_class("destructive-action")
        else:
            self.button.set_label("Install")
            self.button.add_css_class("suggested-action")

    def _on_clicked(self, _button) -> None:
        if not installer.available(self.app):
            self._say(installer.unavailable_reason(self.app))
            return

        action = "remove" if self.app.installed else "install"
        self.button.set_sensitive(False)
        self.button.set_label("Removing…" if action == "remove" else "Installing…")
        self._say(f"Working… ({self.app.ident})")

        installer.run(
            action,
            self.app,
            on_line=lambda line: GLib.idle_add(self._say, line),
            on_done=lambda ok, err: GLib.idle_add(self._finish, ok, err, action),
        )

    def _say(self, text: str) -> bool:
        self.status_label.set_visible(True)
        self.status_label.set_label(text)
        return False

    def _finish(self, ok: bool, err: str, action: str) -> bool:
        if ok:
            self.app.installed = action == "install"
            self.app.enabled = True
            self._say("Installed." if action == "install" else "Removed.")
            self.refresh()
        else:
            self._say(err or "Failed.")
        self._set_button_state()
        return False


class StoreWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("App Store")
        # Fits a 360x720 logical screen; still resizable on a desktop.
        self.set_default_size(360, 720)

        self.nav = Adw.NavigationView()
        self.set_content(self.nav)

        self.search = Gtk.SearchEntry(placeholder_text="Search apps")
        self.search.connect("search-changed", lambda *_: self._populate())

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Refresh installed state")
        # The refresh button also re-checks for a newly published catalogue.
        refresh.connect("clicked", lambda *_: self.refresh(check_remote=True))
        header.pack_end(refresh)

        clamp = Adw.Clamp(maximum_size=640)
        clamp.set_child(self.list_box)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(clamp)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        search_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        search_bar.set_margin_start(12)
        search_bar.set_margin_end(12)
        search_bar.set_margin_top(6)
        search_bar.set_margin_bottom(6)
        search_bar.append(self.search)
        content.append(search_bar)
        content.append(scroller)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(content)

        self.nav.push(Adw.NavigationPage(child=toolbar, title="App Store"))

        self.apps: list[App] = []
        self.refresh()
        # Check for a newly published catalogue after the window is up, so a
        # slow or dead network never delays first paint.
        GLib.timeout_add_seconds(1, self._check_remote_once)

        # Debug aid: MOARCHY_STORE_DETAIL=<package or plugin id> opens straight
        # to that app's detail page. Verifying the detail layout otherwise
        # means synthesising a pointer click, which a headless compositor has
        # no device for.
        wanted = os.environ.get("MOARCHY_STORE_DETAIL")
        if wanted:
            for app in self.apps:
                if app.ident == wanted:
                    GLib.idle_add(self._open_detail, app)
                    break

        # Do not let the search entry take focus at startup. squeekboard raises
        # itself whenever a text field is focused, so an autofocused search box
        # means the keyboard covers half the catalogue before you have looked at
        # it. Focus lands on the search entry when the user taps it, which is
        # when they actually want to type.
        GLib.idle_add(self._drop_focus)

    def _check_remote_once(self) -> bool:
        if catalogue.refresh_remote():
            self.refresh()
        return False  # one-shot

    def _drop_focus(self) -> bool:
        self.set_focus(None)
        return False

    def refresh(self, check_remote: bool = False) -> None:
        if check_remote:
            catalogue.refresh_remote()
        try:
            self.apps = enrich(load_apps())
        except FileNotFoundError as exc:
            self.apps = []
            self._show_error(str(exc))
            return
        self._populate()

    def _show_error(self, message: str) -> None:
        child = self.list_box.get_first_child()
        while child:
            self.list_box.remove(child)
            child = self.list_box.get_first_child()
        self.list_box.append(
            Adw.StatusPage(
                title="No catalogue",
                description=message,
                icon_name="dialog-warning-symbolic",
            )
        )

    def _populate(self) -> None:
        child = self.list_box.get_first_child()
        while child:
            self.list_box.remove(child)
            child = self.list_box.get_first_child()

        needle = self.search.get_text().strip().lower()
        shown = [
            a
            for a in self.apps
            if not needle
            or needle in a.name.lower()
            or needle in a.ident.lower()
            or needle in a.summary.lower()
            or needle in a.category.lower()
        ]

        if not shown:
            self.list_box.append(
                Adw.StatusPage(
                    title="Nothing matches",
                    description=f"No app matches “{self.search.get_text()}”",
                    icon_name="system-search-symbolic",
                )
            )
            return

        for category, apps in by_category(shown).items():
            installed = sum(1 for a in apps if a.installed)
            group = Adw.PreferencesGroup(
                title=category,
                description=f"{installed} of {len(apps)} installed",
            )
            group.set_margin_start(12)
            group.set_margin_end(12)
            group.set_margin_top(6)
            group.set_margin_bottom(6)
            for app in apps:
                group.add(AppRow(app, self._open_detail))
            self.list_box.append(group)

    def _open_detail(self, app: App) -> None:
        self.nav.push(DetailPage(app, self.refresh))
