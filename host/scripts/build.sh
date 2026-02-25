#!/usr/bin/env bash
# scripts/build.sh — build navig-host for the current platform (or cross-compile)
#
# Usage:
#   ./scripts/build.sh                     # normal build with systray (GUI)
#   ./scripts/build.sh --headless          # server/CI build (no systray dependency)
#   GOOS=linux GOARCH=amd64 ./scripts/build.sh --headless   # cross-compile
#   ./scripts/build.sh --race              # race-detector build for dev

set -euo pipefail

MODULE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${MODULE_ROOT}/bin"
BINARY="navig-host"
PACKAGE="navig-core/host/cmd/navig-host"

HEADLESS=false
RACE=""

for arg in "$@"; do
  case $arg in
    --headless) HEADLESS=true ;;
    --race)     RACE="-race"  ;;
    *)          echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# ── Build tags ────────────────────────────────────────────────────────────────
TAGS=""
if $HEADLESS; then
  TAGS="headless"
  BINARY="navig-host-headless"
fi

# ── Version info injected at link time ───────────────────────────────────────
VERSION=$(git -C "$MODULE_ROOT" describe --tags --always --dirty 2>/dev/null || echo "dev")
COMMIT=$(git -C "$MODULE_ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

LDFLAGS="-s -w \
  -X main.version=${VERSION} \
  -X main.commit=${COMMIT} \
  -X main.buildDate=${BUILD_DATE}"

mkdir -p "$OUT_DIR"

TAG_ARG=""
if [ -n "$TAGS" ]; then
  TAG_ARG="-tags ${TAGS}"
fi

echo "Building ${BINARY}  (tags='${TAGS}' version=${VERSION} headless=${HEADLESS}) …"
cd "$MODULE_ROOT"

# shellcheck disable=SC2086  # intentional word-splitting for flags
go build $RACE $TAG_ARG \
  -ldflags "${LDFLAGS}" \
  -o "${OUT_DIR}/${BINARY}" \
  "${PACKAGE}"

echo "Done → ${OUT_DIR}/${BINARY}"
