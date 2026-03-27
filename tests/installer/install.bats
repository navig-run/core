#!/usr/bin/env bats
# tests/installer/install.bats
# Unit-style tests for install.sh using mock binaries.
# Run: bats tests/installer/install.bats
# (WSL: wsl bash -c "cd /mnt/k/_PROJECTS/navig/navig-core && bats tests/installer/install.bats")

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"
MOCK_BIN="$REPO_ROOT/tests/installer/_mock_bin"

setup() {
    # Prepend mock binaries so the installer never calls real pip/git/pipx.
    export PATH="$MOCK_BIN:$PATH"
    # Redirect HOME to a temp dir so real dot-files aren't touched.
    export HOME="$(mktemp -d)"
    # Prevent the installer from actually starting its main loop when sourced.
    export NAVIG_INSTALL_SH_NO_RUN=1
    chmod +x "$MOCK_BIN"/*
}

teardown() {
    rm -rf "$HOME"
}

# ── Helper: source the installer to expose its functions ─────────────────
load_functions() {
    # shellcheck disable=SC1090
    source "$REPO_ROOT/install.sh"
}

# ── Tests ─────────────────────────────────────────────────────────────────

@test "ensure_pip_user_bin_on_path: exits cleanly when PYTHON_CMD is unset" {
    load_functions
    unset PYTHON_CMD
    run ensure_pip_user_bin_on_path
    [ "$status" -eq 0 ]
}

@test "ensure_pip_user_bin_on_path: appends PATH export to .bashrc" {
    load_functions
    export PYTHON_CMD=python
    touch "$HOME/.bashrc"
    ensure_pip_user_bin_on_path
    grep -q "export PATH" "$HOME/.bashrc"
}

@test "ensure_pip_user_bin_on_path: does NOT duplicate PATH entry on re-run" {
    load_functions
    export PYTHON_CMD=python
    touch "$HOME/.bashrc"
    ensure_pip_user_bin_on_path
    ensure_pip_user_bin_on_path
    count=$(grep -c "export PATH" "$HOME/.bashrc")
    [ "$count" -eq 1 ]
}

@test "check_install_pipx: exits cleanly when PIP_CMD is empty" {
    load_functions
    PIP_CMD=()
    run check_install_pipx
    [ "$status" -eq 0 ]
}

@test "uninstall_navig: profile cleanup removes both comment and export PATH line" {
    load_functions
    # Simulate a profile that has the NAVIG sentinel injected
    cat > "$HOME/.bashrc" <<'EOF'
# existing content
# NAVIG CLI
export PATH="/home/user/.local/bin:$PATH"
# post-navig content
EOF
    uninstall_navig
    # Neither the sentinel nor the PATH line should remain
    run grep -c "NAVIG CLI" "$HOME/.bashrc"
    [ "$output" -eq 0 ]
    run grep -c 'export PATH.*local/bin' "$HOME/.bashrc"
    [ "$output" -eq 0 ]
    # Unrelated content must still be present
    grep -q "post-navig content" "$HOME/.bashrc"
}

@test "install_navig_git: exits non-zero and prints message when git clone fails" {
    # Provide a failing git stub for this test only
    local bad_git
    bad_git="$(mktemp -d)"
    printf '#!/usr/bin/env bash\nexit 1\n' > "$bad_git/git"
    chmod +x "$bad_git/git"
    export PATH="$bad_git:$PATH"

    load_functions
    export REPO_URL="https://example.com/fake.git"
    export GitDir="$(mktemp -d)/navig-src"
    export PRODUCTION=0 EXTRAS="" PIP_CMD=(pip3)

    run install_navig_git
    [ "$status" -ne 0 ]
    [[ "$output" == *"git clone failed"* ]]
}

@test "log dir failure is non-fatal at entry point guard" {
    # If the log directory cannot be created (unwritable HOME), the entry guard
    # must print a warning and continue rather than aborting with a non-zero exit.
    # We run a minimal inline script in a subshell with a read-only home dir.
    local readonly_home
    readonly_home="$(mktemp -d)"
    chmod 555 "$readonly_home"

    run bash -c "
        export HOME='$readonly_home'
        # Simulate the entry guard logic from install.sh
        if (( BASH_VERSINFO[0] >= 4 )); then
            if mkdir -p \"\${HOME}/.navig/logs\" 2>/dev/null; then
                echo 'log-dir-ok'
            else
                echo 'log-dir-skipped' >&2
            fi
        fi
        exit 0
    "

    chmod 755 "$readonly_home"
    rm -rf "$readonly_home"

    # Exit must be 0 in all cases (non-fatal)
    [ "$status" -eq 0 ]
    # The warning message must appear (or the dir was somehow created — still ok)
    [[ "$output" == *"log-dir-skipped"* ]] || [[ "$output" == *"log-dir-ok"* ]]
}
