"""The window. Built for 360x720 logical pixels first, not scaled down to it.

Adw.NavigationView gives list -> detail push/pop with a back gesture, which is
the only navigation model that works when the window is one column wide and
there is no keyboard.
"""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from . import installer, launcher, media, theme  # noqa: E402
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


# The tile sizes, with the icon each one holds and the class that gives it a
# radius in proportion. A third would need a third radius in theme.py, so they
# are spelled out here rather than computed from a ratio.
TILE_SIZES = {
    48: (30, ""),        # a grid card with no screenshot yet
    104: (60, "hero"),   # the detail page
}


def app_tile(app: App, size: int = 48) -> Gtk.Widget:
    """An icon in a rounded tile.

    The catalogue's icons come from wherever each app's own .desktop file
    points, so they are a mix of full-colour squares, circles, and thin
    symbolic glyphs where nothing resolved. Loose on a row they read as
    clutter; one container each and the list reads as a grid. See the .app-tile
    comment in theme.py.
    """
    pixels, radius_class = TILE_SIZES[size]
    name = icon_name_for(app)
    icon = Gtk.Image.new_from_icon_name(name)
    icon.set_pixel_size(pixels)
    icon.set_halign(Gtk.Align.CENTER)
    icon.set_valign(Gtk.Align.CENTER)
    icon.set_hexpand(True)
    icon.set_vexpand(True)
    # Only a fallback glyph gets tinted; a real app icon keeps its own colours.
    if name.endswith("-symbolic"):
        icon.add_css_class("tile-symbolic")

    tile = Gtk.Box()
    tile.add_css_class("app-tile")
    if radius_class:
        tile.add_css_class(radius_class)
    # Explicit, not merely a CSS minimum. Some themes ship an icon whose natural
    # size is larger than the pixel size asked for, and the tile then grows to
    # fit it -- which is why the first attempt had every row's tile starting at
    # a different x. A fixed request is what makes the column a column.
    tile.set_size_request(size, size)
    tile.set_halign(Gtk.Align.CENTER)
    tile.set_valign(Gtk.Align.CENTER)
    # Stop here, explicitly. The icon expands so that halign CENTER has room to
    # centre it inside the tile, but expand propagates *upward*: without this
    # the row's whole prefix column inherited it, claimed whatever space the
    # title did not want, and pushed the text right by however much the summary
    # happened not to need. Rows with a short summary indented most, so the
    # list read as ragged. An explicit False overrides the value a parent would
    # otherwise compute from its children.
    tile.set_hexpand(False)
    tile.set_vexpand(False)
    tile.append(icon)
    return tile


# One chain per category, tried in order. The obvious name is not always the
# one a given theme has -- "phone-symbolic" is Adwaita's, "call-start-symbolic"
# the older freedesktop name -- so each category names alternatives and every
# chain ends at a generic glyph rather than at a broken image. Same trap as
# icon_name_for, one level up.
CATEGORY_ICONS = {
    "Chat": ("chat-symbolic", "chat-message-new-symbolic",
             "user-available-symbolic"),
    "Files": ("folder-symbolic", "system-file-manager-symbolic"),
    "Games": ("applications-games-symbolic", "input-gaming-symbolic"),
    "Media": ("applications-multimedia-symbolic", "multimedia-player-symbolic",
              "audio-x-generic-symbolic"),
    "Notes": ("text-editor-symbolic", "accessories-text-editor-symbolic",
              "document-edit-symbolic"),
    "Phone": ("phone-symbolic", "call-start-symbolic", "phone-old-symbolic"),
    "Reading": ("book-open-symbolic", "user-bookmarks-symbolic",
                "x-office-document-symbolic"),
    "Security": ("security-high-symbolic", "channel-secure-symbolic",
                 "dialog-password-symbolic"),
    "System": ("applications-system-symbolic", "computer-symbolic",
               "preferences-system-symbolic"),
    "Terminal": ("utilities-terminal-symbolic", "terminal-symbolic"),
    "Time": ("alarm-symbolic", "preferences-system-time-symbolic",
             "document-open-recent-symbolic"),
    "Travel": ("mark-location-symbolic", "find-location-symbolic",
               "emoji-travel-symbolic"),
    "Utilities": ("applications-utilities-symbolic", "preferences-other-symbolic",
                  "emblem-system-symbolic"),
    "Web": ("web-browser-symbolic", "internet-web-browser-symbolic",
            "globe-symbolic"),
}

