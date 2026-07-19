"""A hardcoded ``Path.home() / ".navig"`` ignores the configured NAVIG dir.

Everything a navig install owns — config, vault, license, spaces, sessions, the agent's
pid — hangs off :func:`navig.platform.paths.config_dir`, which honours ``NAVIG_CONFIG_DIR``.
Code that reaches for ``Path.home() / ".navig"`` instead silently bypasses that, and the
result is a **split brain**: one half of a feature writes to the configured dir, the other
half reads from the real home.

That is not theoretical. The agent wrote its pid to ``Path.home()/".navig"/"agent"`` while
all three readers (``navig agent stop``, ``navig agent status``, the MCP agent tool) read
``config_dir()/"agent"``. Under a custom ``NAVIG_CONFIG_DIR`` the agent became
**unstoppable and invisible**. It is the same assumption — "there is only ever one navig,
at ~/.navig" — that let ``navig gateway start`` force-kill an unrelated brain (PR #173).

The default install is unaffected either way (``config_dir()`` *is* ``~/.navig``), which is
exactly why these survive: they look correct on the only machine anyone tests on.

This test pins the remaining sites. Adding a new one fails; fixing one and leaving a stale
entry also fails, so the list cannot rot into a rubber stamp.
"""

from __future__ import annotations

import ast
from pathlib import Path

import navig

PKG = Path(navig.__file__).resolve().parent


def _is_home_navig(node: ast.AST) -> bool:
    """`Path.home() / ".navig"` — matched on the AST, not the text.

    A regex over source also matches the pattern inside comments and docstrings (it flagged
    the very comment explaining the fix), so it would force authors to avoid *writing about*
    the bug. The AST only sees code.
    """
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
        return False
    right = node.right
    if not (isinstance(right, ast.Constant) and right.value == ".navig"):
        return False
    # Any `<something>.home() / ".navig"`. Deliberately NOT pinned to the name `Path`:
    # main.py imports it as `_Path`, and pinning the receiver made the detector miss it —
    # caught only because the stale-allowlist check flagged the entry as unmatched.
    left = node.left
    return (
        isinstance(left, ast.Call)
        and isinstance(left.func, ast.Attribute)
        and left.func.attr == "home"
        and not left.args
    )


def _is_home_navig_literal(node: ast.AST) -> bool:
    """A ``~/.navig`` path built from a STRING LITERAL — the spelling ``_is_home_navig`` misses.

    Catches the two direct constructions:
      * ``os.path.expanduser("~/.navig/…")`` / ``expanduser("~/.navig/…")``
      * ``Path("~/.navig/…")`` / ``pathlib.Path("~/.navig/…")``

    #192 fixed eight ``debug.log`` readers but LEFT ``os.path.expanduser("~/.navig/debug.log")``
    in ``telegram_commands.py`` — invisible to both that sweep's grep and this guard's original
    ``Path.home()`` matcher. Only matches a literal that STARTS with ``~/.navig`` (a hardcoded
    navig path), never a variable (``Path(user_cfg).expanduser()`` is a user path, left alone),
    never text in a docstring/comment (the AST sees calls, not prose).
    """
    if not isinstance(node, ast.Call) or not node.args:
        return False
    func = node.func
    # `os.path.expanduser(...)` (Attribute) or a bare `expanduser(...)` imported via
    # `from os.path import expanduser` (Name) — both spellings.
    is_expanduser = (isinstance(func, ast.Attribute) and func.attr == "expanduser") or (
        isinstance(func, ast.Name) and func.id == "expanduser"
    )
    is_path_ctor = (isinstance(func, ast.Name) and func.id == "Path") or (
        isinstance(func, ast.Attribute) and func.attr == "Path"
    )
    if not (is_expanduser or is_path_ctor):
        return False
    arg = node.args[0]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return False
    return arg.value.startswith("~/.navig") or arg.value.startswith("~\\.navig")


