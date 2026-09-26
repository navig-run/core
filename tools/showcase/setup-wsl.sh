#!/usr/bin/env bash
# NAVIG showcase — WSL (Ubuntu) environment for recording the README GIFs/videos.
#
# Runs INSIDE WSL as root (the default WSL user on the maintainer box). Idempotent:
# every step checks before it acts, so re-running is cheap and safe.
#
#   wsl -e bash core/tools/showcase/setup-wsl.sh            # install everything
#   wsl -e bash core/tools/showcase/setup-wsl.sh --teardown # stop the lab sshd, drop /etc/hosts aliases
#   wsl -e bash core/tools/showcase/setup-wsl.sh --sync     # only re-sync navig source + reinstall
#
# What it builds (all under /root, nothing touches the Windows side):
#   /usr/local/bin/vhs           charmbracelet VHS (the tape → gif/mp4/webm recorder)
#   ttyd · ffmpeg · google-chrome  VHS's runtime (it drives a headless Chrome over ttyd)
#   ~/.local/share/fonts          JetBrainsMono Nerd Font (the font the README tells users to install)
#   ~/navig-src/core              rsync of THIS checkout's core/ (the /mnt/e 9P mount is too slow to import from)
#   ~/navig-venv                  uv-managed Python 3.13 venv with `navig` installed from ~/navig-src
#   ~/navig-lab/                  a real sshd on :2222 (key-only, user `ops`) + /etc/hosts aliases
#                                 prod-01.lab / staging-01.lab / db-01.lab → 127.0.0.11/12/13
#
# Why a real sshd: the recordings must show REAL output (see docs/showcase/README.md — nothing is
# fabricated). `navig run 'df -h' --host prod-01` in the GIFs is a genuine SSH round-trip into this
# box; the lab hostnames are labels for that, not remote machines.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_SRC="$(cd "$HERE/../.." && pwd)"          # <checkout>/core
SRC_DIR="$HOME/navig-src"
VENV="$HOME/navig-venv"
LAB="$HOME/navig-lab"
FONT_DIR="$HOME/.local/share/fonts"
# ⚠ 0.12.0 (2026-09-09) exits 0 having rendered NOTHING here — ttyd + headless Chrome start, no
# frame is ever captured, ffmpeg is never invoked, no file appears. 0.11.0 renders. Bisected
# with a one-line `echo hello` tape; re-test before bumping.
VHS_VERSION="${VHS_VERSION:-0.11.0}"
PY_VERSION="${NAVIG_SHOWCASE_PYTHON:-3.13}"
SSH_PORT=2222
export DEBIAN_FRONTEND=noninteractive

