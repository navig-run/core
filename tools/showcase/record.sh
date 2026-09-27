#!/usr/bin/env bash
# NAVIG showcase — record every tape into GIF + MP4 + WebM, then verify and describe them.
#
# Runs INSIDE WSL (see setup-wsl.sh). From Windows: `npm run showcase:record`.
#
#   record.sh                 # all tapes
#   record.sh hero spaces     # only these scenes
#   record.sh --plugins [blackbox …]   # plugin demos → plugins/navig-<name>/docs/demo.gif
#   record.sh --check         # verify the committed assets against MANIFEST.json (no recording)
#
# Outputs
#   core/docs/showcase/<scene>.gif          committed — what the README embeds
#   core/docs/showcase/MANIFEST.json        provenance: versions, commit, sha256 + size per asset
#   core/.dev/showcase/video/<scene>.{mp4,webm}   NOT committed — uploaded to a GitHub Release
#   core/.dev/showcase/frames/<scene>-{first,last}.png   for eyeballing a run
#
# Honesty contract: every frame is a real `navig` command against the sandbox in sandbox.sh
# and the lab sshd in setup-wsl.sh. Nothing is typed into the output; nothing is edited out.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$(cd "$HERE/../.." && pwd)"
TAPES="$HERE/tapes"
OUT_GIF="$CORE/docs/showcase"
OUT_DEV="$CORE/.dev/showcase"
OUT_VIDEO="$OUT_DEV/video"
OUT_FRAMES="$OUT_DEV/frames"
MANIFEST="$OUT_GIF/MANIFEST.json"
WORK="$HOME/navig-showcase-run"          # VHS writes next to the tape; keep that off /mnt/e (9P is slow)

# Size gates — a README that pulls 40 MB of GIFs is a README nobody finishes loading.
HERO_MAX_BYTES=$((4 * 1024 * 1024))
SCENE_MAX_BYTES=$((2600 * 1024))

log()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

sha256() { sha256sum "$1" | cut -d' ' -f1; }
fsize()  { stat -c %s "$1"; }

# The commit the assets were recorded from. `git -C` works in a plain checkout; in a git
# WORKTREE mounted from Windows the `.git` file points at `E:/…/.git/worktrees/<slug>`,
# a path Linux git cannot follow — so resolve HEAD by hand through wslpath.
showcase_commit() {
  local repo="$CORE/.." c
  c="$(git -C "$repo" rev-parse --short HEAD 2>/dev/null || true)"
  if [[ -z "$c" && -f "$repo/.git" ]]; then
    local gitdir; gitdir="$(sed 's/^gitdir: //' "$repo/.git")"
    gitdir="$(wslpath -u "$gitdir" 2>/dev/null || echo "$gitdir")"
    local head; head="$(cat "$gitdir/HEAD" 2>/dev/null)"
    if [[ "$head" == ref:* ]]; then
      local ref="${head#ref: }" common; common="$(cat "$gitdir/commondir" 2>/dev/null || echo ..)"
      c="$(cut -c1-9 "$gitdir/$common/$ref" 2>/dev/null || true)"
      [[ -z "$c" ]] && c="$(grep -m1 " $ref\$" "$gitdir/$common/packed-refs" 2>/dev/null | cut -c1-9)"
    else
      c="${head:0:9}"
    fi
  fi
  echo "${c:-unknown}"
}

# ── --check: verify committed assets, no recording ──────────────────────────
# One implementation, in Node: this check must also run in the CI gate, which is Windows
# node and cannot call into WSL. Re-implementing the rules here would be a second copy that
# drifts (and the bash one only ever checked sha/size plus core/README.md's references — not
# orphaned GIFs, the size budget, or the absolute-URL rule PyPI needs).
if [[ "${1:-}" == "--check" ]]; then
  command -v node >/dev/null     || die "node is not on PATH in here — run \`npm run showcase:check\` from Windows instead (it needs no WSL)"
  node "$CORE/../scripts/check-showcase-assets.mjs" "${@:2}"
  exit $?
fi

# ── --manifest: rewrite MANIFEST.json from the GIFs on disk (no recording) ──────
MANIFEST_ONLY=0
if [[ "${1:-}" == "--manifest" ]]; then MANIFEST_ONLY=1; shift; fi

# ── environment ─────────────────────────────────────────────────────────────
command -v vhs >/dev/null || die "vhs not installed — run setup-wsl.sh"
command -v ffmpeg >/dev/null || die "ffmpeg not installed — run setup-wsl.sh"
export VHS_NO_SANDBOX=true               # WSL runs as root; headless Chrome needs --no-sandbox
export SHOWCASE_DIR="$HERE"              # plugin tapes call $SHOWCASE_DIR/plugin-fixtures.sh off-camera
# shellcheck source=sandbox.sh
source "$HERE/sandbox.sh" --reset        # fresh sandbox every run: the GIFs show ONLY the fixtures
[[ -f "$HOME/navig-lab/sshd.pid" ]] && kill -0 "$(cat "$HOME/navig-lab/sshd.pid")" 2>/dev/null \
  || die "lab sshd is not running — run setup-wsl.sh"

