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

## Running it again

This is meant to be run periodically, and everything about finding *new* apps
rather than re-reading old ones is automatic. Nothing needs remembering between
runs except this section.

**Already-answered apps are skipped.** `listed` and `rejected` verdicts, and
anything in `catalogue.toml`, never appear as candidates again. That is what
`sweep/verdicts.toml` is for: 173 records so far, and the expensive half of a
sweep is discovering that an app does not work, which should be paid once.

**Deferred apps come back.** `deferred` is not an answer -- it means "worth
another look when something changes" -- so it is deliberately *not* in the skip
set. It resurfaces after 90 days, and `--deferred` lists them with their
reasons at any time. This was wrong at first: all three scripts skipped every
verdict whatever its outcome, which quietly buried 36 apps, several of them one
fix from listable.

**The data refreshes itself.** The AppStream, AUR and LinuxPhoneApps catalogues
are cached under `~/.cache/moarchy-store-sweep` and re-fetched once they are
over a week old. Every run prints how old the copy is, on stderr, because a
sweep that silently reads a month-old snapshot and reports nothing new looks
exactly like one that read today's and found nothing new. `--refresh` forces it.

So a periodic run is:

```bash
./scripts/sweep-lpa.py                            # new, rated, packaged in Arch
./scripts/sweep-discover.py --table               # new, declaring 360 and touch
python3 scripts/lint-catalogue.py --check-repos   # what has LEFT the repos
```

The third is the one people forget, and it is not optional. Packages disappear
quietly: four entries -- plasma-dialer, spacebar, portfolio-file-manager, livi
-- sat in the catalogue after Arch Linux ARM stopped building them for aarch64,
each drawn as an ordinary row with an Install button that could not work. One
carried a screenshot taken on real hardware. Nothing else notices.

Run `sweep-aur.py` occasionally rather than every time. It cannot produce a
listable entry, and its value is the side effect: it finds repo packages that
ship no AppStream metainfo, which is the one gap `sweep-discover.py` cannot see
past.

If a run finds nothing, check the age lines before concluding the repos are
quiet.

## Re-measuring what is already listed

A sweep adds apps. This is the other half: entries already in the catalogue
that predate the harness and are therefore missing everything it produces.

```bash
python3 scripts/lint-catalogue.py        # says how many, and names three
```

The test is the absence of `measured`, which only `sweep-measure.sh` writes. An
entry can have `tested = "pinephone-a64"` and a screenshot and still be on the
list: the first sixteen shots were taken on a phone by hand, before any of this
existed, so those entries carry a picture and a device string and no `themed`,
no `adaptive`, no install cost. They are invisible to every chip and row the
store learned to show — they look finished and are not.

Two groups, and they want different things:

- **14 with an old-style `<pkg>.png`.** Re-run `sweep-measure.sh` to get the
  fields and a dark/light pair. **Do not delete the old picture.** For the
  PinePhone ones it is the best evidence this project has, and better than
  anything the VM can produce. Leave `screenshot` pointing at it: the detail
  page shows the theme-matched VM shot first and keeps the hardware one at the
  end of the carousel, so nothing is lost.
- **12 with no screenshot at all.** Four are TUIs, which have no window to
  photograph and are honestly served by the terminal icon. Two are plugins. The
  rest — mpv, qrca, snapshot, audiotube, gnome-calls — could not be measured for
  reasons recorded in `sweep/verdicts.toml`, mostly a camera or a modem the VM
  has not got.

So the re-measure batch is the first group, and it is the same loop as any
other batch: cost the whole set in one pass, measure each, read both shots,
write the fields. The only difference is that `catalogue.toml` already has the
entry, so only `metadata.toml` gains anything, and `tested` keeps whatever it
says — a VM run does not overwrite a hardware claim.

## The loop

One batch is 8–15 apps and ends in one commit. Do not start a second batch
before the first is committed.

```
discover -> triage -> measure -> judge -> write -> review -> commit
```

### 1. Discover

Three sources, in increasing order of how much they already know, and the third
is the one to start with:

```bash
./scripts/sweep-lpa.py                  # curated, rated, and joined to packages
./scripts/sweep-discover.py --table     # what upstream claims about itself
./scripts/sweep-aur.py                  # inference, for the AUR
```

**`sweep-lpa.py` first.** linuxphoneapps.org rates apps for mobile
compatibility -- 5 means "fits the screen and works fine with touch input" --
and records which distributions package them. It publishes a conclusion, where
the other two publish a claim and a guess. 795 apps, 219 packaged in Arch.

