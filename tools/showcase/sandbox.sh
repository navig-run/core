#!/usr/bin/env bash
# NAVIG showcase — the isolated navig "home" every recording runs against.
#
# Sourced by record.sh (and usable interactively: `source sandbox.sh && navig host list`).
# Everything navig reads or writes is redirected under $DEMO so a recording can never
# see the maintainer's real hosts, tokens, ledger or logs — and the fixtures below are
# the ONLY state the GIFs show. Idempotent; `--reset` wipes and rebuilds.
#
#   bash sandbox.sh            # (re)build fixtures, print the env
#   bash sandbox.sh --reset    # wipe $DEMO first
set -euo pipefail

DEMO="${NAVIG_SHOWCASE_HOME:-$HOME/navig-demo}"
LAB="$HOME/navig-lab"
VENV="$HOME/navig-venv"

if [[ "${1:-}" == "--reset" ]]; then rm -rf "$DEMO"; fi
mkdir -p "$DEMO"/{cfg/hosts,data,log,cache,vault,store,work}

# ── env (exported for the caller: record.sh sources this file) ──────────────
export PATH="$VENV/bin:$PATH"
export NAVIG_CONFIG_DIR="$DEMO/cfg"
export NAVIG_DATA_DIR="$DEMO/data"
export NAVIG_LOG_DIR="$DEMO/log"
export NAVIG_CACHE_DIR="$DEMO/cache"
export NAVIG_VAULT_DIR="$DEMO/vault"
export NAVIG_STORE_DIR="$DEMO/store"
export NAVIG_SKIP_ONBOARDING=1
export NAVIG_NO_TELEMETRY=1
export NAVIG_LAUNCHER=legacy          # no fzf/arrow pickers mid-recording
export NAVIG_SESSION_ID=navig-showcase  # one stable identity for the host lock across every
                                        # recorded command (a tty vs non-tty run would otherwise
                                        # look like two sessions and block each other for 60m)
export COLUMNS=110
export TERM=xterm-256color
export LANG=C.UTF-8 LC_ALL=C.UTF-8
export HOME_DEMO_WORK="$DEMO/work"

# ── fixtures: three lab hosts (real sshd on :2222, see setup-wsl.sh) ────────
host_yaml() { # name host user
  cat > "$NAVIG_CONFIG_DIR/hosts/$1.yaml" <<YAML
name: $1
host: $2
port: 2222
user: $3
ssh_key: $LAB/id_ed25519
YAML
}
host_yaml prod-01    prod-01.lab    ops
host_yaml staging-01 staging-01.lab ops
host_yaml db-01      db-01.lab      ops

# a project folder for the spaces scene and a lintable skill for the skills scene
mkdir -p "$DEMO/work/blog" "$DEMO/work/demo-skill"
if [[ ! -f "$DEMO/work/demo-skill/SKILL.md" ]]; then
  cat > "$DEMO/work/demo-skill/SKILL.md" <<'MD'
---
name: rotate-logs
description: Rotate and compress application logs older than a week on the active host, then report freed space. Use when the user says "rotate logs", "clean up logs" or disk is filling under /var/log.
---

# Rotate logs

1. `navig run 'sudo find /var/log -name "*.log" -mtime +7 -exec gzip {} +'`
2. `navig run 'df -h /var/log'`
MD
fi

# the node identity `navig whoami` renders — created by step 7 of the first-run wizard, which
# the sandbox skips (NAVIG_SKIP_ONBOARDING=1), so seed it the way that step does
if [[ ! -f "$NAVIG_CONFIG_DIR/state/entity.json" ]]; then
  "$VENV/bin/python" - <<'PY_SIGIL' >/dev/null 2>&1 || true
from navig.identity.sigil_store import ensure_sigil
ensure_sigil()
PY_SIGIL
fi

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'sandbox: %s\n' "$DEMO"
  env | grep -E '^NAVIG_' | sort
fi