mkdir -p "$OUT_GIF" "$OUT_VIDEO" "$OUT_FRAMES" "$WORK"
rm -rf "${WORK:?}"/*

# ── which scenes ────────────────────────────────────────────────────────────
# `--plugins [name…]` records the plugin demos (tapes/plugins/<name>.tape → plugins/navig-<name>/docs/demo.gif).
PLUGINS_ONLY=0
if [[ "${1:-}" == "--plugins" ]]; then
  PLUGINS_ONLY=1; shift
  if (( $# )); then scenes=("${@/#/plugins/}")
  else mapfile -t scenes < <(cd "$TAPES/plugins" && ls -- *.tape | sed 's/\.tape$//; s#^#plugins/#'); fi
elif (( $# )); then
  scenes=("$@")
else
  mapfile -t scenes < <(cd "$TAPES" && ls -- *.tape | sed 's/\.tape$//' | grep -v -e "^common$" -e "^smoke$")
fi

# ── record ──────────────────────────────────────────────────────────────────
record_one() {
  local scene="$1" tape="$TAPES/$1.tape"
  [[ -f "$tape" ]] || die "no tape: $tape"
  # A plugin scene (`plugins/<name>`) runs the same pipeline; only where its files land differs.
  local name="${scene##*/}" gif_out="$OUT_GIF/${scene##*/}.gif" vid="${scene##*/}"
  if [[ "$scene" == plugins/* ]]; then
    gif_out="$CORE/../plugins/navig-$name/docs/demo.gif"; vid="plugin-$name"
    mkdir -p "$(dirname "$gif_out")"
  fi
  local dir="$WORK/$vid"; mkdir -p "$dir"
  # The tape's `Output` lines are relative to the cwd VHS runs in; `Source` lines resolve
  # relative to the tape, so copy the common header next to it.
  cp "$tape" "$TAPES/common.tape" "$dir/"
  scene="$name"
  log "recording $scene"
  # Every scene starts from the same fixtures: the ledger, active host and lab spaces of a
  # previous scene must not leak into this one (the hero would otherwise show a ledger of
  # whatever ran before it).
  source "$HERE/sandbox.sh" --reset
  # VHS spawns the shell in ITS cwd; the tapes `cd` into the sandbox work dir themselves (Hidden).
  ( cd "$dir" && vhs "$scene.tape" >"$dir/vhs.log" 2>&1 ) || { cat "$dir/vhs.log"; die "vhs failed on $scene"; }
  [[ -f "$dir/$scene.gif" ]] || { cat "$dir/vhs.log"; die "vhs produced no gif for $scene"; }
  [[ -f "$dir/$scene.mp4" ]] || die "vhs produced no mp4 for $scene"
  [[ -f "$dir/$scene.webm" ]] || die "vhs produced no webm for $scene"

  # GIF: VHS's ffmpeg palette output is fine but big; gifski from the mp4 is smaller and sharper.
  if command -v gifski >/dev/null; then
    ffmpeg -loglevel error -y -i "$dir/$scene.mp4" -vf "fps=15" "$dir/f-%05d.png"
    gifski --quiet --fps 15 --quality 85 --width 1200 -o "$dir/$scene.opt.gif" "$dir"/f-*.png
    rm -f "$dir"/f-*.png
    mv "$dir/$scene.opt.gif" "$dir/$scene.gif"
  fi

  cp "$dir/$scene.gif" "$gif_out"
  cp "$dir/$scene.mp4" "$OUT_VIDEO/$vid.mp4"
  cp "$dir/$scene.webm" "$OUT_VIDEO/$vid.webm"
  # first/last frame for eyeballing — from the mp4 (seekable; a GIF has no reliable duration)
  ffmpeg -loglevel error -y -i "$dir/$scene.mp4" -vf "select=eq(n\,0)" -vframes 1 "$OUT_FRAMES/$vid-first.png"
  ffmpeg -loglevel error -y -sseof -0.3 -i "$dir/$scene.mp4" -update 1 -vframes 1 "$OUT_FRAMES/$vid-last.png"

  local bytes; bytes="$(fsize "$gif_out")"
  local cap=$SCENE_MAX_BYTES; [[ "$scene" == hero ]] && cap=$HERO_MAX_BYTES
  (( bytes <= cap )) || die "$scene.gif is $((bytes/1024)) KB > $((cap/1024)) KB — shorten the tape or lower fps"
  ok "$vid.gif $((bytes/1024)) KB · $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$dir/$scene.mp4" | cut -d. -f1)s"
}

if (( ! MANIFEST_ONLY )); then
  for s in "${scenes[@]}"; do record_one "$s"; done
fi
# Plugin demos are not part of core's gallery manifest — leave it untouched.
if (( PLUGINS_ONLY )); then ok "plugin demos → plugins/navig-*/docs/demo.gif"; exit 0; fi

# ── manifest (provenance) ───────────────────────────────────────────────────
navig_ver="$(navig --version 2>/dev/null | tail -1)"
commit="$(showcase_commit)"
{
  printf '{\n  "recorded_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '  "navig_version": "%s",\n  "commit": "%s",\n' "$navig_ver" "$commit"
  printf '  "vhs": "%s",\n  "ffmpeg": "%s",\n' "$(vhs --version | head -1)" "$(ffmpeg -version | head -1 | cut -d' ' -f3)"
  printf '  "chrome": "%s",\n' "$(google-chrome --version 2>/dev/null)"
  printf '  "font": "JetBrainsMono Nerd Font",\n  "sandbox": "sandbox.sh (isolated NAVIG_* dirs, three lab hosts on a local sshd)",\n'
  printf '  "assets": [\n'
  first=1
  for f in "$OUT_GIF"/*.gif; do
    n="$(basename "$f")"
    (( first )) || printf ',\n'; first=0
    printf '    {"file": "%s", "bytes": %s, "sha256": "%s"}' "$n" "$(fsize "$f")" "$(sha256 "$f")"
  done
  printf '\n  ]\n}\n'
} > "$MANIFEST"
ok "manifest → $MANIFEST"
ok "videos → $OUT_VIDEO (upload to the GitHub Release; not committed)"
