#!/usr/bin/env bash
# Give the guest something to show, so screenshots are not all empty states.
#
#   ./scripts/sweep-seed.sh            # fill the home directory, once
#   ./scripts/sweep-seed.sh --force    # do it again from scratch
#   ./scripts/sweep-seed.sh --list     # what is there now
#
# A third of the catalogue -- 35 of 109 entries -- reads a standard directory
# and shows whatever is in it. On a fresh image those directories are empty, so
# the screenshots say "No Books Yet", "Add some books", "Get Some Shows", "Drop
# images here to compress them". Those are pictures of an app that has never
# been used, and they sell nothing: someone deciding whether Amberol is worth
# 1.8 MB learns more from a library with albums in it than from a button that
# says Add Folder.
#
# EVERYTHING HERE IS GENERATED, IN THE GUEST, FROM NOTHING.
#
# That is a licensing decision rather than a technical one. Seeding real media
# would mean shipping or fetching files whose rights have to be checked, kept
# straight, and re-checked by whoever touches this next. Generating means there
# is nothing to check: ffmpeg and ImageMagick are already on the image, and the
# result is reproducible, costs the repo nothing, and cannot be wrong about a
# licence.
#
# The metadata is real, because that is the part a screenshot shows. A music
# library displays composer, album, track and cover art, never the waveform --
# so the tags name actual public-domain compositions (Beethoven, Bach, Chopin,
# all long out of copyright) while the audio behind them is a quiet generated
# tone. Every file says so in its comment tag, so nobody mistakes it for a
# recording. The same applies to the text: Sonnet 18 and the opening of Alice
# are public domain and are quoted as themselves.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO_ROOT"

VM=${MOARCHY_VM:-$HOME/Projects/omarchy-mobile}
SSH="$VM/scripts/vm-ssh.sh"
[ -x "$SSH" ] || { echo "!! no VM at $VM -- set MOARCHY_VM" >&2; exit 1; }

MODE=seed
case "${1:-}" in
  --force) MODE=force ;;
  --list)  MODE=list ;;
  "")      ;;
  *)       echo "!! unknown argument: $1" >&2; exit 1 ;;
esac

if [ "$MODE" = list ]; then
  "$SSH" 'for d in Music Pictures Videos Documents Downloads; do
    printf "%-11s %3s files  %s\n" "$d" "$(find ~/$d -type f 2>/dev/null | wc -l)" \
      "$(du -sh ~/$d 2>/dev/null | cut -f1)"
  done; echo; cat ~/.local/share/moarchy-fixtures.txt 2>/dev/null | head -3'
  exit 0
fi

echo "==> seeding the guest's home (generated fixtures, no third-party media)"
"$SSH" "MODE=$MODE bash -s" <<'GUEST'
set -euo pipefail
STAMP=~/.local/share/moarchy-fixtures.txt
if [ -f "$STAMP" ] && [ "${MODE:-seed}" != force ]; then
  echo "    already seeded ($(head -1 "$STAMP")) -- --force to redo"; exit 0
fi

mkdir -p ~/Music ~/Pictures ~/Videos ~/Documents ~/Downloads ~/.local/share
Q="-loglevel error -y"

# ImageMagick has no default font on a headless image and fails outright --
# "unable to read font" -- rather than falling back. Pick one that is there.
FONT=""
for f in Adwaita-Sans DejaVu-Sans Liberation-Sans Noto-Sans; do
  if magick -list font 2>/dev/null | grep -q "Font: $f\$"; then FONT=$f; break; fi
done
[ -n "$FONT" ] || FONT=$(magick -list font 2>/dev/null | awk '/Font:/{print $2; exit}')
# And if ImageMagick knows no fonts by name at all -- which happens when it is
# built without fontconfig, as on the Mac this was written on -- give it a file
# instead. -font takes either.
[ -n "$FONT" ] || FONT=$(fc-match -f '%{file}' sans-serif 2>/dev/null || true)
[ -n "$FONT" ] || FONT=$(fc-list : file 2>/dev/null | head -1 | cut -d: -f1)
[ -n "$FONT" ] || { echo "!! no usable font in this guest -- text cannot be drawn" >&2; exit 1; }
echo "    drawing text with $FONT"
TEXT=(-font "$FONT")

# --- cover art -------------------------------------------------------------
# A sleeve is a coloured field with the composer on it. Plasma rather than a
# flat fill so the album grid does not look like a test card.
sleeve() {  # sleeve <file> <top> <bottom> <line1> <line2>
  magick -size 600x600 "gradient:$2-$3" \
    -swirl 40 -blur 0x8 "${TEXT[@]}" \
    -gravity center -pointsize 52 -fill white \
    -annotate +0-40 "$4" -pointsize 30 -annotate +0+40 "$5" "$1"
}