# What a category this table has never heard of gets. A new category in the
# catalogue should look unremarkable here, not broken.
GENERIC_ICONS = ("view-app-grid-symbolic", "view-grid-symbolic",
                 "applications-other-symbolic", "view-list-symbolic")

_FALLBACK_THEME: Gtk.IconTheme | None = None


def _fallback_theme() -> Gtk.IconTheme:
    """Adwaita, read off the disk, whatever the device thinks its theme is.

    This image sets gtk-icon-theme-name to a theme it does not ship, and GTK
    does not quietly substitute Adwaita when that happens -- it falls back to
    the 164 glyphs compiled into GTK itself. Not one of them is a speech
    bubble, a book or a game controller, so a grid of categories drawn from
    them is fourteen copies of the same generic square, which is what the first
    attempt at this screen actually looked like.

    Adwaita's 646 symbolic icons are on disk regardless, so they are asked
    second. An icon theme of our own, consulted for our own glyphs only: the
    alternative is setting gtk-icon-theme-name for the process, which would
    quietly overrule the user's choice for every icon in the app rather than
    for the fourteen we drew this screen around.
    """
    global _FALLBACK_THEME
    if _FALLBACK_THEME is None:
        icons = Gtk.IconTheme()
        icons.set_search_path([
            str(Path.home() / ".local" / "share" / "icons"),
            "/usr/local/share/icons",
            "/usr/share/icons",
        ])
        icons.set_theme_name("Adwaita")
        _FALLBACK_THEME = icons
    return _FALLBACK_THEME


def _scale_factor(display: Gdk.Display) -> int:
    """What a paintable has to be rendered at to be sharp. A GtkIconPaintable
    is rasterised once, at lookup, so asking for 24 on a 2x screen gets a 24px
    texture stretched to 48 -- a blurry icon beside crisp text."""
    monitors = display.get_monitors()
    monitor = monitors.get_item(0) if monitors.get_n_items() else None
    return monitor.get_scale_factor() if monitor else 1


def category_image(category: str | None, pixels: int) -> Gtk.Image:
    """The glyph for a category, from the live theme if it has one."""
    image = Gtk.Image()
    image.set_pixel_size(pixels)
    image.add_css_class("category-icon")

    specific = CATEGORY_ICONS.get(category or "", ())
    display = Gdk.Display.get_default()
    if display is None:
        image.set_from_icon_name((specific or GENERIC_ICONS)[0])
        return image

    live = Gtk.IconTheme.get_for_display(display)
    fallback = _fallback_theme()

    # The generic glyph is the last resort for *this category*, not for this
    # lookup: asked in one flat chain it wins immediately, because a grid
    # square is one of the few things every theme has -- which is how the first
    # version of this screen ended up fourteen identical squares with the
    # right icons sitting on disk unread.
    for names in (specific, GENERIC_ICONS):
        for candidate in names:
            if live.has_icon(candidate):
                image.set_from_icon_name(candidate)
                return image
        for candidate in names:
            if fallback.has_icon(candidate):
                image.set_from_paintable(
                    fallback.lookup_icon(
                        candidate, None, pixels, _scale_factor(display),
                        Gtk.TextDirection.NONE,
                        Gtk.IconLookupFlags.FORCE_SYMBOLIC,
                    )
                )
                return image

    image.set_from_icon_name(GENERIC_ICONS[0])
    return image


def category_tile(title: str, on_click) -> Gtk.Widget:
    """One cell of the front page's grid: a glyph and what the category is.

    A button rather than a row, because the whole cell is the target. At 360px
    the grid is three across, which leaves about 106px -- enough for a glyph
    and a word, and not enough for anything else, so the name ellipsises rather
    than wrapping a category into two lines of ragged text.

    No count of what you already have, here or on the category's own page. A
    grid is a way in; a number under every door is something to read before you
    can use one.
    """
    icon = category_image(title, 24)

    name = Gtk.Label(label=title)
    name.add_css_class("category-name")
    name.set_ellipsize(Pango.EllipsizeMode.END)
    name.set_max_width_chars(12)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_valign(Gtk.Align.CENTER)
    box.append(icon)
    box.append(name)

    tile = Gtk.Button()
    tile.add_css_class("category-tile")
    tile.set_child(box)
    # The number the FlowBox counts with: three of these plus spacing do not
    # fit a 360px screen four times, and do fit a 640px one.
    tile.set_size_request(96, -1)
    tile.connect("clicked", lambda *_: on_click())
    return tile


