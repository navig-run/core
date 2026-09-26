#!/usr/bin/env bash
# NAVIG showcase — stitch the recorded scenes into one ~80 s trailer (MP4 + WebM).
#
# Runs INSIDE WSL after record.sh. Title cards are drawn by ffmpeg (drawtext, the same Nerd
# Font the scenes use); scenes are padded from 1200×700 onto a 1280×720 canvas in the
# terminal theme's background and joined with short cross-fades. Nothing in the scenes is
# altered — a trailer is a sequence, not an edit.
#
#   trailer.sh                      # → core/.dev/showcase/video/trailer.{mp4,webm}
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$(cd "$HERE/../.." && pwd)"
VIDEO="$CORE/.dev/showcase/video"
WORK="$HOME/navig-showcase-run/trailer"
BG="0x1e1e2e"                      # Catppuccin Mocha base — the tapes' theme background
FONT="$(fc-match -f '%{file}' 'JetBrainsMono Nerd Font:style=Bold' 2>/dev/null || true)"
[[ -n "$FONT" ]] || FONT="$(fc-match -f '%{file}' 'monospace')"
W=1280; H=720; FPS=20; XFADE=0.5

log() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()  { printf '\033[32m✓\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# scene order for the trailer (a subset: the ones that tell the story in 80 s)
SCENES=(hero hosts-and-safety spaces blocks ledger-and-undo whoami)
for s in "${SCENES[@]}"; do [[ -f "$VIDEO/$s.mp4" ]] || die "missing $VIDEO/$s.mp4 — run record.sh first"; done

rm -rf "$WORK"; mkdir -p "$WORK"

# ── title cards ─────────────────────────────────────────────────────────────
card() { # out.mp4 seconds "line1" "line2" [size1 size2]
  local out="$1" secs="$2" l1="$3" l2="$4" s1="${5:-72}" s2="${6:-30}"
  ffmpeg -loglevel error -y -f lavfi -i "color=c=$BG:s=${W}x${H}:r=$FPS:d=$secs" \
    -vf "drawtext=fontfile='$FONT':text='$l1':fontcolor=0xcdd6f4:fontsize=$s1:x=(w-text_w)/2:y=(h-text_h)/2-40,\
drawtext=fontfile='$FONT':text='$l2':fontcolor=0x94e2d5:fontsize=$s2:x=(w-text_w)/2:y=(h-text_h)/2+50" \
    -c:v libx264 -pix_fmt yuv420p -crf 20 "$out"
}
log "title cards"
card "$WORK/00-open.mp4"  3 "NAVIG" "Inspect. Operate. Automate."
card "$WORK/25-mid.mp4"   2.5 "One operator surface." "For operators."
card "$WORK/99-close.mp4" 4 "pip install navig" "github.com/navig-run/core" 56 34

# ── pad every scene onto the canvas at a constant frame rate ────────────────
pad() { # in out
  ffmpeg -loglevel error -y -i "$1" \
    -vf "scale=w=$W:h=$H:force_original_aspect_ratio=decrease,pad=$W:$H:(ow-iw)/2:(oh-ih)/2:color=$BG,fps=$FPS,format=yuv420p" \
    -c:v libx264 -crf 20 -an "$2"
}
i=10
for s in "${SCENES[@]}"; do
  log "pad $s"
  pad "$VIDEO/$s.mp4" "$WORK/$(printf '%02d' "$i")-$s.mp4"
  i=$((i + 10))
done

# ── join with cross-fades ───────────────────────────────────────────────────
# xfade needs each clip's duration to place the offset; build the filter graph in order.
mapfile -t CLIPS < <(ls "$WORK"/*.mp4 | sort)
inputs=(); filter=""; prev="0:v"; offset=0
for idx in "${!CLIPS[@]}"; do
  inputs+=(-i "${CLIPS[$idx]}")
done
for idx in "${!CLIPS[@]}"; do
  (( idx == 0 )) && continue
  dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "${CLIPS[$((idx - 1))]}")"
  offset="$(python3 -c "print(round($offset + $dur - $XFADE, 3))")"
  out="v$idx"
  filter+="[$prev][$idx:v]xfade=transition=fade:duration=$XFADE:offset=$offset[$out];"
  prev="$out"
done
filter="${filter%;}"
log "concat ${#CLIPS[@]} clips"
ffmpeg -loglevel error -y "${inputs[@]}" -filter_complex "$filter" -map "[$prev]" \
  -c:v libx264 -pix_fmt yuv420p -crf 21 -movflags +faststart "$VIDEO/trailer.mp4"
ffmpeg -loglevel error -y -i "$VIDEO/trailer.mp4" -c:v libvpx-vp9 -b:v 0 -crf 33 -row-mt 1 -an "$VIDEO/trailer.webm"
ok "trailer → $VIDEO/trailer.mp4 ($(ffprobe -v error -show_entries format=duration -of csv=p=0 "$VIDEO/trailer.mp4" | cut -d. -f1)s) + trailer.webm"