# Sites that may legitimately reach for the real home, and why. Everything else must use
# navig.platform.paths (config_dir / store_dir / log_dir / …).
ALLOWED: dict[str, str] = {
    # The uv-managed runtime is installed to ~/.navig/runtime by install.ps1 / install.sh,
    # BEFORE any config exists and regardless of NAVIG_CONFIG_DIR. It is an installer
    # contract, not config-scoped state.
    "cli/providers.py": "~/.navig/runtime — the bundled uv runtime (installer contract)",
    "commands/doctor.py": "~/.navig/runtime — the bundled uv runtime (installer contract)",
    "commands/quickstart.py": "~/.navig/runtime/venv — the bundled uv runtime",
    "commands/update.py": "~/.navig/runtime — locating the bundled uv for self-update",
    # Defensive last resort, only after paths.* and the config manager have both failed —
    # the logger must never be the thing that raises.
    "debug_logger.py": "last-resort fallback after paths.debug_log_path() and the config manager both fail",
    # Already env-aware: reads NAVIG_CONFIG_DIR and only falls back to the home default.
    "agent/proactive/eve_log.py": "reads NAVIG_CONFIG_DIR first; home is the documented default",
    # Migrating the pre-spaces layout, which by definition lived in the real home.
    "main.py": "one-time migration of the legacy ~/.navig workspace layout",
    # The TODO backlog is CLOSED — every remaining entry below is deliberate, and each one
    # was resolved by the same method: find the WRITER, make the reader agree. Fixed and
    # removed from this list: commands/service.py · commands/wire.py ·
    # gateway/deck/routes/{apps,catalog,remote}.py · scheduler/habit_store.py's gateway.json
    # read. (A stale entry fails this suite, so none of them can be quietly left behind.)
    #
    # `legacy_store_path()` must point at the DEAD pre-fix location by definition — that is
    # the whole job of the function: recognise the store the old habit surfaces wrote to and
    # never executed. Resolving it through config_dir() would make it stop finding the very
    # thing it exists to find. The LIVE path next to it (live_store_path) is config-scoped.
    "scheduler/habit_store.py": (
        "legacy_store_path() — the dead pre-fix cron store, which by definition lived at "
        "~/.navig/daemon/cron_jobs.json; the live store is config-scoped"
    ),
    # A read-only probe of a file this codebase never writes. The producer is an external
    # bridge process, so ~/.navig is ITS contract, not ours to relocate unilaterally.
    "gateway/channels/telegram_commands.py": (
        "bridge-registry.json — read-only probe of a file with NO writer in core; an external "
        "bridge produces it, so the ~/.navig location is that bridge's contract"
    ),
}


def _offenders() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for py in sorted(PKG.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):  # pragma: no cover
            continue
        hits = sorted({
            getattr(n, "lineno", 0)
            for n in ast.walk(tree)
            if _is_home_navig(n) or _is_home_navig_literal(n)
        })
        if hits:
            found[py.relative_to(PKG).as_posix()] = hits
    return found