log()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m⚠\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" == 0 ]] || die "run as root inside WSL (the maintainer's WSL default user is root)"

# ── teardown ────────────────────────────────────────────────────────────────
teardown() {
  if [[ -f "$LAB/sshd.pid" ]] && kill -0 "$(cat "$LAB/sshd.pid")" 2>/dev/null; then
    kill "$(cat "$LAB/sshd.pid")" && ok "lab sshd stopped"
  else
    ok "lab sshd not running"
  fi
  rm -f "$LAB/sshd.pid"
  if grep -q 'navig-showcase-lab' /etc/hosts; then
    sed -i '/navig-showcase-lab/d' /etc/hosts && ok "/etc/hosts lab aliases removed"
  fi
}
if [[ "${1:-}" == "--teardown" ]]; then teardown; exit 0; fi

# ── navig source + venv ─────────────────────────────────────────────────────
sync_navig() {
  command -v rsync >/dev/null || apt-get install -y -qq rsync >/dev/null
  log "syncing $CORE_SRC → $SRC_DIR/core"
  mkdir -p "$SRC_DIR/plugins"
  rsync_opts=(-a --delete
    --exclude '.dev/' --exclude '__pycache__/' --exclude '*.pyc' --exclude '.pytest_cache/'
    --exclude '.ruff_cache/' --exclude '.mypy_cache/' --exclude 'build/' --exclude 'dist/'
    --exclude '*.egg-info/' --exclude '.navig/')
  rsync "${rsync_opts[@]}" "$CORE_SRC/" "$SRC_DIR/core/"
  # core's pyproject resolves its two HARD deps (navig-vault, navig-contacts) from
  # `../plugins/<name>` via [tool.uv.sources], so the editable install needs them beside it.
  for plug in navig-vault navig-contacts; do
    rsync "${rsync_opts[@]}" "$CORE_SRC/../plugins/$plug/" "$SRC_DIR/plugins/$plug/"
  done
  if ! command -v uv >/dev/null && [[ ! -x "$HOME/.local/bin/uv" ]]; then
    log "installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  fi
  export PATH="$HOME/.local/bin:$PATH"
  if [[ ! -x "$VENV/bin/python" ]]; then
    log "creating venv (python $PY_VERSION)"
    uv python install "$PY_VERSION" >/dev/null
    uv venv --python "$PY_VERSION" "$VENV" >/dev/null
  fi
  log "installing navig into the venv (editable, from the rsync copy)"
  uv pip install --python "$VENV/bin/python" -q -e "$SRC_DIR/core"
  "$VENV/bin/navig" --version >/dev/null || die "navig does not run from $VENV"
  ok "navig $("$VENV/bin/navig" --version 2>/dev/null | tail -1) in $VENV"
}
if [[ "${1:-}" == "--sync" ]]; then sync_navig; exit 0; fi

# ── apt packages ────────────────────────────────────────────────────────────
need_pkgs=()
# fonts-noto-color-emoji: navig prints emoji (doctor's stethoscope, the dashboard's kraken)
# and a WSL image ships no emoji face, so Chrome rendered every one as a tofu box in the GIFs.
for p in ttyd ffmpeg openssh-server fontconfig fonts-noto-color-emoji rsync curl unzip jq git; do
  dpkg -s "$p" >/dev/null 2>&1 || need_pkgs+=("$p")
done
if ((${#need_pkgs[@]})); then
  log "apt: ${need_pkgs[*]}"
  apt-get update -qq
  apt-get install -y -qq "${need_pkgs[@]}" >/dev/null
fi
ok "ttyd $(ttyd --version 2>&1 | head -1) · ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"

# ── Chrome for VHS (rod needs a real browser; Ubuntu's chromium is a snap, useless in WSL) ──
if ! command -v google-chrome >/dev/null; then
  log "installing google-chrome-stable (VHS renders through headless Chrome)"
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor --yes -o /etc/apt/keyrings/google-chrome.gpg
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list
  apt-get update -qq
  apt-get install -y -qq google-chrome-stable >/dev/null
fi
ok "$(google-chrome --version 2>/dev/null)"

# ── VHS ─────────────────────────────────────────────────────────────────────
if ! command -v vhs >/dev/null || [[ "$(vhs --version 2>/dev/null | grep -o '[0-9][0-9.]*' | head -1)" != "$VHS_VERSION" ]]; then
  log "installing vhs $VHS_VERSION"
  tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/charmbracelet/vhs/releases/download/v${VHS_VERSION}/vhs_${VHS_VERSION}_Linux_x86_64.tar.gz" \
    | tar -xz -C "$tmp"
  install -m 0755 "$(find "$tmp" -type f -name vhs | head -1)" /usr/local/bin/vhs
  rm -rf "$tmp"
fi
ok "vhs $(vhs --version 2>&1 | head -1)"

# ── gifski (optional: record.sh falls back to VHS's own ffmpeg GIF when absent) ──
GIFSKI_VERSION="${GIFSKI_VERSION:-1.34.0}"
if ! command -v gifski >/dev/null; then
  log "installing gifski $GIFSKI_VERSION"
  tmp="$(mktemp -d)"
  if curl -fsSL "https://github.com/ImageOptim/gifski/releases/download/${GIFSKI_VERSION}/gifski-${GIFSKI_VERSION}.tar.xz"        | tar -xJ -C "$tmp" 2>/dev/null && bin="$(find "$tmp" -type f -path '*linux*' -name gifski | head -1)" && [[ -n "$bin" ]]; then
    install -m 0755 "$bin" /usr/local/bin/gifski
  else
    warn "gifski download failed — GIFs will come from ffmpeg's palette pass instead (larger)"
  fi
  rm -rf "$tmp"
fi
command -v gifski >/dev/null && ok "gifski $(gifski --version 2>&1 | head -1)"

# ── font ────────────────────────────────────────────────────────────────────
if ! ls "$FONT_DIR"/JetBrainsMonoNerd/*.ttf >/dev/null 2>&1; then
  log "installing JetBrainsMono Nerd Font"
  mkdir -p "$FONT_DIR"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/JetBrainsMono.zip" \
    https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.zip
  unzip -q -o "$tmp/JetBrainsMono.zip" -d "$FONT_DIR/JetBrainsMonoNerd" '*.ttf'
  rm -rf "$tmp"
  fc-cache -f >/dev/null
fi
ok "font: $(fc-list | grep -ci 'JetBrainsMono Nerd Font') faces"

sync_navig

# ── lab sshd (:2222, key-only, user `ops`) + hostname aliases ───────────────
mkdir -p "$LAB" /run/sshd
id ops >/dev/null 2>&1 || useradd -m -s /bin/bash ops
# `useradd` leaves the account LOCKED (`!` in shadow) and with UsePAM=no sshd refuses locked
# accounts even for key auth ("Permission denied (publickey)"). `*` = no password, not locked.
usermod -p '*' ops
if [[ ! -f "$LAB/id_ed25519" ]]; then
  ssh-keygen -q -t ed25519 -N '' -C 'navig-showcase' -f "$LAB/id_ed25519"
fi
install -d -m 0700 -o ops -g ops /home/ops/.ssh
install -m 0600 -o ops -g ops "$LAB/id_ed25519.pub" /home/ops/.ssh/authorized_keys
ssh-keygen -A >/dev/null 2>&1 || true     # host keys, once
cat > "$LAB/sshd_config" <<CFG
Port $SSH_PORT
ListenAddress 0.0.0.0
PidFile $LAB/sshd.pid
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers ops
UsePAM no
PrintMotd no
LogLevel ERROR
CFG
if ! grep -q 'navig-showcase-lab' /etc/hosts; then
  cat >> /etc/hosts <<HOSTS
127.0.0.11 prod-01.lab      # navig-showcase-lab
127.0.0.12 staging-01.lab   # navig-showcase-lab
127.0.0.13 db-01.lab        # navig-showcase-lab
HOSTS
fi
if ! { [[ -f "$LAB/sshd.pid" ]] && kill -0 "$(cat "$LAB/sshd.pid")" 2>/dev/null; }; then
  /usr/sbin/sshd -f "$LAB/sshd_config"
fi
# known_hosts for the three aliases so the recordings never show a host-key prompt
mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
for h in prod-01.lab staging-01.lab db-01.lab; do
  grep -q "^\[$h\]:$SSH_PORT" "$HOME/.ssh/known_hosts" 2>/dev/null \
    || ssh-keyscan -p "$SSH_PORT" "$h" 2>/dev/null >> "$HOME/.ssh/known_hosts"
done
if out="$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -i "$LAB/id_ed25519" -p "$SSH_PORT" ops@prod-01.lab uptime 2>&1)"; then
  ok "lab sshd: ops@prod-01.lab:$SSH_PORT → $out"
else
  die "lab sshd is not answering: $out"
fi

ok "showcase environment ready — next: wsl -e bash core/tools/showcase/record.sh"
