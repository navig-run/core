#!/usr/bin/env bats
# bats-core tests for install.sh
# Run: bats tests/install.bats
#
# Requires: bats-core (https://github.com/bats-core/bats-core)
# Install:  npm install -g bats  OR  brew install bats-core

REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/.." && pwd)"
INSTALL_SH="$REPO_ROOT/install.sh"

# ---------------------------------------------------------------------------
# 1. Dry-run exits 0 and prints expected marker
# ---------------------------------------------------------------------------
@test "dry-run exits 0 and reports dry-run mode" {
    run bash "$INSTALL_SH" --dry-run
    [ "$status" -eq 0 ]
    [[ "$output" =~ [Dd]ry.?run ]]
}

# ---------------------------------------------------------------------------
# 2. Idempotent: two dry-runs both succeed
# ---------------------------------------------------------------------------
@test "dry-run is idempotent across two runs" {
    run bash "$INSTALL_SH" --dry-run
    [ "$status" -eq 0 ]
    run bash "$INSTALL_SH" --dry-run
    [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# 3. Invalid action exits non-zero with error context
# ---------------------------------------------------------------------------
@test "unsupported action flag exits non-zero" {
    run bash "$INSTALL_SH" --action bogus
    [ "$status" -ne 0 ]
    [[ "$output" =~ [Uu]nsupported|[Uu]nknown|[Ii]nvalid|bogus ]]
}

# ---------------------------------------------------------------------------
# 4. No system python is NOT an error — install.sh bundles its own runtime.
#
# This test used to assert the opposite ("warns or exits non-zero when python3 is
# absent"), which was true before the installer grew its uv-managed runtime: it now
# downloads uv and runs `uv python install "$PYTHON_SERIES"` into ~/.navig/runtime,
# deliberately never touching system Python. So a machine with no python at all is a
# SUPPORTED case, and demanding a warning asserted a requirement that no longer
# exists. Nobody ever saw it fail because the file did not parse (see the note below).
# Re-pointed at the real contract rather than deleted: "you do not need Python
# installed to install navig" is a product promise worth a guard.
# ---------------------------------------------------------------------------
# NOTE: bats test names must be ASCII. bats mangles the name into a shell function
# identifier, and a non-ASCII byte (an em dash here) produces
# `bats: unknown test name test_..._\342-80-94_...` — the test is SKIPPED and only the
# "Executed 4 instead of expected 5 tests" warning says so, below a screen of `ok`s.
@test "a machine with no python on PATH can still install - runtime is bundled" {
    # Strip real python from PATH by RESOLVING it, not by matching the directory name.
    # The old heuristic dropped entries containing "python"/"homebrew", which leaves
    # python reachable on Windows via ...\AppData\Local\Microsoft\WindowsApps (no
    # "python" in the path) — so the premise silently did not hold and the assertion
    # below failed for a reason that had nothing to do with the installer.
    local stripped_path="$PATH" found dir guard=0
    while found="$(PATH="$stripped_path" command -v python3 2>/dev/null \
                   || PATH="$stripped_path" command -v python 2>/dev/null)" \
          && [ -n "$found" ] && [ "$guard" -lt 32 ]; do
        dir="$(dirname "$found")"
        stripped_path="$(printf '%s' "$stripped_path" | tr ':' '\n' \
            | grep -vxF "$dir" | tr '\n' ':' | sed 's/:$//')"
        guard=$((guard + 1))
    done
    # If python is somehow still reachable, this test cannot assert what it claims —
    # say so rather than reporting a failure the installer did not cause.
    if PATH="$stripped_path" command -v python3 >/dev/null 2>&1 \
       || PATH="$stripped_path" command -v python >/dev/null 2>&1; then
        skip "could not remove python from PATH on this platform"
    fi
    run env PATH="$stripped_path" bash "$INSTALL_SH" --dry-run
    [ "$status" -eq 0 ]
    # Reaching the completion marker (not just exiting 0 early) is what makes this
    # non-vacuous: it fails the moment anyone reintroduces a hard system-python
    # requirement into the pre-flight path.
    #
    # ⚠ A regex written inline after `=~` is parsed by BASH, so a space in the pattern
    # is a syntax error ("unexpected token `found'") that kills the whole FILE — bats
    # then reports `not ok 1 bats-gather-tests` and runs none of the 5 tests. That is
    # exactly what shipped here. Keep any pattern with a space in a variable.
    local done_pat='[Dd]ry.?run'
    [[ "$output" =~ $done_pat ]]
}

# ---------------------------------------------------------------------------
# 5. NAVIG_DEV_SYNC=1 with no navig-www present: non-fatal (installer succeeds)
# ---------------------------------------------------------------------------
@test "NAVIG_DEV_SYNC=1 without navig-www is non-fatal in dry-run" {
    run env NAVIG_DEV_SYNC=1 bash "$INSTALL_SH" --dry-run
    # Should still exit 0 (dev sync failure must not kill the main install)
    [ "$status" -eq 0 ]
}
