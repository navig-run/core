#!/usr/bin/env bats
# tests/installer/install.bats
# Safety tests for install.sh's destructive path.
#
# This file used to hold 7 unit tests against `ensure_pip_user_bin_on_path`,
# `check_install_pipx` and `install_navig_git` — the pip/pipx-era installer. All three
# functions were deleted when install.sh moved to a self-contained uv runtime, so those
# tests could only ever fail with `command not found` (status 127). Nobody saw that,
# because the sibling suite had a bash SYNTAX ERROR and nothing in CI ran either file.
# An eighth test asserted an inline COPY of the entry guard rather than install.sh
# itself, so it passed while proving nothing.
#
# What remains is what is both still real and safe to execute: the uninstaller must not
# run unconfirmed, must not kill bystanders, and must actually dispatch every action it
# advertises.
#
# ⚠ Do NOT "restore" a test that calls `uninstall_navig` with `_YES=1`. Its first step is
# `_stop_navig_background`, which SIGTERMs live NAVIG daemons and stops systemd units on
# whatever machine runs the suite — including the operator's own brain. The cancel path
# below never reaches it, and the dispatch test stubs it out.
# (Until 2026-08-07 that step was `pkill -f 'navig'`, i.e. every process whose command
# line merely CONTAINED "navig" — the operator's editor and the agent session too. That
# is fixed; the tests below are what keep it fixed.)
#
# Run: bats tests/installer/install.bats

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../.." && pwd)"

setup() {
    # A temp HOME so nothing here can touch real dot-files, and the entry-point guard
    # so sourcing install.sh defines its functions without running the installer.
    export HOME
    HOME="$(mktemp -d)"
    export NAVIG_INSTALL_SH_NO_RUN=1
}

teardown() {
    rm -rf "$HOME"
}

load_functions() {
    # shellcheck disable=SC1090
    source "$REPO_ROOT/install.sh"
}

@test "uninstall refuses to proceed without confirmation when there is no tty" {
    load_functions
    # A profile carrying the sentinel the uninstaller would strip.
    cat > "$HOME/.bashrc" <<'EOF'
# existing content
# NAVIG CLI
export PATH="/home/user/.local/bin:$PATH"
# post-navig content
EOF

    # No tty and no -y/--yes: `read < /dev/tty` fails, the reply defaults to "n", and
    # the function must return 0 having changed NOTHING. Returning 0 matters as much as
    # not deleting: a declined uninstall is a normal outcome, not an error.
    run uninstall_navig
    [ "$status" -eq 0 ]
    [[ "$output" == *"Cancelled"* ]]

    # The profile must be byte-for-byte untouched — this is the assertion that would
    # fail if the prompt were ever made to default to "yes".
    grep -q "# NAVIG CLI" "$HOME/.bashrc"
    grep -q "post-navig content" "$HOME/.bashrc"
}

@test "the uninstaller never kills its own shell" {
    # `pkill -f` matches the FULL command line, so an installer run by absolute path from
    # a clone — `bash /home/me/navig-core/install.sh --action uninstall` — matched its own
    # pattern and SIGTERMed itself partway through, leaving the runtime removed and the
    # profile lines in place with no error. Measured on Linux before the fix: the script
    # died mid-run, printing nothing after its first line.
    #
    # This asserts the mechanism that prevents it, and is deliberately READ-ONLY: it does
    # not call _stop_navig_background, whose remaining steps would kill real navig
    # processes and stop systemd units on the machine running the suite.
    load_functions

    local chain
    chain=" $(_navig_self_chain | tr '\n' ' ') "
    # This shell must be in the skip set — it is the process the old code destroyed.
    # Asserting only `$$` is deliberate: the ancestor walk needs `ps -o ppid=`, which
    # Linux and macOS have and MSYS/Git Bash does not, so there the chain degrades to a
    # single entry. That degradation is safe (the installer's own shell is still spared)
    # and untestable as a fixed length — measured on WSL the chain is 4 deep, under Git
    # Bash it is 1. install.sh targets Linux/macOS; Windows uses install.ps1.
    [[ "$chain" == *" $$ "* ]]

    # The bare form must not come back. Source-level, because the behavioural check
    # cannot be run without killing things.
    local fn
    fn=$(sed -n '/^_stop_navig_background() {/,/^}/p' "$REPO_ROOT/install.sh")
    [ -n "$fn" ]
    [[ "$fn" != *"pkill"* ]]
    [[ "$fn" == *"_navig_self_chain"* ]]
}