def shot_for_theme(app: App) -> str:
    """The one screenshot to lead with: the one matching the running theme.

    With no staged Omarchy theme there is no colors.toml to ask, so libadwaita
    -- which is drawing this window in that case -- is asked instead. The
    detail page orders the rest behind this one; a card only ever shows this.
    """
    mode = theme.mode()
    if not mode:
        mode = "dark" if Adw.StyleManager.get_default().get_dark() else "light"
    return app.shot_for(mode) or app.screenshot


def card_pills(app: App) -> list[str]:
    """What a card says under the name.

    `features` first: that is the vocabulary the catalogue keeps for exactly
    this question -- what an app opens, what it speaks -- and it is the only
    field here that answers "what is this for" rather than "what is this".
    `subcategory` stands in when there are none. It is a fact from the same
    sweep and it is at least finer than the category whose page you are
    already standing on.

    Three at most. A 148px cell fits two on a line, and a third wrapping onto
    a second is the most a cell carries before it is a paragraph again. The
    detail page lists every one of them.
    """
    pills = [f for f in app.features if f]
    if not pills and app.subcategory:
        pills = [app.subcategory]
    return pills[:3]


def feature_pill(text: str) -> Gtk.Widget:
    pill = Gtk.Label(label=text)
    pill.add_css_class("feature-pill")
    pill.set_ellipsize(Pango.EllipsizeMode.END)
    pill.set_max_width_chars(16)
    return pill


# How tall a card's picture is, in logical pixels. A shot is 720x1440, so at
# the ~148px a two-across grid leaves it, showing the whole thing would be
# 296px of picture per card and one and a half rows on a 720px screen. This is
# a window onto the middle of the shot instead -- roughly 3:4, which is enough
# of an app to recognise it and short enough that four cards fit a screen.
CARD_SHOT_HEIGHT = 190


def card_shot(app: App) -> tuple[Gtk.Widget, Gtk.Picture]:
    """The picture at the top of a card, with the app's icon showing through
    until -- or unless -- a screenshot arrives.

    The placeholder is the icon tile the list used to show anyway, so an app
    with no screenshot in the catalogue, and a phone that is simply offline,
    both get the old row's picture rather than a grey rectangle.

    Overflow HIDDEN rather than CSS alone: border-radius on a widget rounds
    the background it paints, and a GtkPicture's texture is not that -- an
    unclipped shot puts four square corners inside a rounded card.
    """
    frame = Gtk.Overlay()
    frame.add_css_class("card-shot")
    frame.set_overflow(Gtk.Overflow.HIDDEN)
    frame.set_size_request(-1, CARD_SHOT_HEIGHT)
    frame.set_hexpand(True)

    frame.set_child(app_tile(app, size=48))

    picture = Gtk.Picture()
    picture.set_can_shrink(True)
    # COVER, so a portrait shot fills the width of the cell and is cropped
    # top and bottom rather than letterboxed into a stripe down the middle.
    picture.set_content_fit(Gtk.ContentFit.COVER)
    frame.add_overlay(picture)

    badge_icon, tip = "", ""
    if app.installed:
        badge_icon, tip = "object-select-symbolic", "Installed"
    elif not app.available:
        badge_icon, tip = "dialog-warning-symbolic", "Not found in the repositories"

    if badge_icon:
        glyph = Gtk.Image.new_from_icon_name(badge_icon)
        glyph.set_pixel_size(12)
        # The disc is a Box around the image, not the image itself. CSS
        # min-width on a GtkImage fights the pixel size it was given, and GTK
        # says so once per measure pass, per badge.
        badge = Gtk.Box()
        badge.append(glyph)
        badge.add_css_class("installed-badge")
        if not app.installed:
            badge.add_css_class("warning")
        badge.set_halign(Gtk.Align.END)
        badge.set_valign(Gtk.Align.START)
        badge.set_margin_top(6)
        badge.set_margin_end(6)
        badge.set_tooltip_text(tip)
        frame.add_overlay(badge)

    return frame, picture


