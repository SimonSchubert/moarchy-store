#!/usr/bin/env bash
# Run one app in the VM at phone size and report what happened.
#
#   ./scripts/sweep-measure.sh foliate
#   ./scripts/sweep-measure.sh --keep foliate     # leave it installed
#
# Emits one JSON record on stdout and writes two screenshots into
# screenshots/. It decides nothing: `adaptive` comes back as "mapped" at best,
# because whether an app FITS 360px is a question about pixels that only
# someone looking at the picture can answer. Hyprland tiles, so it forces
# 360x674 onto an app that cannot cope -- a clipped app reports exactly the
# same geometry as a perfect one. The window size is a precondition, never
# evidence. See .claude/skills/catalogue-sweep for the part that is judgement.
#
# What this script is for is the half that IS mechanical, and that is easy to
# get wrong by hand:
#
#   * cost is read before anything is installed, because the cost of an app is
#     relative to what is already there and measuring app 12 after installing
#     apps 1-11 reports a number that is true of nothing;
#   * the keyboard is proven down before every shot. `reserved` has been seen
#     as [0,26,0,0], [0,26,0,20] and [0,26,0,220] on the same guest within
#     minutes; the last is squeekboard up, and it makes a 360x474 window that
#     still photographs like a plausible phone app;
#   * the app is RELAUNCHED after the theme switch. GTK parses the user
#     stylesheet once at process start, which is why omarchy's own theme hook
#     kills the --gapplication-service daemons. Shooting without relaunching
#     photographs the old palette and reports a perfectly themed app as
#     ignoring the theme;
#   * a launch that produces no window is interrogated rather than retried.
#     gtk-launch exits 0 with no process and no window when the entry is
#     DBusActivatable and its unit wants a GNOME session -- which is exactly
#     what Chatty does, and what serial 7 found by hand.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO_ROOT"

VM=${MOARCHY_VM:-$HOME/Projects/omarchy-mobile}
SSH="$VM/scripts/vm-ssh.sh"
[ -x "$SSH" ] || { echo "!! no VM at $VM -- set MOARCHY_VM" >&2; exit 1; }

# How long to wait for a window, in seconds. Raise it for an app that is known
# to be slow rather than raising it for everyone: most map in under ten.
WAIT=${WAIT:-60}
DARK_THEME=${DARK_THEME:-tokyo-night}
LIGHT_THEME=${LIGHT_THEME:-catppuccin-latte}
KEEP=0
[ "${1:-}" = "--keep" ] && { KEEP=1; shift; }
PKG=${1:?usage: sweep-measure.sh [--keep] <pkg>}

# An ssh session has none of the session's environment; every guest command
# that talks to the compositor has to rebuild it. OMARCHY_PATH is in here for
# the same reason and is easy to miss: omarchy-theme-set looks for the theme
# under $OMARCHY_PATH/themes, so without it every theme "does not exist" and
# the failure names the theme rather than the variable.
E='export XDG_RUNTIME_DIR=/run/user/$(id -u);
   export OMARCHY_PATH=/usr/share/omarchy;
   export HYPRLAND_INSTANCE_SIGNATURE=$(ls -t $XDG_RUNTIME_DIR/hypr | head -1);
   export WAYLAND_DISPLAY=$(basename $(ls -t $XDG_RUNTIME_DIR/wayland-[0-9]* | grep -v "\.lock$" | head -1));'

note() { printf '\033[2m--   %s\033[0m\n' "$*" >&2; }
fail() { printf '\033[31m!! %s\033[0m\n' "$*" >&2; }

json_escape() { python3 -c 'import json,sys;print(json.dumps(sys.stdin.read().strip()))'; }

# A guest with an empty home photographs as an empty app: "No Books Yet", "Add
# some books", "Drop images here". Say so once rather than let a batch of
# screenshots come out useless.
if ! "$SSH" "test -f ~/.local/share/moarchy-fixtures.txt" 2>/dev/null; then
  note "the guest has no fixtures -- shots of library apps will be empty states"
  note "run ./scripts/sweep-seed.sh first"
fi