# --- music -----------------------------------------------------------------
# Real compositions, all public domain. Generated audio, said so in the tag.
album() {  # album <dir> <artist> <album> <year> <cover> <title...>
  local dir="$1" artist="$2" name="$3" year="$4" cover="$5"; shift 5
  mkdir -p "$HOME/Music/$dir"
  local n=1
  for title in "$@"; do
    local out
    out=$(printf '%s/Music/%s/%02d %s.mp3' "$HOME" "$dir" "$n" "$title")
    # A quiet tone, not silence: a player that skips empty files still shows it.
    ffmpeg $Q -f lavfi -i "sine=frequency=$((180 + n * 40)):duration=$((150 + n * 17))" \
      -i "$cover" -map 0:a -map 1:v -c:a libmp3lame -b:a 64k -c:v copy \
      -id3v2_version 3 -metadata:s:v title="Album cover" \
      -metadata artist="$artist" -metadata album_artist="$artist" \
      -metadata album="$name" -metadata title="$title" \
      -metadata date="$year" -metadata track="$n" -metadata genre="Classical" \
      -metadata comment="Generated test tone, not a recording. The work is public domain." \
      "$out"
    n=$((n + 1))
  done
}

sleeve /tmp/c1.png '#1b2a4a' '#6d3b5e' 'Beethoven' 'Symphony No. 5'
sleeve /tmp/c2.png '#23402f' '#7a6a2f' 'J. S. Bach' 'Cello Suite No. 1'
sleeve /tmp/c3.png '#3a1f3d' '#8c4a3a' 'Chopin' 'Nocturnes'

album 'Beethoven - Symphony No. 5' 'Ludwig van Beethoven' 'Symphony No. 5 in C minor' 1808 /tmp/c1.png \
  'I. Allegro con brio' 'II. Andante con moto' 'III. Scherzo. Allegro' 'IV. Allegro'
album 'Bach - Cello Suite No. 1' 'Johann Sebastian Bach' 'Cello Suite No. 1 in G major' 1720 /tmp/c2.png \
  'Prelude' 'Allemande' 'Courante' 'Sarabande'
album 'Chopin - Nocturnes' 'Frederic Chopin' 'Nocturnes' 1832 /tmp/c3.png \
  'Op. 9 No. 1 in B flat minor' 'Op. 9 No. 2 in E flat major' 'Op. 27 No. 2 in D flat major'

# --- pictures --------------------------------------------------------------
# Landscapes rather than noise. The first attempt used ImageMagick's plasma
# fractal, which produces neon static: a gallery full of it looks worse than
# the empty state it was meant to replace. A sky gradient, a low sun and two
# layered ridges read as a photograph at thumbnail size, which is the size
# these are ever seen at.
scene() {  # scene <out> <sky-top> <sky-bottom> <ridge> <sun-x> <sun-y> <ridge-path> <fore-path>
  magick -size 1600x1200 "gradient:$2-$3" \
    \( -size 1600x1200 xc:none -fill '#fff8e0' -draw "circle $5,$6 $5,$(($6 + 70))" -blur 0x45 \) \
    -compose screen -composite \
    \( -size 1600x1200 xc:none -fill "$4" -draw "polygon $7" \) -compose over -composite \
    \( -size 1600x1200 xc:none -fill '#00000055' -draw "polygon $8" \) -compose over -composite \
    -attenuate 0.28 +noise Gaussian -blur 0x0.6 -contrast-stretch 0.5%x0.5% "$1"
}
RIDGE_A='0,900 260,760 520,860 900,700 1240,820 1600,740 1600,1200 0,1200'
RIDGE_B='0,820 300,700 640,880 980,730 1320,850 1600,790 1600,1200 0,1200'
RIDGE_C='0,960 380,820 760,930 1100,790 1450,900 1600,860 1600,1200 0,1200'
FORE_A='0,1010 340,930 700,1010 1050,920 1600,990 1600,1200 0,1200'
FORE_B='0,1060 420,980 820,1050 1200,960 1600,1030 1600,1200 0,1200'