Its rating is not evidence for this device, and the script says so in its
docstring. 5 there means it fits some phone, at some width, usually under Phosh
or Plasma Mobile, and this runs neither. KleverNotes is rated 5 and clips at
360x674 here. It produces a queue, never an entry.

**`sweep-discover.py`** reads `<display_length>` and `<control>touch</control>`
out of the repo's own AppStream catalogue: upstream's claim about itself, for
every packaged app, fetched to `~/.cache` so it needs no VM.

Once a batch is chosen, `./scripts/sweep-backfill.py` fills the four fields
upstream already publishes -- subtitle, description, homepage, released -- for
every catalogued app that lacks them. It never overwrites, because the fields
worth writing by hand are exactly the ones it would trample: `description`
especially, which arrives as desktop marketing and has to be rewritten as an
opinion about using the thing with a thumb.

**`sweep-aur.py`** infers from dependencies and description, because the AUR
ships no AppStream data at all. Weakest of the three, and the one that found
the most, though not in the way it was meant to: 51 of its 277 candidates
turned out to be **repo** packages that `sweep-discover.py` could not see,
because they ship no metainfo. Komikku, Marknote and Filelight came from there.

That blind spot is worth walking deliberately. `sweep-lpa.py` prints how many
rated apps it could not join to a package, and `--json` lists them; they are
where the misses hide.

**Never sweep the AUR for things to list.** `data/moarchy-store-helper` execs
`/usr/bin/pacman -S`, so an AUR package cannot be installed through the store
and the allowlist cannot cover it. An AUR entry would be a row with a button
that cannot work. Surveying it is still worth doing -- it finds repo packages,
and it records what exists -- but the output goes to `sweep/verdicts.toml`.

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

### 2a. Seed the guest, once

```bash
./scripts/sweep-seed.sh          # generated fixtures, idempotent
./scripts/sweep-seed.sh --list   # what is there
```

A third of the catalogue reads a standard directory and shows what is in it.
On a fresh image those directories are empty, so the shots come out as "No
Books Yet", "Add some books", "Get Some Shows", "Drop images here to compress
them" -- pictures of an app nobody has used, which sell nothing. Someone
deciding whether Amberol is worth 1.8 MB learns more from a library with albums
in it than from a button marked Add Folder.

Everything is **generated in the guest**, by ffmpeg and ImageMagick, from
nothing. That is a licensing decision, not a technical one: seeding real media
means files whose rights have to be checked and re-checked by whoever touches
this next, and generating means there is nothing to check.

The metadata is real, because metadata is the part a screenshot shows. A music
library displays composer, album, track and cover art and never the waveform,
so the tags name actual public-domain compositions while the audio is a quiet
tone -- and every file says so in its comment tag, so nobody mistakes it for a
recording. Same for the text: Sonnet 18 and the opening of Alice are public
domain and are quoted as themselves.

Run it once per image. It is idempotent and skips if the stamp file is there;
`--force` redoes it.

### 3. Cost the whole queue, then measure

```bash
./scripts/sweep-cost.sh --queue sweep/queue.txt     # all of them, one pass
./scripts/sweep-measure.sh foliate                  # then one at a time
```

Cost first, and for the whole batch, because install cost is *relative to what
is already installed*. Measured serially it drifts: klevernotes cost 38
packages on a fresh image and 25 an hour later, the same app on the same day,
because other apps had been installed and removed in between. Numbers taken one
at a time cannot be compared with each other, and comparing them is the entire
point -- Tuba is Mastodon for 1.57 MB where Tokodon is 108 MB.

`sweep-measure.sh` then handles one app: install, resolve the desktop id, launch
under the dark theme, wait for a window, shoot, switch to light, **relaunch**,
shoot, diff, uninstall. It emits JSON and writes
`screenshots/<pkg>-dark.png`, plus `-light.png` when the app actually
recoloured. `WAIT=180` for something known to be slow.

It returns `adaptive: "mapped"` or `"no-window"` -- never `"fits"`. That word is
yours, in step 4.

Four things it guards, each of which cost an hour to find:

- **The keyboard.** `reserved` has been `[0,26,0,0]`, `[0,26,0,20]` and
  `[0,26,0,220]` on the same guest within minutes. The last is squeekboard up,
  and the 360x474 window it leaves still photographs like a plausible phone app.
  An app that focuses a text field on startup raises it *after* the window maps,
  so the script lowers it and waits for the window to grow back before shooting.