def test_no_new_hardcoded_home_path() -> None:
    found = _offenders()
    unexpected = {m: h for m, h in found.items() if m not in ALLOWED}
    assert not unexpected, (
        "These modules hardcode `Path.home() / \".navig\"`, bypassing NAVIG_CONFIG_DIR.\n"
        "Use navig.platform.paths (config_dir / store_dir / log_dir / …) — it resolves to\n"
        "~/.navig by default, so the default install is unchanged, but it follows a custom\n"
        "config dir instead of splitting the feature across two homes.\n"
        "If the real home is genuinely correct (an installer contract), add the module to\n"
        "ALLOWED with a reason.\n\n"
        + "\n".join(f"  {m}: line(s) {h}" for m, h in sorted(unexpected.items()))
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Fixed one? Remove its entry, so the list can't become a rubber stamp."""
    stale = sorted(set(ALLOWED) - set(_offenders()))
    assert not stale, (
        "These modules no longer hardcode the home path — delete their ALLOWED entries:\n"
        + "\n".join(f"  {m}" for m in stale)
    )


# ─────────────────────────────────────────────────────────────────────────────
# The SAME hazard, in the test tree. The `_offenders()` scan above covers only the
# shipped package; a TEST that reaches for `Path.home() / ".navig"` writes to the
# operator's REAL config dir, because `Path.home()` ignores NAVIG_CONFIG_DIR just as
# thoroughly in a test as in production. That is not theoretical either — it went
# undetected for weeks and passed green the whole time:
#   • test_hierarchical_config wrote a host into ~/.navig/hosts and DELETED it (#204),
#   • the debug-cmd tests ran `debug clear` against the real path, TRUNCATING the
#     operator's actual debug.log on every run (#199).
# Both looked correct on the only machine anyone runs the suite on — the default
# install, where config_dir() *is* ~/.navig — which is exactly why they survived.
#
# The correct pattern for a test that needs the home path is to be HERMETIC: patch
# `pathlib.Path.home` so it resolves inside the tmp sandbox (see
# test_autonomous_agent_hermetic). Such files are auto-exempt below. A test that only
# READS the real path (an assertion or a skip-guard, never a write) is benign and is
# pinned in ALLOWED_TESTS with its reason.

TESTS_ROOT = Path(__file__).resolve().parents[1]  # core/tests

# The string a hermetic test patches to sandbox Path.home for its whole run. Its mere
# presence in a file means every `Path.home()` there resolves into the tmp dir.
_HOME_PATCH_TARGET = "pathlib.Path.home"

# Test files that reference `Path.home() / ".navig"` but only READ it (== assertions,
# is_dir()/exists() skip-guards) — they never write or delete, so they cannot damage the
# operator's real config. A test that WRITES must be hermetic (patch pathlib.Path.home)
# instead of being added here.
ALLOWED_TESTS: dict[str, str] = {
    "settings/test_settings_resolver_paths.py":
        "asserts the resolver does NOT return ~/.navig — read-only",
    "wave/test_wave10_paths.py":
        "asserts the default global_config_dir equals ~/.navig — read-only",
    "workspace/test_workspace.py":
        "path-classification assertions (is_project_workspace_path) — never writes",
    "core/test_tmp_dir_repo_boundary.py":
        "is_dir() skip-guard: skips when the machine has no ~/.navig — never writes",
    "agent/test_autonomous_agent.py":
        "exists()/stat() E2E smoke over the real workspace — never writes",
    "commands/test_commands_paths_cmd.py":
        "asserts the logs row is NOT ~/.navig/logs (i.e. uses log_dir()) — read-only",
}


def _patches_home(tree: ast.AST) -> bool:
    """True if the file patches ``pathlib.Path.home`` — i.e. Path.home() is sandboxed for
    the whole run, so any ``Path.home()/".navig"`` in it lands in the tmp dir, not the
    operator's real home. Detected on the AST (a Constant string), so a comment or
    docstring mentioning the target does not count."""
    return any(
        isinstance(n, ast.Constant) and n.value == _HOME_PATCH_TARGET
        for n in ast.walk(tree)
    )


def _test_offenders() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for py in sorted(TESTS_ROOT.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):  # pragma: no cover
            continue
        if _patches_home(tree):
            continue  # hermetic — Path.home() is sandboxed for this file
        hits = sorted({
            getattr(n, "lineno", 0) for n in ast.walk(tree) if _is_home_navig(n)
        })
        if hits:
            found[py.relative_to(TESTS_ROOT).as_posix()] = hits
    return found


def test_no_new_hardcoded_home_in_tests() -> None:
    """A test must not write to the operator's REAL ~/.navig.

    Two tests that did — deleting host configs (#204) and truncating debug.log (#199) —
    passed green for weeks. A new test that reaches for ``Path.home()/".navig"`` must
    either be HERMETIC (patch ``pathlib.Path.home`` so it lands in the tmp sandbox) or, if
    it only READS the real path, be added to ALLOWED_TESTS with a reason.
    """
    unexpected = {m: h for m, h in _test_offenders().items() if m not in ALLOWED_TESTS}
    assert not unexpected, (
        "These TESTS hardcode `Path.home() / \".navig\"`, so they operate on the\n"
        "operator's REAL config dir instead of the isolated NAVIG_CONFIG_DIR sandbox.\n"
        "A test that WRITES there can delete real host configs or truncate real logs.\n"
        "Fix: patch `pathlib.Path.home` to return tmp_path (be hermetic). If it only\n"
        "READS the real path, add it to ALLOWED_TESTS with a reason.\n\n"
        + "\n".join(f"  {m}: line(s) {h}" for m, h in sorted(unexpected.items()))
    )


def test_allowed_tests_has_no_stale_entries() -> None:
    """Made one hermetic (or removed the reference)? Delete its ALLOWED_TESTS entry, so
    the list cannot rot into a rubber stamp."""
    stale = sorted(set(ALLOWED_TESTS) - set(_test_offenders()))
    assert not stale, (
        "These tests no longer reference the raw home path — delete their ALLOWED_TESTS "
        "entries:\n" + "\n".join(f"  {m}" for m in stale)
    )


def test_the_agent_pid_is_written_where_it_is_read() -> None:
    """THE REGRESSION: the writer used Path.home(), all three readers use config_dir().

    Under a custom NAVIG_CONFIG_DIR the agent became unstoppable — `navig agent stop` and
    `navig agent status` looked in a directory the running agent never wrote to.
    """
    from navig.commands.agent import _get_agent_config_dir
    from navig.platform.paths import config_dir

    assert "agent/runner.py" not in _offenders(), (
        "the agent pid path hardcodes the real home again — `navig agent stop` would go blind"
    )
    # writer target == reader target
    assert _get_agent_config_dir() == config_dir() / "agent"


def test_gateway_json_reader_agrees_with_the_writer(tmp_path, monkeypatch) -> None:
    """REGRESSION: the gateway WRITES gateway.json to config_dir() (gateway/server.py) and
    gateway_client.py reads it from there — but scheduler/habit_store.py read it from
    `Path.home()/".navig"`. Under a custom NAVIG_CONFIG_DIR the scheduler could never find
    the live gateway, so every habit/cron surface silently lost its HTTP path to it.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    import json

    from navig.platform.paths import config_dir
    from navig.scheduler import habit_store

    (config_dir() / "gateway.json").write_text(
        json.dumps({"url": "http://127.0.0.1:55555"}), encoding="utf-8")
    assert habit_store._gateway_url() == "http://127.0.0.1:55555", (
        "habit_store reads gateway.json from somewhere the gateway does not write it"
    )


def test_deck_navig_dir_follows_the_configured_dir(tmp_path, monkeypatch) -> None:
    """REGRESSION: the deck's `_navig_dir()` feeds tasks.json AND the spaces list, but spaces
    are written to config_dir()/spaces (commands/install.py). Hardcoding the home meant the
    deck listed a different set of spaces than the CLI had installed.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    from navig.gateway.deck.routes.apps import _navig_dir
    from navig.platform.paths import config_dir

    assert _navig_dir() == config_dir() == tmp_path


def test_spaces_root_is_the_same_dir_everywhere(tmp_path, monkeypatch) -> None:
    """`navig install` writes spaces to config_dir()/spaces; `navig wire` classified a space
    as "root" vs "external" by comparing against ~/.navig/spaces. Under a custom config dir a
    space the CLI had just installed was mis-registered as external.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    from navig.commands.install import _dest_for
    from navig.platform.paths import config_dir

    assert _dest_for("space", "demo") == config_dir() / "spaces" / "demo"


def test_literal_detector_catches_the_expanduser_and_path_spellings() -> None:
    """The #192 leftover was `os.path.expanduser("~/.navig/debug.log")` — invisible to the
    original `Path.home()` matcher. Prove the literal detector sees both direct spellings and
    ignores user paths + prose."""
    import ast as _ast

    def _hits(src: str) -> bool:
        return any(_is_home_navig_literal(n) for n in _ast.walk(_ast.parse(src)))

    # caught — hardcoded ~/.navig literals
    assert _hits('import os\np = os.path.expanduser("~/.navig/debug.log")')
    assert _hits('from pathlib import Path\np = Path("~/.navig/flows/x.jsonl")')
    assert _hits('import pathlib\np = pathlib.Path("~/.navig")')
    assert _hits('p = expanduser("~/.navig/scripts")')

    # NOT caught — a user-supplied path (variable), never a hardcoded navig literal
    assert not _hits('from pathlib import Path\np = Path(user_cfg).expanduser()')
    assert not _hits('import os\np = os.path.expanduser(some_path)')
    # NOT caught — a different ~/ path
    assert not _hits('from pathlib import Path\np = Path("~/incident.navbox")')
    # NOT caught — the literal only appears in a docstring / comment (AST sees calls, not prose)
    assert not _hits('"""Config lives in ~/.navig/config.yaml."""\nx = 1')
    assert not _hits('x = 1  # reads ~/.navig/debug.log')


def test_the_192_debug_log_leftover_is_gone() -> None:
    """REGRESSION: telegram_commands.py had two `os.path.expanduser("~/.navig/debug.log")`
    reads (the #192 decoy) that the earlier sweep and the pre-hardening guard both missed.
    They must resolve through debug_log_path()/log_dir() now, not a home literal."""
    import ast as _ast

    src = (PKG / "gateway" / "channels" / "telegram_commands.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    bad = [
        n for n in _ast.walk(tree)
        if _is_home_navig_literal(n)
        and isinstance(n.args[0], _ast.Constant)
        and "debug.log" in str(n.args[0].value)
    ]
    assert not bad, "a ~/.navig/debug.log literal is back in telegram_commands.py"