# ── Which processes an uninstall is allowed to kill ──────────────────────────
#
# `pgrep -f` matches the FULL command line, so the original `pgrep -f 'navig'` selected
# every process that merely MENTIONED the word: an editor holding a file in a navig
# checkout, a terminal running a navig script, the agent session driving the install.
# All of them were SIGTERMed by `install.sh --action uninstall`.
#
# These two tests are the behavioural pair: the pattern must still match every real NAVIG
# process shape, and must match none of the bystanders. Both run against the pattern
# itself, so nothing is signalled.

@test "the process pattern matches every real navig process shape" {
    load_functions
    local pattern cmd
    pattern="$(_navig_proc_pattern)"
    [ -n "$pattern" ]

    # The managed runtime, module launches, and a navig installed anywhere else.
    while IFS= read -r cmd; do
        [ -n "$cmd" ] || continue
        printf '%s' "$cmd" | grep -Eq "$pattern" || {
            printf 'pattern did not match a real navig process: %s\n' "$cmd" >&2
            printf 'pattern: %s\n' "$pattern" >&2
            return 1
        }
    done <<EOF
$HOME/.navig/runtime/venv/bin/python -m navig.daemon.entry
$HOME/.navig/runtime/venv/bin/python -m navig gateway start
$HOME/.navig/runtime/venv/bin/python -m navig.daemon.telegram_worker
$HOME/.local/bin/navig gateway start
/usr/bin/python3 /usr/local/bin/navig gateway start
/opt/venv/bin/python -m navig
EOF
}

@test "the process pattern spares processes that only mention navig" {
    load_functions
    local pattern cmd
    pattern="$(_navig_proc_pattern)"

    # Every line here is a process an operator would be furious to lose, and every one of
    # them was killed by the old bare-word pattern. `navigation` is the near-miss that a
    # naive `-m navig` prefix still matched until the boundary was added.
    while IFS= read -r cmd; do
        [ -n "$cmd" ] || continue
        if printf '%s' "$cmd" | grep -Eq "$pattern"; then
            printf 'pattern would kill a bystander: %s\n' "$cmd" >&2
            printf 'pattern: %s\n' "$pattern" >&2
            return 1
        fi
    done <<EOF
nvim $HOME/projects/navig/core/install.sh
bash $HOME/navig-core/install.sh --action uninstall
node $HOME/projects/navig/scripts/ci-local.mjs
code $HOME/projects/navig
git -C $HOME/projects/navig status
python -m navigation.tools
less $HOME/projects/navig/README.md
EOF
}

@test "regex metacharacters in HOME are literals, not operators" {
    # $HOME is user-controlled, and unescaped it changes the pattern's MEANING: `te+st`
    # as a regex also matches `teeest`, so the uninstaller would start signalling other
    # users' processes. The decoy below differs from the real home only in the characters
    # that would have been operators, which is what makes this a discriminator rather
    # than a restatement.
    HOME="$HOME/te+st.d"
    mkdir -p "$HOME"
    load_functions
    local pattern decoy
    pattern="$(_navig_proc_pattern)"

    printf '%s' "$HOME/.navig/runtime/venv/bin/python -m navig.daemon.entry" \
        | grep -Eq "$pattern"

    decoy="${HOME/te+st.d/teeestXd}"
    run grep -Eq "$pattern" <<< "$decoy/.navig/runtime/venv/bin/python"
    [ "$status" -ne 0 ]
}

@test "a HOME that is not a valid regex still stops navig" {
    # The other half, and the worse one: `[` unescaped makes an unterminated bracket
    # expression, so the whole pattern is rejected and pgrep matches NOTHING. That fails
    # silently on the one path whose entire job is to stop things.
    HOME="$HOME/te[st.d"
    mkdir -p "$HOME"
    load_functions
    local pattern
    pattern="$(_navig_proc_pattern)"

    printf '%s' "$HOME/.navig/runtime/venv/bin/python -m navig.daemon.entry" \
        | grep -Eq "$pattern"
}

@test "the bare-word process pattern cannot come back" {
    # Source-level, because the behavioural check cannot be run without killing things.
    local fn bare_sq bare_dq
    fn=$(sed -n '/^_stop_navig_background() {/,/^}/p' "$REPO_ROOT/install.sh")
    [ -n "$fn" ]
    [[ "$fn" != *"pkill"* ]]
    # The two spellings that select every process merely mentioning navig.
    bare_sq="pgrep -f 'navig'"
    bare_dq='pgrep -f "navig"'
    [[ "$fn" != *"$bare_sq"* ]]
    [[ "$fn" != *"$bare_dq"* ]]
    [[ "$fn" == *"_navig_proc_pattern"* ]]
}

