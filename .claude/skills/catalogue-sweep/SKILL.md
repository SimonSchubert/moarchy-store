---
name: catalogue-sweep
description: >
  Grow moarchy-store's catalogue one reviewed batch at a time: find likely
  phone apps from AppStream, run each one at 360px in the omarchy-mobile VM,
  screenshot it in a dark and a light theme, judge whether it really fits, and
  write catalogue.toml + metadata.toml entries with the evidence. Use when the
  user asks to add apps to the catalogue, sweep for new phone apps, measure or
  screenshot an app in the VM, backfill catalogue metadata, or run
  /catalogue-sweep.
---

# Sweeping the catalogue

`pacman -Ss` already lists every package. What it cannot say is which ones
survive 360×720 logical pixels, and that judgement is the entire product. This
skill systematises what commit `29f9444` did by hand: pick candidates, run them
at phone size, shoot them, and write down what broke.

It is deliberately not a scraper. **Every entry that reaches `catalogue.toml`
has had a person look at a picture of the app running.** The scripts do the
mechanical half so that the judgement half is the only thing left to do.

## Before you start

- The VM lives in a sibling checkout, `~/Projects/omarchy-mobile`. Override
  with `MOARCHY_VM`. If it is not there, stop and say so.
- **Take the lease, and never force it.** One VM, one session:
  ```bash
  cd ~/Projects/omarchy-mobile && ./scripts/vm-lease.sh take "catalogue sweep"
  ```
  If somebody holds it, run `./scripts/vm-lease.sh wait` in the background and
  do the non-VM work meanwhile. `take --force` steals the VM from a session
  that is mid-drag. Do not.
- The guest needs the repo-wide AppStream database once:
  ```bash
  ./scripts/vm-ssh.sh 'sudo pacman -S --needed --noconfirm archlinux-appstream-data'
  ```
- A sweep wants a pristine image, so prefer `./scripts/vm-run.sh --snapshot`.
  `vm-run.sh` refuses to restart a running VM on purpose — the window on the
  Mac is somebody's. If a normal VM is already up, **ask** before powering it
  down; do not decide for them. Using it as-is is fine, but say so in the
  record, because "does it install on the shipped image" stops being an honest
  question once the image has been written to.

## The loop

One batch is 8–15 apps and ends in one commit. Do not start a second batch
before the first is committed.

```
discover -> triage -> measure -> judge -> write -> review -> commit
```

### 1. Discover

```bash
./scripts/sweep-discover.sh --table      # readable
./scripts/sweep-discover.sh > /tmp/candidates.json
```

This reads the whole repo's AppStream data in the guest and ranks by what
*upstream itself* claims: a `<display_length>` of 360 or less, a
`<control>touch</control>`, and a recent `<release>`. It already drops
everything in `catalogue.toml`.

**Never sweep the AUR**, and the reason is structural rather than aesthetic:
`data/moarchy-store-helper` execs `/usr/bin/pacman -S`, so an AUR package
cannot be installed through the store at all. An AUR entry would be a row with
a button that cannot work.

Cross-check against the curated lists when the AppStream yield thins out —
LinuxPhoneApps.org (open data, fetch the source rather than scraping),
apps.gnome.org, the KDE Plasma Mobile app list, the postmarketOS wiki. Take
their names and intersect with `pacman -Sl`; the intersection is the point.

### 2. Triage

Drop, without spending VM time:

- already in `catalogue.toml` or already in `sweep/verdicts.toml`
- `min_width > 360` — upstream says it does not fit, believe it
- no release in three years, unless nothing else fills the category
- desktop-shaped whatever it claims: IDEs, DAWs, VM managers, disk partitioners

Then look at what the catalogue is *missing* rather than what the sweep found.
Walk the things a phone is for — dialer, SMS, contacts, calendar, camera,
gallery, maps, browser, mail, podcasts, music, files, notes, weather, clock,
calculator, OTP, transit — and for each ask what the best aarch64 answer is.
"There isn't one" is a finding; record it.

### 3. Measure

```bash
./scripts/sweep-measure.sh foliate
```

Per app: reads install cost *before* installing anything, installs, resolves
the desktop id, launches under the dark theme, waits for a window, shoots,
switches to the light theme, **relaunches**, shoots again, diffs the pair, and
removes the package.

It emits JSON and writes `screenshots/<pkg>-dark.png` (and `-light.png` when
the app actually recoloured). It returns `adaptive: "mapped"` or
`"no-window"` — never `"fits"`. That word is yours to write, in step 4.

Three things the script guards that are easy to lose by hand:

- **The keyboard.** `reserved` has been `[0,26,0,0]`, `[0,26,0,20]` and
  `[0,26,0,220]` on the same guest within minutes. The last is squeekboard up,
  and the resulting 360×474 window still photographs like a plausible phone
  app. Every shot asserts the bottom reservation is ≤ 20.
- **The relaunch after a theme switch.** GTK parses the user stylesheet once at
  process start; that is why omarchy's own hook kills the
  `--gapplication-service` daemons. Shooting without relaunching photographs
  the old palette and reports a perfectly themed app as ignoring the theme.
- **A launch that maps nothing.** `gtk-launch` exits 0 with no process and no
  window when the entry is `DBusActivatable` and its unit wants a GNOME
  session. Exit status proves nothing; the client list does. The script
  captures the log, the journal, and the `DBusActivatable` line so the failure
  is diagnosable rather than just recorded.

### 4. Judge — the part that is not automatable

**Read both screenshots.** Actually open them. Then decide `adaptive`:

| value | means |
|---|---|
| `fits` | header bar complete, nothing clipped, every primary control reachable |
| `tight` | usable, but something is cramped — a dialog overflows, a column is cut |
| `clipped` | content cut off or controls unreachable at 360 |
| `no-window` | never launched, or launched and mapped nothing |

Hyprland tiles, so it *forces* 360×674 onto an app that cannot cope. A clipped
app reports exactly the same geometry as a perfect one. **The geometry is a
precondition, never evidence. The picture is the evidence.**

Look for: buttons pushed off the right edge; text clipped mid-word; a popover
wider than the window; horizontal scroll where there should be none; touch
targets under 44 logical px (88 device px at 2×, per moarchy `docs/style.md`
§H). And ask whether the shot shows the app *doing its job* or just a login
wall — if it is a login wall, say so in `measured`, because a screenshot of an
empty state sells nothing.

`themed` comes from the RMSE diff, but sanity-check it. An app showing a clock,
a map or a photo differs between two runs for reasons that have nothing to do
with theming. Kirigami and Qt apps structurally cannot follow the theme — the
image ships `gtk.css.tpl` and `gtk3.css.tpl` and nothing for Qt — so `no` is
the expected answer there and `yes` would mean the measurement is wrong.

### 5. Write

`catalogue.toml` — the allowlist, and only these fields:

```toml
[[app]]
pkg      = "foliate"
name     = "Foliate"
category = "Reading"
toolkit  = "libadwaita"
summary  = "One line on why it works on a phone. Ours, not upstream's."
icon     = "com.github.johnfactotum.Foliate"   # from its own .desktop, not guessed
screenshot = "foliate-dark.png"
tested   = "omarchy-mobile-vm"
```

`metadata.toml` — everything else, keyed by ident. See its header for the
field list and what produces each.

Rules that are not negotiable:

1. **`tested` is never written by an unreviewed pass.** A measurement writes
   `sweep/verdicts.toml`. Promotion into `catalogue.toml` requires that you
   have read the screenshots and said what you saw.
2. **`tested` is `omarchy-mobile-vm`, never a device string.** These ran on an
   emulated panel; the chip should say so. A device name is a claim someone can
   hold us to, and nobody held the phone.
3. **An entry with a verdict and no screenshot is illegal.** A screenshot with
   `adaptive = ""` is fine and means "we have a picture, nobody has judged it".
4. **Descriptions are ours.** AppStream's `<description>` is a draft to rewrite:
   it is desktop marketing, it is under upstream's licence, and the opinion is
   the product. `subtitle` may be upstream's `<summary>` verbatim — it is
   labelled as what the app calls itself.
5. **`summary` stays one line about the phone.** Not what the app is — what it
   is like to use with a thumb, and what it costs.

### 6. Review, then commit

```bash
./scripts/sweep-shots.sh                  # pngquant + oxipng over new shots
python3 scripts/lint-catalogue.py         # must be 0 errors
```

Then bump `serial` in `catalogue.toml`, sign, and commit:

```bash
./scripts/sign-catalogue.sh
```

One commit per batch, in the repo's voice. The commit message is where the
findings live that no field can hold — read `git show 29f9444` for the model.
Record what was measured, what broke and why, and **what was rejected**. The
rejections are the part that stops the next sweep re-testing the same thing.

## State

- `sweep/verdicts.toml` — **committed.** One record per app ever examined,
  including everything rejected. This is the lab notebook; `catalogue.toml`
  already carries a hand-written version of it in the plugin-sweep comment, and
  this generalises it.
  ```toml
  [gnome-podcasts]
  date    = 2026-09-13
  outcome = "listed"        # listed | rejected | deferred
  adaptive = "fits"
  reason  = ""              # required when rejected
  ```
- `/tmp/candidates.json` — scratch, per batch. Not committed.

## When it goes wrong

| symptom | cause |
|---|---|
| `HYPRLAND_INSTANCE_SIGNATURE not set` | an ssh session has no session env; the scripts rebuild it, a hand-run command must too |
| `Theme 'tokyo-night' does not exist` | `OMARCHY_PATH` is unset over ssh, so omarchy-theme-set looks in `/themes`. Export `OMARCHY_PATH=/usr/share/omarchy`. The message names the theme, not the variable, which is why this costs an hour the first time |
| every app reports `themed = yes` | the crop is missing, so the diff is reading the shell's top bar |
| every app reports `themed = no` | the relaunch after the theme switch was skipped |
| shots are 360×474 | squeekboard was up. The script lowers it (`busctl --user call sm.puri.OSK0 /sm/puri/OSK0 sm.puri.OSK0 SetVisible b false`) and re-checks before giving up |
| `grim produced nothing` | the session is down — `./scripts/vm-ssh.sh hyprctl monitors` |
| the lease refuses | somebody is using it. Wait. Do not force. |
| a command after a `pkill -f` silently does not run | `-f` matches the whole command line, and the first match is the ssh invocation itself -- its `-c` string contains the pattern. `pkill -f moarchy-store` kills the shell running it. Use `pkill -x` (process name), or kill the pid `hyprctl -j clients` reports |
| the store's own detail page will not scroll | `vm-drag.sh` makes a *pointer*, and a pointer drag does not scroll a `GtkScrolledWindow` -- only touch or a wheel does. The device needs `REL_WHEEL` |
| a shot has the mouse pointer in it | known, and the existing catalogue shots have it too. `hyprctl keyword`/`eval cursor:invisible` are both refused by this Hyprland's lua parser; nobody has found the right incantation yet |
