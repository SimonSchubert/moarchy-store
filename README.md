# moarchy-store

A curated store of Linux phone apps that **actually fit a small screen**.

`pacman -Ss` already lists every package. What it cannot tell you is which of
them survive 360×720 logical pixels and 2 GB of RAM. That judgement is the whole
point of this app.

Built for [mobileomarchy](https://github.com/SimonSchubert/mobileomarchy) on a
PinePhone, but nothing in it is specific to that: it is useful on Phosh, Plasma
Mobile, postmarketOS, or any Arch-based phone.

## What it does

- Browse ~20 curated apps by category, with a note on *why* each one works
- See installed state, version and download size, read live from pacman
- Install and remove, from the app, by touch
- Marks which entries were verified on real hardware and which are suggestions

Apps are grouped by the toolkit that makes them adaptive — **libadwaita** (GNOME)
and **Kirigami** (Plasma Mobile) are the two families designed for phone widths.
Kirigami apps run fine as ordinary Wayland clients; no KDE session needed.

## Install

```bash
yay -S moarchy-store-git
```

## The catalogue is the allowlist

Installing packages needs root, which is a genuine privilege surface. The design:

- The polkit action does **not** point at pacman. It points at
  `/usr/lib/moarchy-store/moarchy-store-helper`.
- The helper refuses any package not named in
  `/usr/share/moarchy-store/catalogue.toml`, and refuses to run at all if that
  file is not root-owned and non-world-writable.
- Package names are validated against `^[a-z0-9][a-z0-9@._+-]*$` before use, and
  pacman is exec'd with an argument list — never a shell.

So authenticating grants *"install one of these curated apps"*, not *"install
anything"*. If the action pointed at pacman directly, one authentication would
let a user install a package with a setuid binary or an install hook — a
straightforward local privilege escalation.

The consequence, deliberately accepted: **adding an installable app requires a
package update**, because it means editing a root-owned file. A catalogue
fetched over the network could not be trusted as an allowlist.

## Adding an app

Send a PR editing `catalogue.toml`:

```toml
[[app]]
pkg      = "foliate"
name     = "Foliate"
category = "Reading"
toolkit  = "libadwaita"   # libadwaita | kirigami | gtk | qt | tui
summary  = "E-book reader. One of the best things about having a Linux phone."
tested   = "pinephone-a64"   # or "" if you have not run it on a device
```

`tested` is not decoration. An empty value renders as *"Not yet tested on a
device"* — a suggestion. A device string is a claim someone can hold you to.

## Requirements

`python`, `python-gobject`, `gtk4`, `libadwaita`, `polkit`, `pacman` — all of
which a GNOME-adjacent phone image already has.

## License

MIT