- **The relaunch after a theme switch.** GTK parses the user stylesheet once at
  process start. The process is killed by pid, not asked to close: `hyprctl
  dispatch closewindow` is rejected outright by this Hyprland, which wants the
  lua form, and it failed silently -- so nothing was relaunched and Foliate
  measured 0.045 (`partial`) instead of 0.80 (`yes`).
- **Both shots being the same shape.** If they are not, no diff is taken at all.
  KleverNotes measured `themed` at 0.23 with pixels identical between the two
  themes, purely because one shot was 474 tall and the other 674 and a single
  crop was applied to both.
- **Windows it cannot name.** It counts windows before launching and waits for a
  new one, rather than matching a class guessed from the desktop id. Railway's
  entry is `de.schmidhuberj.DieBahn`, Collision maps as `dev.geopjr.Collision`,
  SongRec as `re.fossplant.songrec`; all three were recorded "no-window" with
  their windows on screen.

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

```bash
./scripts/sweep-record.py --from /tmp/measured --adaptive fits kalk loupe
```

Merges the harness output into `metadata.toml` -- cost, theme diff, shots --
and leaves everything else in the table alone.

It refuses to write `adaptive` unless you pass it, which is the whole point.
The harness reports `mapped`, never `fits`, so an entry stays unjudged until a
person has looked at the picture and said so. It also never touches `tested`: a
VM run does not overwrite a hardware claim.

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
featured = true                                # optional, and see rule 6
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
6. **`featured` is not a sweep's to give.** It puts the app on the front page's
   Editor's Choice shelf, under a line promising every pick was measured at
   360px, follows your theme and costs a few megabytes — so the store says
   *start here* about it to everybody who opens the app. `lint-catalogue.py`
   enforces the measurable half (`fits`, `themed = "yes"`, a `tested` device);
   the rest is judgement, the shelf is meant to stay around ten, and it is one
   app per job — two music players on it would mean neither was the answer.
   Propose it in the PR, do not add it because an entry qualifies.

### 6. Review, then commit

```bash
./scripts/sweep-shots.sh                       # pngquant + oxipng over new shots
python3 scripts/lint-catalogue.py              # must be 0 errors
python3 scripts/lint-catalogue.py --check-repos  # before every publish
```

`--check-repos` asks the guest whether every catalogued package still exists,
and it is not a formality: it found four entries -- plasma-dialer, spacebar,
portfolio-file-manager and livi -- that Arch Linux ARM had stopped building for
aarch64, each rendering as an ordinary row with an Install button that could not
work. One of them carried a hardware screenshot. Packages leave the repos
quietly, so run it before every publish, not only when adding apps.

Then bump `serial` in `catalogue.toml`, sign, and commit. Bump it whenever the
file changes at all, not only when apps are added: `remote.py` refuses a
*lower* serial, which stops a replayed old catalogue, but nothing stops two
different catalogues sharing one. Edit a summary, re-sign, publish, and every
client holding that serial has different bytes under the same name.
`lint-catalogue.py` compares against `origin/main` and errors if the file
changed and the serial did not.

```bash
./scripts/sign-catalogue.sh
```

One commit per batch, in the repo's voice. The commit message is where the
findings live that no field can hold — read `git show 29f9444` for the model.
Record what was measured, what broke and why, and **what was rejected**. The
rejections are the part that stops the next sweep re-testing the same thing.

## State

- `sweep/verdicts.toml` — **committed, and load-bearing.** One record per app
  ever examined, including everything rejected. It is the lab notebook, and it
  is also what makes a periodic sweep cheap: the scripts read it to decide what
  not to offer again.

  ```toml
  [gnome-podcasts]
  date    = 2026-09-13
  outcome = "listed"        # listed | rejected | deferred
  reason  = ""              # required when rejected
  ```

  `outcome` is not decoration either. `listed` and `rejected` are skipped for
  good; `deferred` comes back after 90 days. So choose it honestly: a `rejected`
  you meant as "not yet" is an app nobody will ever look at again, and a
  `deferred` you meant as "no" is one that will keep asking.

  `lint-catalogue.py` checks the notebook and the catalogue agree about what
  shipped, because a notebook that disagrees is worse than none.

- `~/.cache/moarchy-store-sweep/` — the three upstream catalogues, re-fetched
  when over a week old. Not committed, and safe to delete.

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
