"""The operator's `tools.blocked_tools` must reach the router the live path uses.

#813 fixed the *matching* inside `ToolRouter`: a policy of `blocked_tools: ["web-fetch"]`
canonicalises to `web_fetch` on both sides, so a gate and its setter cannot disagree. Every
test written for it hands the router its policy by hand::

    router = ToolRouter(safety_policy={"blocked_tools": [spelling]})

which proves the matching works and can never see the question one level up: **does anything
in production supply that argument?**

It did not. `get_tool_router()` is a first-caller-wins singleton, and the two live callers are

  * `agent/conv/executor.py`  — `get_tool_router()`, no policy at all, and
  * `llm/generate.py`         — the only caller that built one, on a path that is dormant.

So the singleton was constructed with `_policy = {}` and `_blocked` empty. An operator who set
`blocked_tools: ["bash_exec"]` — a tool whose own description is "Execute a shell command" —
got a router that had never heard of it.

Two further layers, each of which would have been enough on its own:

* `get_tool_router(safety_policy=…)` **silently ignored** its argument whenever the singleton
  already existed, so even the caller that supplied a policy only mattered if it happened to
  run first. A parameter that quietly does nothing is the same class of lie as a green ✓ over
  a check that never ran.
* The reader itself could never have worked. It called
  `navig.core.config_loader.load_config()` with no arguments — but that function loads *a
  file* and takes a required `path`, so every call raised `TypeError` straight into a bare
  `except` that returned `{}`. There was no working reader of this policy anywhere in the
  tree.

These tests exercise the *live* entry point — `get_tool_router()`, no arguments — against a
real `config.yaml`, rather than constructing the object the way only tests do.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest


@pytest.fixture(autouse=True)
def _fresh_router():
    """Each test gets a router singleton it built itself, and leaves none behind."""
    from navig.tools.router import reset_globals

    reset_globals()
    yield
    reset_globals()


def _operator_config(monkeypatch, tmp_path, body: str):
    """Point the policy reader at a REAL `config.yaml` holding *body*.

    Deliberately a real `ConfigManager` over a real file rather than a stub returning a
    canned object. A stub is what hid the underlying bug: the previous reader called
    `config_loader.load_config()` with no arguments, which raises `TypeError` because that
    function loads *a file* and needs a `path`. A fake accepting `*args, **kwargs` answers
    happily, so tests around it pass while the real call can only ever return `{}`. **The
    fake must not be more permissive than the function it stands in for.**

    ⚠ `ConfigManager(config_dir=…)` is the wrong lever here: it sets `base_dir`, while
    `cm.get()` reads the *global* config out of `global_config_dir`, which comes from
    `NAVIG_CONFIG_DIR`. Passing `config_dir` alone yields a manager that reads a different
    file than the one the test wrote, and every assertion fails against an empty policy for
    a reason that has nothing to do with the code under test.
    """
    import navig.config as config_mod

    (tmp_path / "config.yaml").write_text(body, encoding="utf-8")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    cm = config_mod.ConfigManager()
    monkeypatch.setattr(config_mod, "get_config_manager", lambda *a, **k: cm)
    return cm


BLOCK_BASH = "tools:\n  blocked_tools:\n    - bash_exec\n"


def test_the_live_caller_gets_the_operators_blocked_tools(monkeypatch, tmp_path):
    """`get_tool_router()` — how conv/executor.py calls it — must honour config."""
    _operator_config(monkeypatch, tmp_path, BLOCK_BASH)

    from navig.tools.router import get_tool_router

    router = get_tool_router()

    assert "bash_exec" in router._blocked, (
        "get_tool_router() built a router with no safety policy, so the operator's "
        "tools.blocked_tools was not applied on the path conv/executor.py actually uses."
    )


def test_every_documented_spelling_survives_the_trip_from_config(monkeypatch, tmp_path):
    """#813's canonicalisation must still apply when the policy comes from config.

    Fixing the wiring by handing the router a raw config dict would re-open #813 from the
    other side, so the hyphenated spelling is re-asserted at the *live* entry point.
    """
    _operator_config(monkeypatch, tmp_path, "tools:\n  blocked_tools:\n    - web-fetch\n")

    from navig.tools.router import get_tool_router

    assert "web_fetch" in get_tool_router()._blocked


def test_require_confirmation_also_reaches_the_live_router(monkeypatch, tmp_path):
    """The sibling control had the identical gap — it is fed by the same `_policy` dict."""
    _operator_config(
        monkeypatch, tmp_path, "tools:\n  require_confirmation:\n    - web_fetch\n"
    )

    from navig.tools.router import get_tool_router

    assert "web_fetch" in get_tool_router()._require_confirmation


def test_a_blocked_tool_is_actually_denied_end_to_end(monkeypatch, tmp_path):
    """Not just present in a set — the call must come back DENIED."""
    _operator_config(monkeypatch, tmp_path, "tools:\n  blocked_tools:\n    - web_fetch\n")

    from navig.tools.router import ToolResultStatus, get_tool_router
    from navig.tools.schemas import ToolCallAction

    result = get_tool_router().execute(ToolCallAction(tool="web_fetch", parameters={}))

    assert result.status is ToolResultStatus.DENIED, (
        f"a blocked tool returned {result.status} — the operator's policy did not stop it"
    )


def test_the_policy_reader_calls_config_the_way_config_accepts(monkeypatch, tmp_path):
    """The loader must be callable as written — the defect that hid all the others.

    `load_safety_policy` swallows exceptions by design (a config read must not break
    dispatch), which means a wrong call signature inside it is invisible: it simply returns
    `{}` forever. So assert a populated policy comes back from a real config file, and that
    the reader is not reaching for the file-loading `load_config(path)` again.
    """
    _operator_config(monkeypatch, tmp_path, BLOCK_BASH)

    from navig.tools.router import load_safety_policy

    policy = load_safety_policy()

    assert policy.get("blocked_tools") == ["bash_exec"], (
        "load_safety_policy() returned an empty/incorrect policy from a real config.yaml — "
        "its exception handler is converting a broken call into a silent no-policy."
    )

    # On the AST, not the text: this function's docstring *names* the broken reader in
    # order to warn about it, and a substring scan cannot tell prose from an import.
    tree = ast.parse(textwrap.dedent(inspect.getsource(load_safety_policy)))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("config_loader" in m for m in imported), (
        f"load_safety_policy imports {imported} — config_loader.load_config requires a "
        "`path` argument and raises TypeError when called bare, which its own exception "
        "handler then hides as an empty policy. That was the original bug."
    )


def test_an_unreadable_config_does_not_take_the_router_down(monkeypatch):
    """A policy that cannot be loaded must degrade to no-policy, never raise.

    The router sits on the dispatch path; a config read that throws must not turn every
    tool call into a crash.
    """
    import navig.config as config_mod

    def _boom(*a, **k):
        raise OSError("config is being rewritten")

    monkeypatch.setattr(config_mod, "get_config_manager", _boom)

    from navig.tools.router import get_tool_router

    assert get_tool_router()._blocked == set()


def test_an_explicit_policy_that_arrives_too_late_is_not_silent(monkeypatch, tmp_path):
    """Passing a policy to an already-built singleton must say so rather than vanish.

    This is the shape that made the bug survivable: `llm/generate.py` passed a real policy
    and, whenever it did not construct the singleton, that policy was discarded without a
    word. The caller had every reason to believe its policy applied.
    """
    _operator_config(monkeypatch, tmp_path, BLOCK_BASH)

    from navig.tools import router as router_mod

    router_mod.get_tool_router()  # first caller builds it

    warnings: list[str] = []
    monkeypatch.setattr(
        router_mod.logger,
        "warning",
        lambda msg, *a, **k: warnings.append(str(msg) % a if a else str(msg)),
    )

    router_mod.get_tool_router(safety_policy={"blocked_tools": ["web_fetch"]})

    assert warnings, (
        "a safety policy handed to an existing router singleton was discarded silently"
    )


def test_a_caller_supplied_policy_still_wins_when_it_builds_the_router(monkeypatch, tmp_path):
    """Loading from config must not break the explicit-override contract."""
    _operator_config(monkeypatch, tmp_path, BLOCK_BASH)

    from navig.tools.router import get_tool_router

    router = get_tool_router(safety_policy={"blocked_tools": ["web_fetch"]})

    assert "web_fetch" in router._blocked
    assert "bash_exec" not in router._blocked