scene ~/Pictures/coast-morning.jpg  '#1b3a5c' '#e8a973' '#2a2233' 1180 820 "$RIDGE_A" "$FORE_A"
scene ~/Pictures/pine-ridge.jpg     '#22304a' '#c98f6e' '#1e2630' 420  760 "$RIDGE_B" "$FORE_B"
scene ~/Pictures/harbour-lights.jpg '#101d33' '#5b6f8e' '#0d1420' 980  880 "$RIDGE_C" "$FORE_A"
scene ~/Pictures/riverbank.jpg      '#2d4a3a' '#d8c08a' '#20301f' 700  800 "$RIDGE_B" "$FORE_A"
mkdir -p ~/Pictures/Camera
scene ~/Pictures/Camera/IMG_0041.jpg '#3a2a45' '#e0a07a' '#231a2b' 300 840 "$RIDGE_C" "$FORE_B"
scene ~/Pictures/Camera/IMG_0042.jpg '#14304a' '#9fb4c4' '#12222e' 1320 780 "$RIDGE_A" "$FORE_B"
# One carrying EXIF, so Metadata Cleaner has something to strip.
magick ~/Pictures/Camera/IMG_0041.jpg \
  -set exif:Make 'Pine64' -set exif:Model 'PinePhone' \
  -set exif:DateTimeOriginal '2026:08:14 09:12:33' \
  -set exif:GPSLatitude '52/1 31/1 0/1' -set exif:GPSLongitude '13/1 24/1 0/1' \
  ~/Pictures/Camera/IMG_0043.jpg

# --- video -----------------------------------------------------------------
# drawtext needs ffmpeg built against libfreetype. The guest has it; not every
# machine does, and a missing filter is a hard error rather than a fallback, so
# the label is optional and the clip is not.
HAS_DRAWTEXT=0
ffmpeg -hide_banner -filters 2>/dev/null | grep -qE '^ [.TS]+ drawtext' && HAS_DRAWTEXT=1

clip() {  # clip <file> <label> <seconds>
  local vf=()
  [ "$HAS_DRAWTEXT" = 1 ] && vf=(-vf "drawtext=text='$2':fontcolor=white:fontsize=54:x=(w-tw)/2:y=(h-th)/2")
  ffmpeg $Q -f lavfi -i "testsrc2=size=1280x720:rate=24:duration=$3" \
    -f lavfi -i "sine=frequency=220:duration=$3" "${vf[@]}" \
    -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest "$1"
}
clip ~/Videos/tide-timelapse.mp4 'Tide, timelapse'   18
clip ~/Videos/workshop-clip.mp4  'Workshop'          12

# --- documents -------------------------------------------------------------
# Public-domain text, quoted as itself.
cat > /tmp/sonnet.txt <<'TXT'
Sonnet 18
William Shakespeare

Shall I compare thee to a summer's day?
Thou art more lovely and more temperate:
Rough winds do shake the darling buds of May,
And summer's lease hath all too short a date;

Sometime too hot the eye of heaven shines,
And often is his gold complexion dimm'd;
And every fair from fair sometime declines,
By chance or nature's changing course untrimm'd;

But thy eternal summer shall not fade,
Nor lose possession of that fair thou ow'st;
Nor shall death brag thou wander'st in his shade,
When in eternal lines to time thou grow'st:

So long as men can breathe or eyes can see,
So long lives this, and this gives life to thee.
TXT
magick -size 1240x1754 xc:white "${TEXT[@]}" -gravity northwest -pointsize 34 -fill '#222' \
  -annotate +90+120 "@/tmp/sonnet.txt" ~/Documents/sonnet-18.pdf

cat > /tmp/alice.txt <<'TXT'
Alice's Adventures in Wonderland
Lewis Carroll, 1865

CHAPTER I. Down the Rabbit-Hole

Alice was beginning to get very tired of sitting by her sister
on the bank, and of having nothing to do: once or twice she had
peeped into the book her sister was reading, but it had no
pictures or conversations in it, "and what is the use of a
book," thought Alice, "without pictures or conversations?"
TXT
magick -size 1240x1754 xc:white "${TEXT[@]}" -gravity northwest -pointsize 32 -fill '#222' \
  -annotate +90+120 "@/tmp/alice.txt" /tmp/alice-p1.png
magick -size 1240x1754 xc:white "${TEXT[@]}" -gravity northwest -pointsize 32 -fill '#222' \
  -annotate +90+120 "So she was considering in her own mind, whether the\npleasure of making a daisy-chain would be worth the\ntrouble of getting up and picking the daisies, when\nsuddenly a White Rabbit with pink eyes ran close by her." /tmp/alice-p2.png
magick /tmp/alice-p1.png /tmp/alice-p2.png ~/Documents/alice-in-wonderland.pdf