# --- 0. cost, before a single package lands ---------------------------------
note "cost of $PKG, on the image as it stands"
# The package list comes from pacman, the sizes from expac. NOT from pacman's
# own %s: that is what would have to be *downloaded*, so it reports 0 for
# anything already sitting in /var/cache/pacman -- which is every package a
# previous sweep touched. Cost would then quietly shrink as the batch went on.
# expac reads the sync database, so it says the same thing on every run.
COST=$("$SSH" "pacman -S --print --print-format '%n' -- $PKG 2>/dev/null" || true)
COST_PKGS=$(printf '%s\n' "$COST" | grep -c . || true)
COST_BYTES=0
if [ "$COST_PKGS" -gt 0 ]; then
  COST_BYTES=$("$SSH" "expac -S '%k' -- $(printf '%s' "$COST" | tr '\n' ' ') 2>/dev/null" \
               | awk '{s+=$1} END{print s+0}')
fi
COST_MB=$(python3 -c "print(round($COST_BYTES/1048576, 2))")
[ "$COST_PKGS" -gt 0 ] || { fail "$PKG is not in the repos"; exit 1; }
note "$COST_PKGS packages, $COST_MB MB"

# --- 1. install -------------------------------------------------------------
note "installing"
"$SSH" "sudo pacman -S --needed --noconfirm -- $PKG" >/dev/null 2>&1 || {
  fail "$PKG failed to install"; exit 1; }

# --- 2. the launch handle ---------------------------------------------------
# Which .desktop, when a package ships several.
#
# `head -1` takes the alphabetically first, and for nautilus that is
# nautilus-autorun-software.desktop -- a helper that prints a usage message and
# exits, which looks exactly like an app that will not start. The store solves
# this in launcher.py by preferring the entry whose name matches the app's own
# icon; the same rule works here, with the AppStream id standing in for it.
DESKTOPS=$("$SSH" "pacman -Ql $PKG | grep -o '/usr/share/applications/.*\.desktop$'" || true)
# `|| true` on both, and it is load-bearing: with `set -o pipefail` a grep that
# matches nothing fails the whole pipeline, and `set -e` then aborts the script
# silently, mid-measurement. deja-dup found this -- it ships
# org.gnome.DejaDup.desktop, which no pattern built from "deja-dup" matches.
squashed=$(printf '%s' "$PKG" | tr -d '-')
DESKTOP=$(printf '%s\n' "$DESKTOPS" \
  | grep -iE "/([^/]*\.)?(${PKG}|${squashed})\.desktop$" | head -1 || true)
# Failing that, the shortest name: helpers are longer than the app they help.
[ -n "$DESKTOP" ] || DESKTOP=$(printf '%s\n' "$DESKTOPS" \
  | awk 'NF{print length($0)"\t"$0}' | sort -n | cut -f2- | head -1 || true)
if [ "$(printf '%s\n' "$DESKTOPS" | grep -c .)" -gt 1 ]; then
  note "$PKG ships $(printf '%s\n' "$DESKTOPS" | grep -c .) desktop entries; using $(basename "$DESKTOP")"
fi
if [ -z "$DESKTOP" ]; then
  echo "{\"pkg\":\"$PKG\",\"launch\":\"no-desktop-entry\",\"cost_pkgs\":$COST_PKGS,\"cost_mb\":$COST_MB}"
  fail "$PKG ships no .desktop -- a TUI, or not an app"
  exit 0
fi
ID=$(basename "$DESKTOP" .desktop)
CLASS=$("$SSH" "grep -m1 '^StartupWMClass=' $DESKTOP | cut -d= -f2" || true)
[ -n "$CLASS" ] || CLASS=$ID
note "desktop id $ID, window class $CLASS"