def app_card(app: App, on_activate) -> Gtk.Widget:
    """One app in the grid: what it looks like, what it is called, what it does.

    This replaced a row with a 48px icon, and the reason is that an icon is the
    one thing about a phone app that says nothing useful. Three quarters of
    this catalogue is GNOME, so three quarters of the icons were a rounded
    square in the same palette, and the row's real content was the summary
    beside it -- a paragraph you had to read to tell two apps apart. A
    screenshot answers "what is this" before you have read anything.
    """
    frame, picture = card_shot(app)

    name = Gtk.Label(label=app.name, xalign=0)
    name.add_css_class("card-name")
    name.set_ellipsize(Pango.EllipsizeMode.END)
    # Without this a long name asks for its full width as the cell's minimum,
    # and a homogeneous FlowBox hands every other cell the same -- one
    # "GNOME Text Editor" and the grid is one column wide.
    name.set_max_width_chars(8)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_valign(Gtk.Align.START)
    box.append(frame)
    box.append(name)

    pills = card_pills(app)
    if pills:
        wrap = Adw.WrapBox(child_spacing=4, line_spacing=4)
        for text in pills:
            wrap.append(feature_pill(text))
        box.append(wrap)

    card = Gtk.Button()
    card.add_css_class("app-card")
    card.set_child(box)
    # The number the FlowBox counts with, exactly as for a category tile. A
    # card's own minimum is the 48px placeholder icon plus padding, so left to
    # itself the grid would fit three 64px cells on a 360px line and call that
    # two-across working. 140 is the width at which three do not fit a phone
    # (3x140 + spacing = 440 > 336) and do fit the 640px clamp.
    card.set_size_request(140, -1)
    card.connect("clicked", lambda *_: on_activate(app))

    shot = shot_for_theme(app)
    if shot:
        media.thumbnail(
            shot,
            lambda path, p=picture: GLib.idle_add(_show_card_shot, p, path),
        )
    return card


def _show_card_shot(picture: Gtk.Picture, path) -> bool:
    try:
        picture.set_filename(str(path))
    except Exception:
        pass  # a corrupt cache entry is not worth breaking the grid over
    return False


def app_grid(apps: list[App], on_activate) -> Gtk.Widget:
    """Two across on a phone, three in a desktop window.

    Both ends are set for the same reason the category grid sets them: left
    alone a FlowBox fits as many cells as the minimum widths allow, and a
    minimum width here is a picture that can shrink -- so it would happily
    put five unreadable screenshots on a 360px line.
    """
    grid = Gtk.FlowBox()
    grid.set_selection_mode(Gtk.SelectionMode.NONE)
    grid.set_homogeneous(True)
    grid.set_min_children_per_line(2)
    grid.set_max_children_per_line(3)
    grid.set_row_spacing(10)
    grid.set_column_spacing(10)
    grid.set_valign(Gtk.Align.START)
    for app in apps:
        grid.append(app_card(app, on_activate))
    return grid


def empty(box: Gtk.Box) -> None:
    """Remove every child. GTK4 has no remove_all on Gtk.Box."""
    child = box.get_first_child()
    while child:
        box.remove(child)
        child = box.get_first_child()