# EPUB: a zip with the mimetype stored first and uncompressed, which is the
# one thing the format is strict about.
build_epub() {
  local work=/tmp/epub out="$1"
  rm -rf "$work"; mkdir -p "$work/META-INF" "$work/OEBPS"
  printf 'application/epub+zip' > "$work/mimetype"
  cat > "$work/META-INF/container.xml" <<'XML'
<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
XML
  cat > "$work/OEBPS/content.opf" <<'XML'
<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Alice's Adventures in Wonderland</dc:title>
    <dc:creator>Lewis Carroll</dc:creator>
    <dc:language>en</dc:language>
    <dc:date>1865</dc:date>
    <dc:rights>Public domain</dc:rights>
    <dc:identifier id="id">moarchy-fixture-alice</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="c1"/></spine>
</package>
XML
  cat > "$work/OEBPS/toc.ncx" <<'XML'
<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="moarchy-fixture-alice"/></head>
  <docTitle><text>Alice's Adventures in Wonderland</text></docTitle>
  <navMap><navPoint id="n1" playOrder="1"><navLabel><text>Down the Rabbit-Hole</text></navLabel>
    <content src="chapter1.xhtml"/></navPoint></navMap>
</ncx>
XML
  cat > "$work/OEBPS/chapter1.xhtml" <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Down the Rabbit-Hole</title></head>
<body><h1>Chapter I. Down the Rabbit-Hole</h1>
<p>Alice was beginning to get very tired of sitting by her sister on the bank,
and of having nothing to do: once or twice she had peeped into the book her
sister was reading, but it had no pictures or conversations in it, &#8220;and
what is the use of a book,&#8221; thought Alice, &#8220;without pictures or
conversations?&#8221;</p>
<p>So she was considering in her own mind, whether the pleasure of making a
daisy-chain would be worth the trouble of getting up and picking the daisies,
when suddenly a White Rabbit with pink eyes ran close by her.</p>
</body></html>
XML
  ( cd "$work" && zip -q -X -0 "$out" mimetype && zip -q -X -r "$out" META-INF OEBPS )
}
rm -f ~/Documents/alice-in-wonderland.epub
build_epub ~/Documents/alice-in-wonderland.epub

# CBZ: a comic is a zip of page images.
mkdir -p /tmp/cbz
for i in 1 2 3 4 5 6; do
  magick -size 900x1300 "gradient:#e8e2d4-#b9ae97" \
    -fill '#3a3328' -draw "rectangle 60,120 840,640" \
    -fill '#cfc6b2' -draw "rectangle 80,140 820,620" \
    -fill '#3a3328' -draw "rectangle 60,700 430,1180" -draw "rectangle 470,700 840,1180" \
    -fill '#cfc6b2' -draw "rectangle 80,720 410,1160" -draw "rectangle 490,720 820,1160" \
    "${TEXT[@]}" -gravity north -pointsize 44 -fill '#3a3328' -annotate +0+40 "Page $i" \
    "/tmp/cbz/$(printf '%02d' $i).jpg"
done
rm -f ~/Documents/the-yellow-kid.cbz
( cd /tmp/cbz && zip -q -r ~/Documents/the-yellow-kid.cbz . )

# Notes, for the markdown editors that open a folder.
mkdir -p ~/Documents/Notes
cat > ~/Documents/Notes/shopping.md <<'MD'
# Shopping

- [x] coffee
- [ ] oat milk
- [ ] bread
- [ ] the good olive oil
MD
cat > ~/Documents/Notes/phone-setup.md <<'MD'
# Phone setup

Things to do after flashing:

1. `omarchy-theme-set tokyo-night`
2. pair the keyboard
3. copy Music across
4. check the modem sees the SIM
MD
cat > ~/Documents/Notes/reading-list.md <<'MD'
# Reading list

| Title | Author | Done |
|---|---|---|
| Alice's Adventures in Wonderland | Carroll | yes |
| The Time Machine | Wells | no |
MD

# --- an archive, for the file managers -------------------------------------
rm -f ~/Downloads/photos-august.zip
( cd ~/Pictures && zip -q -r ~/Downloads/photos-august.zip . )

# --- record what happened --------------------------------------------------
{
  echo "seeded $(date -Iseconds) by scripts/sweep-seed.sh"
  echo
  echo "Everything here is generated in the guest by ffmpeg and ImageMagick."
  echo "No third-party media is shipped, fetched, or implied to be a recording."
  echo
  echo "The compositions named in the music tags -- Beethoven's Fifth, Bach's"
  echo "first cello suite, Chopin's nocturnes -- are public domain, and so are"
  echo "Shakespeare's Sonnet 18 and the opening of Alice's Adventures in"
  echo "Wonderland. The audio is a generated tone and each file says so in its"
  echo "comment tag."
  echo
  find ~/Music ~/Pictures ~/Videos ~/Documents ~/Downloads -type f | sort
} > "$STAMP"

echo "    $(find ~/Music ~/Pictures ~/Videos ~/Documents ~/Downloads -type f | wc -l) files"
GUEST
