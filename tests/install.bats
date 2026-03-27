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
# 4. Missing python: warns or exits non-zero (does not silently succeed)
# ---------------------------------------------------------------------------
@test "installer warns when python3 is absent from PATH" {
    # Strip real python from PATH
    local stripped_path
    stripped_path=$(echo "$PATH" | tr ':' '\n' \
        | grep -v -i python | grep -v "homebrew" \
        | tr '\n' ':' | sed 's/:$//')
    run env PATH="$stripped_path" bash "$INSTALL_SH" --dry-run
    # Acceptable: either non-zero exit OR output contains a warning about python
    [[ "$status" -ne 0 || "$output" =~ [Pp]ython|[Nn]ot found|[Ww]arn|\[!!\] ]]
}

# ---------------------------------------------------------------------------
# 5. NAVIG_DEV_SYNC=1 with no navig-www present: non-fatal (installer succeeds)
# ---------------------------------------------------------------------------
@test "NAVIG_DEV_SYNC=1 without navig-www is non-fatal in dry-run" {
    run env NAVIG_DEV_SYNC=1 bash "$INSTALL_SH" --dry-run
    # Should still exit 0 (dev sync failure must not kill the main install)
    [ "$status" -eq 0 ]
}