def app_group(title: str, apps: list[App], on_activate) -> Adw.PreferencesGroup:
    """A grid of cards, with the installed count in the corner.

    The count used to live in the group description, which cost a line of
    vertical space per category to say what a chip says in the corner.
    """
    group = Adw.PreferencesGroup(title=title)
    if title:
        installed = sum(1 for a in apps if a.installed)
        count = Gtk.Label(label=f"{installed}/{len(apps)}")
        count.add_css_class("count-chip")
        count.set_valign(Gtk.Align.CENTER)
        count.set_tooltip_text(f"{installed} of {len(apps)} installed")
        group.set_header_suffix(count)
    group.set_margin_start(12)
    group.set_margin_end(12)
    group.set_margin_top(6)
    group.set_margin_bottom(6)
    group.add(app_grid(apps, on_activate))
    return group


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
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        header.set_margin_top(8)
        header.set_margin_bottom(4)
        header.append(app_tile(app, size=104))

        title = Gtk.Label(label=app.name)
        title.add_css_class("app-title")
        title.set_wrap(True)
        title.set_justify(Gtk.Justification.CENTER)
        header.append(title)

        blurb = Gtk.Label(label=app.summary)
        blurb.add_css_class("app-summary")
        blurb.set_wrap(True)
        blurb.set_justify(Gtk.Justification.CENTER)
        blurb.set_margin_start(8)
        blurb.set_margin_end(8)
        header.append(blurb)

        # The three facts worth knowing at a glance, as chips rather than as
        # three more rows in a list of rows. WrapBox because "libadwaita --
        # adapts to phone widths" is a wide chip and 360px is not wide.
        chips = Adw.WrapBox(child_spacing=6, line_spacing=6)
        # `align` centres each *line* within the box. halign alone only centres
        # the box, which leaves a wrapped second line hanging off to the left.
        chips.set_align(0.5)
        chips.set_margin_top(2)
        for label, css in self._chips(app):
            chips.append(self._chip(label, css))
        header.append(chips)

        group_intro = Adw.PreferencesGroup()
        group_intro.add(header)
        page.add(group_intro)

        # What it actually supports -- the protocols a chat app speaks, the
        # formats a reader opens. Above the button, because it is the last
        # thing you check before deciding, and one flat list of chips rather
        # than a row per namespace: at 360px a row costs a whole line to say
        # one word.
        if app.features:
            supports = Adw.PreferencesGroup(title="Supports")
            tags = Adw.WrapBox(child_spacing=6, line_spacing=6)
            tags.set_margin_top(4)
            tags.set_margin_bottom(4)
            for feature in app.features:
                tags.append(self._chip(feature, ""))
            supports.add(tags)
            page.add(supports)

        if app.is_plugin:
            # Above the button, and that ordering is the whole point. The store
            # passes --yes to `omarchy plugin add`, which is where this warning
            # would otherwise have appeared; suppressing a prompt is only
            # defensible if what the prompt said still gets said, and gets said
            # before the thing it is warning about.
            #
            # A wrapping label rather than a _row: a row puts a long value in
            # the subtitle, which caps at three lines and would ellipsise the
            # one paragraph on this page that has to be read in full.
            trust = Adw.PreferencesGroup(title="Runs in your shell")
            caution = Gtk.Label(
                label=(
                    "Unsandboxed QML loaded into omarchy-shell itself, not a "
                    "sandboxed app in a window of its own. It asks for no "
                    "password because it needs no privilege — it can already "
                    "do anything you can."
                )
            )
            caution.set_wrap(True)
            caution.set_xalign(0)
            caution.add_css_class("app-summary")
            caution.set_margin_top(2)
            caution.set_margin_bottom(2)
            trust.add(caution)
            page.add(trust)

        # The action goes here, not after the Details list. On a 720px-tall
        # screen the hero alone fills the first view, so a button below the
        # facts is a button you have to go looking for -- and the facts are
        # what you read *after* deciding, not before.
        actions = Adw.PreferencesGroup()

        # Open sits beside Remove rather than replacing it, and comes first:
        # once something is installed, opening it is the thing you came back
        # for, and removing it is the rarer, heavier action.
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_homogeneous(True)
        buttons.set_margin_top(14)
        buttons.set_margin_start(4)
        buttons.set_margin_end(4)

        self.open_button = Gtk.Button(label="Open")
        self.open_button.add_css_class("pill-button")
        self.open_button.add_css_class("suggested-action")
        self.open_button.connect("clicked", self._on_open)
        buttons.append(self.open_button)

        self.button = Gtk.Button()
        self.button.add_css_class("pill-button")
        self.button.connect("clicked", self._on_clicked)
        buttons.append(self.button)

        self._set_button_state()
        actions.add(buttons)

        self.status_label = Gtk.Label()
        self.status_label.add_css_class("dim-label")
        self.status_label.set_wrap(True)
        self.status_label.set_margin_top(8)
        self.status_label.set_visible(False)
        actions.add(self.status_label)
        page.add(actions)

        # Directly under the button and ahead of the prose. Once the hero has
        # said what this is, the next question is what it looks like, and a
        # picture answers that faster than two paragraphs -- so what follows
        # the action is the app itself, not a description of it.
        #
        # Fetched from GitHub and cached. The group only appears once an image
        # actually arrives, so an offline phone shows a clean page rather than
        # a broken-image placeholder.
        shots = self._shot_order(app)
        self.shot_group = Adw.PreferencesGroup(
            title="Screenshot" if len(shots) < 2 else "Screenshots"
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.shot_carousel = Adw.Carousel()
        self.shot_carousel.set_allow_long_swipes(False)

        # Every page is built now and filled by index as fetches land. They
        # complete out of order, so appending on arrival would shuffle light
        # and dark around between runs.
        self.shot_pictures = []
        for _ in shots:
            picture = Gtk.Picture()
            picture.set_can_shrink(True)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_size_request(-1, 420)
            picture.add_css_class("shot-frame")
            self.shot_carousel.append(picture)
            self.shot_pictures.append(picture)
        box.append(self.shot_carousel)

        # Dots only when there is somewhere to swipe to.
        if len(shots) > 1:
            box.append(Adw.CarouselIndicatorDots(carousel=self.shot_carousel))

        self.shot_group.add(box)
        self.shot_group.set_visible(False)
        page.add(self.shot_group)

        for index, name in enumerate(shots):
            media.fetch(
                name,
                lambda path, i=index: GLib.idle_add(self._show_screenshot, i, path),
            )

        # After the screenshot: prose is what you read once you have decided,
        # or when the summary above did not settle it. The subtitle rides as
        # the group description -- it is what the app calls itself, where
        # `summary` is what we think of it, and stacking both under the hero
        # would put two one-liners in a row.
        if app.description or app.subtitle:
            about = Adw.PreferencesGroup(title="About")
            if app.subtitle:
                about.set_description(app.subtitle)
            if app.description:
                blurb = Gtk.Label(label=app.description)
                blurb.set_wrap(True)
                blurb.set_xalign(0)
                blurb.add_css_class("app-summary")
                blurb.set_margin_top(2)
                blurb.set_margin_bottom(2)
                about.add(blurb)
            page.add(about)

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
        if app.cost_pkgs:
            # Distinct from Download above, which is this package alone. What
            # you actually pay is the dependency stack it drags onto a
            # GNOME-adjacent image, and that is the number the serial-7 commit
            # reasoned about by hand: Fractal adds nothing, Kaidan adds 44.
            facts.add(self._row("With dependencies", self._cost_label(app)))
        if app.toolkit:
            facts.add(self._row("Toolkit", self._toolkit_label(app.toolkit)))
        if app.released:
            facts.add(self._row("Last release", app.released))
        if app.homepage:
            facts.add(self._row("Homepage", app.homepage))
        # No Verified row: the hero chip above already says it, in colour.
        page.add(facts)

        toolbar.set_content(page)
        self.set_child(toolbar)

    @staticmethod
    def _shot_order(app: App) -> list[str]:
        """The screenshots to show, the one matching the running theme first.

        So the page opens on the app as it will actually look, and the other
        mode is one swipe away rather than behind a control. The lead shot is
        the same one the grid card showed, so tapping a card does not change
        the picture under your thumb.
        """
        if not app.shots:
            return [app.screenshot] if app.screenshot else []
        first = shot_for_theme(app)
        order = [first] + [s for s in app.shots if s != first]
        # A screenshot named in catalogue.toml but absent from `shots` predates
        # the pairs, and for the first sixteen entries that means it was taken
        # on a PinePhone. That is stronger evidence than anything the VM makes,
        # so it goes on the end rather than being dropped -- the theme-matched
        # shot still leads, because that is the one that answers "what will
        # this look like on my phone".
        if app.screenshot and app.screenshot not in order:
            order.append(app.screenshot)
        return order

    def _show_screenshot(self, index: int, path) -> bool:
        try:
            self.shot_pictures[index].set_filename(str(path))
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
    def _chip(label: str, css: str) -> Gtk.Widget:
        chip = Gtk.Label(label=label)
        chip.add_css_class("meta-chip")
        if css:
            chip.add_css_class(css)
        chip.set_ellipsize(Pango.EllipsizeMode.END)
        chip.set_max_width_chars(30)
        return chip

    @staticmethod
    def _chips(app: App) -> list[tuple[str, str]]:
        """Category, what it is built with, and whether anyone has run it.

        Deliberately short forms: the Details list below still spells each one
        out, so a chip only has to be recognisable, not complete.
        """
        toolkit = {
            "libadwaita": "libadwaita",
            "kirigami": "Kirigami",
            "quickshell": "Shell plugin",
            "tui": "Terminal",
            "gtk": "GTK",
            "qt": "Qt",
        }.get(app.toolkit, app.toolkit)

        category = f"{app.category} · {app.subcategory}" if app.subcategory else app.category
        chips = [(category, "")]
        if toolkit:
            chips.append((toolkit, ""))

        # The exception, never the rule. 81 of the catalogue's apps fit 360px
        # and 66 follow the theme, because that is what being in the catalogue
        # means -- so a chip saying "Fits" would appear almost everywhere and
        # tell nobody anything, while crowding a 360px page that already
        # carries three. A chip earns its place by warning you.
        if app.adaptive == "tight":
            chips.append(("Cramped at 360px", ""))
        elif app.adaptive in ("clipped", "no-window"):
            # Plain, not the dim "untested" style it started as. Rendered, that
            # made the most important chip on the page the faintest one --
            # quieter than "Kirigami" and far quieter than the green tested
            # chip beside it, which is backwards for the only chip warning you
            # the thing does not fit.
            chips.append(("Does not fit 360px", ""))
        if app.themed == "no":
            chips.append(("Keeps its own colours", ""))
        elif app.themed == "partial":
            chips.append(("Themed in part", ""))

        chips.append(
            (f"Tested on {app.tested}", "verified")
            if app.verified
            else ("Not yet tested", "untested")
        )
        return chips

    @staticmethod
    def _cost_label(app: App) -> str:
        packages = "1 package" if app.cost_pkgs == 1 else f"{app.cost_pkgs} packages"
        if not app.cost_mb:
            return packages
        # %g so 3.15 stays 3.15 and 26.0 reads as 26.
        return f"{packages}, {app.cost_mb:g} MB"

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

        # Hidden rather than insensitive when there is nothing to open: a
        # greyed-out Open on a terminal app invites a tap and explains nothing.
        # With it hidden the box is one button wide, which homogeneous sizing
        # turns back into a full-width pill on its own.
        self.open_button.set_visible(launcher.can_open(self.app))

    def _on_open(self, _button) -> None:
        ok, message = launcher.open_app(self.app)
        if not ok:
            self._say(message)

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
            # Resolved here because only the UI has a display to ask.
            icon=icon_name_for(self.app),
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


class CategoryPage(Adw.NavigationPage):
    """Every app in one category.

    A page of its own rather than a filter on the front page: at 360px,
    browsing is choosing a shelf and then reading it, and the back gesture is
    already how everything else in this window is left.
    """

    def __init__(self, category: str, window: "StoreWindow"):
        super().__init__(title=category)
        self.category = category
        self.window = window

        # The page title alone. A count of what you have already installed is
        # a number to read before you can start looking, and the grid says the
        # same thing in the corner of every card that has a tick on it.
        header = Adw.HeaderBar()

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        clamp = Adw.Clamp(maximum_size=640)
        clamp.set_child(self.list_box)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(clamp)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        self.set_child(toolbar)

        self.reload()

    def reload(self) -> None:
        """Rebuild from the window's current list of apps.

        Called again after an install finishes, because this page is still on
        the stack underneath the detail page that did it -- popping back to a
        card with no tick on it would be the store lying about the one thing it
        reads off the system.
        """
        empty(self.list_box)
        # No group title: the header bar already says which category this is,
        # and repeating it immediately below costs a line of a screen that has
        # 720 of them.
        self.list_box.append(
            app_group("", self.window.apps_in(self.category), self.window.open_detail)
        )


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

        # The category page currently on the stack, if any, so an install done
        # from underneath it can put the tick on its row too.
        self.category_page: CategoryPage | None = None
        self.nav.connect("popped", self._on_popped)

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
                    GLib.idle_add(self.open_detail, app)
                    break

        # MOARCHY_STORE_CATEGORY=<name> does the same for a category page.
        wanted = os.environ.get("MOARCHY_STORE_CATEGORY")
        if wanted:
            GLib.idle_add(self._open_category, wanted)

        # Do not let the search entry take focus at startup. squeekboard raises
        # itself whenever a text field is focused, so an autofocused search box
        # means the keyboard covers half the catalogue before you have looked at
        # it. Focus lands on the search entry when the user taps it, which is
        # when they actually want to type.
        GLib.idle_add(self._drop_focus)

    def _on_popped(self, _nav, page) -> None:
        if page is self.category_page:
            self.category_page = None

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
        if self.category_page is not None:
            self.category_page.reload()

    def _show_error(self, message: str) -> None:
        empty(self.list_box)
        self.list_box.append(
            Adw.StatusPage(
                title="No catalogue",
                description=message,
                icon_name="dialog-warning-symbolic",
            )
        )

    def _populate(self) -> None:
        empty(self.list_box)

        needle = self.search.get_text().strip().lower()
        if not needle:
            self._browse()
            return

        shown = [
            a
            for a in self.apps
            if needle in a.name.lower()
            or needle in a.ident.lower()
            or needle in a.summary.lower()
            or needle in a.category.lower()
            or needle in a.subcategory.lower()
            or needle in a.subtitle.lower()
            # Typing "matrix" and finding Fractal is the point of recording
            # protocols at all. Description is deliberately not searched: it is
            # a paragraph, and matching inside one returns everything.
            or any(needle in f.lower() for f in a.features)
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
            self.list_box.append(app_group(category, apps, self.open_detail))

    def _browse(self) -> None:
        """The front page with nothing typed: what there is, then what to
        start with.

        A grid rather than the catalogue itself. 109 rows in one column is a
        list you scroll past rather than read, and it opened on Chat every
        time because C sorts early -- so the first screen is fourteen ways in
        and nothing else, and the shelf under it answers the question someone
        arriving actually has, which is not "which category" but "what should
        I install".

        The category grid carries no heading. A heading earns its place by
        telling one group of things apart from another, and this is the first
        thing on the page: fourteen tinted glyphs with a word under each are
        not going to be mistaken for anything, and "Categories" cost a line of
        a 720px screen to say so.
        """
        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_homogeneous(True)
        # Three across on a phone, four on a desktop window. Both ends are set:
        # left alone a FlowBox fits as many as it can, which at 360px means
        # four cramped tiles the moment a category name is short enough to
        # ellipsise away to nothing.
        grid.set_min_children_per_line(3)
        grid.set_max_children_per_line(4)
        grid.set_row_spacing(8)
        grid.set_column_spacing(8)

        for category in by_category(self.apps):
            grid.append(
                category_tile(category, lambda c=category: self._open_category(c))
            )

        categories = Adw.PreferencesGroup()
        categories.set_margin_start(12)
        categories.set_margin_end(12)
        categories.set_margin_top(6)
        categories.add(grid)
        self.list_box.append(categories)

        picks = [a for a in self.apps if a.featured]
        if not picks:
            return

        choice = Adw.PreferencesGroup(title="Editor's Choice")
        # Every word of this is a claim the catalogue can be checked against --
        # see the featured comment in catalogue.toml, and the check in
        # scripts/lint-catalogue.py that holds the shelf to it.
        choice.set_description(
            "Start here. Each one was measured at 360px, follows your theme, "
            "and costs a few megabytes."
        )
        choice.set_margin_start(12)
        choice.set_margin_end(12)
        choice.set_margin_top(12)
        choice.set_margin_bottom(12)
        # The same grid the categories use, rather than the wide summary cards
        # this shelf used to be. The summary was there because a row of icons
        # would have been the one surface in the store saying "good" without
        # saying why -- a screenshot says considerably more than that summary
        # did, and the group description above still carries the claim the
        # shelf is making.
        choice.add(app_grid(picks, self.open_detail))
        self.list_box.append(choice)

    def apps_in(self, category: str) -> list[App]:
        """One category's apps, by name."""
        return sorted(
            (a for a in self.apps if a.category == category),
            key=lambda a: a.name.lower(),
        )

    def _open_category(self, category: str) -> None:
        self.category_page = CategoryPage(category, self)
        self.nav.push(self.category_page)

    def open_detail(self, app: App) -> None:
        self.nav.push(DetailPage(app, self.refresh))