# --- helpers ----------------------------------------------------------------
screen_clear() {
  # grim photographs the composited screen, not a window. The drawer and the
  # recents switcher are layer-shell overlays drawn above every tiled window,
  # and `hyprctl clients` does not list layer surfaces -- so a shot taken with
  # the drawer open is a picture of the drawer while every window check passes.
  #
  # That is not hypothetical. Four apps were "measured" through an app drawer
  # somebody had left open with a half-typed search in it, and the drawer under
  # two themes diffs to 0.82, which reads as a perfectly themed app. Two of the
  # four were Kirigami, which cannot follow the theme at all.
  #
  # omarchy-shell needs OMARCHY_PATH, and without it fails quietly -- which is
  # how the first attempt to close the drawer appeared to work and did not.
  "$SSH" "$E omarchy-shell drawer close; omarchy-shell recents close" >/dev/null 2>&1 || true
  local state
  state=$("$SSH" "$E omarchy-shell drawer state" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$state" ] && [ "$state" != closed ]; then
    fail "the drawer is $state and will be in the picture"
    return 1
  fi
  return 0
}

osk_down() {
  # Must be [0,26,0,0] or [0,26,0,20]. Anything larger at the bottom is the
  # keyboard, and a shot taken now is a picture of a 360x474 window.
  #
  # Lower it and re-check rather than giving up: an app that focuses a text
  # field on startup raises the keyboard by itself, and refusing to shoot those
  # would quietly exclude every search-first app in the catalogue.
  local bottom
  for _ in 1 2; do
    bottom=$("$SSH" "$E hyprctl -j monitors | jq -r '.[0].reserved[3]'" 2>/dev/null || echo 999)
    [ "${bottom:-999}" -le 20 ] && return 0
    note "the keyboard is up (reserved ${bottom}px) -- lowering it"
    "$SSH" "$E busctl --user call sm.puri.OSK0 /sm/puri/OSK0 sm.puri.OSK0 \
      SetVisible b false" >/dev/null 2>&1 || true
    sleep 2
  done
  return 1
}

launch_and_wait() {
  # Count the windows first, then launch, then wait for the count to rise.
  #
  # Matching on a class guessed from the desktop id is not good enough, and the
  # failures look identical to an app that never started: railway's entry is
  # de.schmidhuberj.DieBahn, Collision maps as dev.geopjr.Collision, songrec as
  # re.fossplant.songrec. Each was recorded "no-window" while its window sat on
  # screen. "Did a new window appear" needs no guess.
  #
  # 60s, not 30: plasma-camera and haruna both turned up minutes after the
  # script had given up and uninstalled them. A timeout is a statement about
  # patience, so the verdict says "no window within 60s", never "does not
  # launch".
  local before after
  before=$("$SSH" "$E hyprctl -j clients | jq -r '.[].address'" 2>/dev/null | sort | tr '\n' ' ')
  "$SSH" "$E setsid -f gtk-launch $ID >/tmp/sweep-$PKG.log 2>&1" >/dev/null 2>&1

  local i
  for i in $(seq 1 "$WAIT"); do
    sleep 1
    after=$("$SSH" "$E hyprctl -j clients | jq -r '.[].address'" 2>/dev/null | sort | tr '\n' ' ')
    for addr in $after; do
      case " $before " in
        *" $addr "*) continue ;;
      esac

      # A new window is not necessarily OUR window.
      #
      # The shell's own surfaces come and go: open the app drawer, or leave it
      # open from another session, and it appears here as a new client. The
      # first version of this took any new address, so launching kalk
      # photographed the drawer with somebody's half-typed search in it -- and
      # the drawer under two themes diffs to 0.82, which reads as a perfectly
      # themed app. Four Kirigami apps "measured" as following the theme, which
      # is impossible, before anyone looked at a picture.
      local cls cmd
      cls=$("$SSH" "$E hyprctl -j clients | jq -r --arg a '$addr' \
        '.[]|select(.address==\$a)|.class'" 2>/dev/null || true)
      case "$cls" in
        org.quickshell|org.moarchy.Store|""|null) continue ;;
      esac
      # And confirm it is the thing we launched rather than a coincidence:
      # either the class looks like the desktop id, or the process behind the
      # window was started from this package.
      cmd=$("$SSH" "$E hyprctl -j clients | jq -r --arg a '$addr' \
        '.[]|select(.address==\$a)|.pid' | xargs -r -I{} cat /proc/{}/cmdline 2>/dev/null | tr '\\0' ' '" 2>/dev/null || true)
      local stem=${ID##*.}
      case "$(printf '%s %s' "$cls" "$cmd" | tr 'A-Z' 'a-z')" in
        *"$(printf '%s' "$stem" | tr 'A-Z' 'a-z')"*|*"$(printf '%s' "$PKG" | tr 'A-Z' 'a-z')"*) ;;
        *) note "ignoring a new window that is not $PKG: $cls"; continue ;;
      esac

      WINDOW=$addr
      CLASS=$cls
      return 0
    done
  done
  return 1
}

window_geometry() {
  [ -n "${WINDOW:-}" ] || { echo null; return; }
  "$SSH" "$E hyprctl -j clients | jq -c --arg a '$WINDOW' \
    '[.[]|select(.address==\$a)][0]|{at,size}'" 2>/dev/null || echo null
}