# ── Every advertised action must actually be dispatched ──────────────────────
#
# `reinstall` and `repair` were parsed, validated, documented in --help and named in the
# invalid-action error for the installer's whole life, and dispatched NOWHERE: both fell
# through to a plain overlay install, leaving exactly the stale runtime a reinstall exists
# to replace. install.ps1 has always had the branch. This matrix is what would have caught
# it, and it is behavioural rather than a source grep.

_dispatch_probe() {
    # Runs main() for one action with everything destructive stubbed out, and prints the
    # two observable things: whether the teardown ran, and whether the install was reached.
    # detect_os is the cut point, the first thing main does after the action branches.
    local probe="$BATS_TEST_TMPDIR/dispatch.sh"
    cat > "$probe" <<'PROBE_EOF'
export NAVIG_INSTALL_SH_NO_RUN=1
# shellcheck disable=SC1090
source "$1/install.sh"
unset NAVIG_INSTALL_SH_NO_RUN
mkdir -p "$RUNTIME_DIR"          # pretend a previous install is present
CALLS=""
uninstall_navig() { CALLS="$CALLS uninstall_navig($1)"; }
print_section() { :; }
print_header()  { :; }
detect_os() { printf 'TEARDOWN[%s] REACHED_INSTALL\n' "$CALLS"; exit 0; }
main --action "$2"
printf 'TEARDOWN[%s] NO_INSTALL\n' "$CALLS"
PROBE_EOF
    bash "$probe" "$REPO_ROOT" "$1"
}

@test "install does not tear anything down" {
    load_functions
    run _dispatch_probe install
    [ "$status" -eq 0 ]
    [[ "$output" == *"TEARDOWN[] REACHED_INSTALL"* ]]
}

@test "reinstall clears the previous install and then installs" {
    load_functions
    run _dispatch_probe reinstall
    [ "$status" -eq 0 ]
    # preserve_data=1: the runtime and shim go, the user's ~/.navig data stays.
    [[ "$output" == *"TEARDOWN[ uninstall_navig(1)] REACHED_INSTALL"* ]]
}

@test "repair is an alias for reinstall" {
    load_functions
    run _dispatch_probe repair
    [ "$status" -eq 0 ]
    [[ "$output" == *"TEARDOWN[ uninstall_navig(1)] REACHED_INSTALL"* ]]
    # And the predicate agrees, which is what install.ps1's Normalize-NavigAction does.
    _action_is_reinstall repair
    _action_is_reinstall reinstall
    ! _action_is_reinstall install
    ! _action_is_reinstall uninstall
}

@test "uninstall never reaches the install path" {
    load_functions
    run _dispatch_probe uninstall
    [ "$status" -eq 0 ]
    [[ "$output" != *"REACHED_INSTALL"* ]]
}

@test "reinstall on a machine with nothing installed is a plain install" {
    # No runtime dir and no shim: tearing down would print removal warnings for files that
    # were never there.
    load_functions
    local probe="$BATS_TEST_TMPDIR/fresh.sh"
    cat > "$probe" <<'FRESH_EOF'
export NAVIG_INSTALL_SH_NO_RUN=1
# shellcheck disable=SC1090
source "$1/install.sh"
unset NAVIG_INSTALL_SH_NO_RUN
CALLS=""
uninstall_navig() { CALLS="$CALLS uninstall_navig($1)"; }
print_section() { :; }
print_header()  { :; }
detect_os() { printf 'TEARDOWN[%s] REACHED_INSTALL\n' "$CALLS"; exit 0; }
main --action reinstall
FRESH_EOF
    run bash "$probe" "$REPO_ROOT"
    [ "$status" -eq 0 ]
    [[ "$output" == *"TEARDOWN[] REACHED_INSTALL"* ]]
}

@test "every action the installer accepts is documented and handled" {
    load_functions
    local action usage
    usage="$(show_usage)"
    [ -n "$_NAVIG_ACTIONS" ]

    for action in $_NAVIG_ACTIONS; do
        # Accepted by the validator...
        _action_is_valid "$action"
        # ...named in --help, so an action can never ship undocumented...
        [[ "$usage" == *"$action"* ]]
        # ...and reachable: the parser must accept it as a bare positional too.
        _ACTION="install"
        _parse_args "$action"
        [ "$_ACTION" = "$action" ]
    done

    # The inverse: an unknown action is rejected rather than silently treated as install.
    ! _action_is_valid "frobnicate"
    _ACTION="install"
    _parse_args "frobnicate"
    [ "$_ACTION" = "install" ]
}