close_it() {
  # Kill the process, do not ask the compositor to close the window.
  #
  # Two reasons, and the second is the one that matters. `hyprctl dispatch
  # closewindow class:X` is rejected outright by this Hyprland, which wants the
  # lua form -- so it failed silently, the window stayed up, and launch_and_wait
  # found it still there and returned success without relaunching anything. The
  # light shot was then the dark app with a light shell around it, and every app
  # measured as ignoring the theme.
  #
  # And even when it works, closing a window is not enough for a GNOME app: it
  # is D-Bus activatable, so the process stays behind as a windowless daemon and
  # the next open paints from the stylesheet it parsed at startup. The theme
  # test needs a genuinely new process, which is what a kill guarantees.
  local pids
  pids=$("$SSH" "$E hyprctl -j clients | jq -r --arg c '$CLASS' \
    '.[]|select(.initialClass==\$c or .class==\$c)|.pid'" 2>/dev/null || true)
  [ -n "$pids" ] && "$SSH" "kill $(printf '%s' "$pids" | tr '\n' ' ')" >/dev/null 2>&1 || true

  # ...and the windowless daemon behind it, if this app leaves one.
  #
  # NOT `pkill -f -- '--gapplication-service'`. -f matches the whole command
  # line, and the command line it matches first is this very ssh invocation,
  # whose -c string contains the pattern -- so the pkill kills the shell running
  # it and everything after it silently does not happen. That is not
  # hypothetical: `pkill -f moarchy-store` did exactly this here, and omarchy's
  # own theme hook carries a long comment about the same trap.
  #
  # The fix is the hook's: require the flag to be the LAST argument, which is
  # what a real daemon looks like and never what a shell running a -c string
  # looks like.
  "$SSH" "for p in \$(pgrep -f -- '--gapplication-service' 2>/dev/null); do
      [ \"\$p\" = \$\$ ] && continue
      last=\$(tr '\\0' '\\n' < /proc/\$p/cmdline 2>/dev/null | tail -1)
      [ \"\$last\" = '--gapplication-service' ] && kill \$p
    done" >/dev/null 2>&1 || true
  return 0
}

# The geometry the last shot was actually taken at. The theme diff is only
# meaningful if both shots were the same shape -- see shoot().
SHOT_GEOM=""

shoot() {  # shoot <theme> <outfile>
  "$SSH" "$E omarchy-theme-set $1" >/dev/null 2>&1 || { fail "no theme $1"; return 1; }
  close_it; sleep 2
  launch_and_wait || return 1

  # Settle: an app that focuses a text field on startup raises the keyboard
  # *after* the window maps, so checking once and shooting is not enough. Lower
  # it, wait for the window to grow back, and only then shoot.
  local geom=""
  for _ in 1 2 3; do
    screen_clear || true
    osk_down || true
    sleep 1
    geom=$(window_geometry)
    case "$geom" in *'"size":[360,674]'*|*'"size": [360, 674]'*) break ;; esac
    sleep 1
  done
  SHOT_GEOM=$geom

  screen_clear || return 1
  sleep 2   # let it finish its first paint
  "$VM/scripts/vm-screenshot.sh" "$2" >/dev/null
}

# --- 3. dark ----------------------------------------------------------------
mkdir -p screenshots
# Absolute, because vm-screenshot.sh cd's to the omarchy-mobile checkout before
# it writes -- a relative path here silently lands in that repo's screenshots/.
DARK=$REPO_ROOT/screenshots/$PKG-dark.png
LIGHT=$REPO_ROOT/screenshots/$PKG-light.png

note "launching under $DARK_THEME"
if ! shoot "$DARK_THEME" "$DARK"; then
  # The Chatty case: exit 0, no process, no window. Say why.
  LOG=$("$SSH" "cat /tmp/sweep-$PKG.log 2>/dev/null | tail -20" || true)
  DBUS=$("$SSH" "grep -m1 '^DBusActivatable=' $DESKTOP" || true)
  JOURNAL=$("$SSH" "journalctl --user -n 25 --no-pager 2>/dev/null | tail -12" || true)
  [ "$KEEP" = 1 ] || "$SSH" "sudo pacman -Rns --noconfirm -- $PKG" >/dev/null 2>&1 || true
  python3 - "$PKG" "$ID" "$COST_PKGS" "$COST_MB" <<PY
import json, sys
print(json.dumps({
  "pkg": sys.argv[1], "desktop_id": sys.argv[2], "launch": "no-window",
  "cost_pkgs": int(sys.argv[3]), "cost_mb": float(sys.argv[4]),
  "adaptive": "no-window", "themed": "unknown",
  "log": """$LOG""".strip(), "dbus_activatable": """$DBUS""".strip(),
  "journal": """$JOURNAL""".strip(),
}, indent=2))
PY
  exit 0
fi
GEOM=$SHOT_GEOM
DARK_GEOM=$SHOT_GEOM
note "mapped at $GEOM"

# --- 4. light, with the relaunch that makes it mean anything ----------------
note "switching to $LIGHT_THEME and relaunching"
shoot "$LIGHT_THEME" "$LIGHT" || fail "no light shot"
LIGHT_GEOM=$SHOT_GEOM

# --- 5. did it actually recolour? -------------------------------------------
# Compare only the app's own rectangle. The 26px bar at the top is the shell's
# and is always themed; leaving it in reports every app as following the theme.
THEMED=unknown; RMSE=""; THEME_NOTE=""
# Both shots must be the same shape, or the diff is comparing different parts
# of two different layouts. KleverNotes measured "themed" at RMSE 0.23 purely
# because it raised the keyboard for the dark shot and not the light one: the
# windows were 474 and 674 tall, one crop was applied to both, and the number
# described the misalignment rather than the palette.
if [ -n "$DARK_GEOM" ] && [ "$DARK_GEOM" != "$LIGHT_GEOM" ]; then
  THEME_NOTE="the two shots are different shapes ($DARK_GEOM vs $LIGHT_GEOM); no diff taken"
  fail "$THEME_NOTE"
elif [ -f "$DARK" ] && [ -f "$LIGHT" ]; then
  CROP=$(python3 - "$GEOM" <<'PY'
import json, sys
try:
    g = json.loads(sys.argv[1])
    x, y = g["at"]; w, h = g["size"]
    print(f"{w*2}x{h*2}+{x*2}+{y*2}")     # logical -> physical, scale 2
except Exception:
    print("")
PY
)
  if [ -n "$CROP" ]; then
    magick "$DARK"  -crop "$CROP" +repage /tmp/sweep-d.png
    magick "$LIGHT" -crop "$CROP" +repage /tmp/sweep-l.png
    # `magick compare` exits 1 whenever the images differ at all, which is the
    # normal case here and is not an error. Without the guard, set -e kills the
    # script the moment an app turns out to be themed.
    RMSE=$(magick compare -metric RMSE /tmp/sweep-d.png /tmp/sweep-l.png null: 2>&1 \
           | sed -n 's/.*(\([0-9.]*\)).*/\1/p' || true)
    # Thresholds from measurement, not taste. Apps that follow the theme land
    # far away from those that do not: Foliate 0.80 and gnome-calculator 0.66
    # against KleverNotes 0.042 and marble-maps 0.071, both Kirigami and both
    # structurally unable to follow it. The gap is an order of magnitude, so the
    # band between 0.10 and 0.25 is genuinely "look at it yourself".
    THEMED=$(python3 -c "
v = float('${RMSE:-0}')
print('yes' if v > 0.25 else 'no' if v < 0.10 else 'partial')")
    note "RMSE $RMSE -> themed=$THEMED"
  fi
fi

# A light shot identical to the dark one is a duplicate of a thing that did
# not happen. Keep it only when the app actually responded.
SHOTS="[\"$PKG-dark.png\", \"$PKG-light.png\"]"
if [ "$THEMED" = "no" ]; then
  rm -f "$LIGHT"; SHOTS="[\"$PKG-dark.png\"]"
  note "dropped the light shot: it is the dark one"
fi

close_it
[ "$KEEP" = 1 ] || "$SSH" "sudo pacman -Rns --noconfirm -- $PKG" >/dev/null 2>&1 || true

python3 - "$PKG" "$ID" "$COST_PKGS" "$COST_MB" "$THEMED" "${RMSE:-}" "$GEOM" "$SHOTS" \
        "${THEME_NOTE:-}" <<'PY'
import json, sys
_, pkg, ident, pkgs, mb, themed, rmse, geom, shots, note = sys.argv
print(json.dumps({
  "pkg": pkg, "desktop_id": ident, "launch": "ok",
  "cost_pkgs": int(pkgs), "cost_mb": float(mb),
  "geometry": json.loads(geom) if geom and geom != "null" else None,
  # "mapped" and not "fits": nobody has looked at the picture yet.
  "adaptive": "mapped", "themed": themed, "rmse": float(rmse) if rmse else None,
  "theme_note": note or None,
  "shots": json.loads(shots),
}, indent=2))
PY
