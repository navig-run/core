"""The last six `call-arg` findings that were genuine defects.

After #1032 (gate + 12 fixed) and #1049 (8 more), the baseline held 15. Seven were the
`load_pem_private_key` union artifact; the rest were listed as "needs local context". The
context was in each file:

* `cli/recovery.py`      `ConfigManager.list_apps(host_name)` called with no host — past a
                         `hasattr` guard that only proved the *name* existed.
* `commands/agent.py`    `agent_config_cmd(key=…)`; the parameter is `set_key`. The
                         interactive menu's "config" entry was dead.
* `agent/ai_client.py`   `AirLLMClient(airllm_config=…)` omitting the required `config`.
* `tools/hook_bridge.py` `ExecutionEvent.after(…)` omitting keyword-only `error`.
* `gateway/channels/telegram.py`  a Session object passed where `chat_id` goes, `user_id`
                         missing — so per-tier routing overrides never reached the router.
* `media/generation_service.py`   an inference artifact, not a runtime bug: three modality
                         branches reused one `gen` name, so mypy kept the first binding
                         (`ImageGenerator`) and called the audio branch's real kwargs
                         unexpected. Split into per-branch names.

Every one sat behind a `try`/`except` or a `hasattr` that turned a guaranteed `TypeError`
into silence, which is why none of them ever surfaced as a bug report.
"""

from __future__ import annotations

import inspect

import pytest

CORE = __import__("pathlib").Path(__file__).resolve().parents[2]


def _ast_of(rel: str):
    import ast

    return ast.parse((CORE / rel).read_text(encoding="utf-8"))


def _method_calls_in(rel: str, fn_name: str) -> set[str]:
    """Attribute names called (`x.foo()`) inside the named function — AST, not text."""
    import ast

    for node in ast.walk(_ast_of(rel)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == fn_name:
            return {
                c.func.attr
                for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            }
    raise AssertionError(f"{fn_name} not found in {rel} — did it move?")


def _plain_calls_in(rel: str) -> set[str]:
    """Bare-name calls (`Foo(...)`) anywhere in the module."""
    import ast

    return {
        c.func.id
        for c in ast.walk(_ast_of(rel))
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }


def _call_keywords(rel: str, attr: str) -> list[set[str]]:
    """Keyword names passed to every `*.<attr>(...)` call in the module."""
    import ast

    return [
        {k.arg for k in c.keywords if k.arg}
        for c in ast.walk(_ast_of(rel))
        if isinstance(c, ast.Call) and getattr(c.func, "attr", None) == attr
    ]


def _bind(fn, /, *args, **kwargs):
    """Bind against the REAL signature — the check that fails for the original reasons."""
    return inspect.signature(fn).bind(*args, **kwargs)


def test_the_app_recovery_path_passes_a_host_to_list_apps():
    """`list_apps` needs a host; the recovery path must pass the one in scope."""
    import ast

    from navig.config import ConfigManager

    with pytest.raises(TypeError):
        _bind(ConfigManager.list_apps, object())  # no host_name
    _bind(ConfigManager.list_apps_from_files, object())  # navig_dir is optional

    called = _method_calls_in("navig/cli/recovery.py", "require_active_app")
    assert "list_apps_from_files" in called, "the no-host fallback is gone"
    for node in ast.walk(_ast_of("navig/cli/recovery.py")):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "list_apps":
            assert node.args or node.keywords, (
                "require_active_app calls list_apps() with no host_name — it raises "
                "TypeError past its hasattr guard, so 'no apps configured' never renders"
            )


class _RecoveryCfg:
    """Two hosts: `web` has a legacy embedded app, `db` owns an individual app file."""

    def __init__(self, active_host):
        self.active_host = active_host

    def get_active_app(self):
        return None

    def get_active_host(self):
        return (self.active_host, "global")

    def list_apps(self, host_name):
        return {"web": ["legacy-site"], "db": ["pg-admin"]}.get(host_name, [])

    def list_apps_from_files(self):
        return ["pg-admin"]  # every individual file, whatever its host

    def set_active_app(self, name):
        self.chosen = name


def test_app_recovery_offers_the_selected_hosts_apps(monkeypatch):
    """Review: https://github.com/navig-run/labs/pull/1061#discussion_r3792532817"""
    import types

    from navig.cli import recovery

    offered: list[list[str]] = []

    def fake_fzf(entries, prompt=""):
        offered.append([e.name for e in entries])
        return entries[0]

    monkeypatch.setattr(recovery, "_is_tty", lambda: True)
    monkeypatch.setattr(recovery, "ch", types.SimpleNamespace(
        info=lambda *a, **k: None, success=lambda *a, **k: None,
        warning=lambda *a, **k: None,
    ), raising=False)
    import navig.cli.selector as selector

    monkeypatch.setattr(selector, "fzf_or_fallback", fake_fzf)

    cfg = _RecoveryCfg("web")
    assert recovery.require_active_app({}, cfg) == "legacy-site"
    assert offered[-1] == ["legacy-site"]  # legacy app offered, the other host's file not

    cfg = _RecoveryCfg(None)
    assert recovery.require_active_app({"host": "db"}, cfg) == "pg-admin"
    assert offered[-1] == ["pg-admin"]


def test_the_menu_wrapper_can_call_agent_config():
    """The parameter is `set_key`. The wrapper must also pass values explicitly.

    A bare `agent_config_cmd()` would hand `typer.Option(...)` objects through as if they
    were values, so the explicit arguments are load-bearing, not decoration.
    """
    from navig.commands.agent import agent_config_cmd

    _bind(agent_config_cmd, edit=False, show=True, set_key=None, value=None)

    with pytest.raises(TypeError):
        _bind(agent_config_cmd, key=None, value=None, edit=False)


def test_the_airllm_client_is_built_through_its_factory():
    """`AirLLMClient` inherits a required `config: ProviderConfig`."""
    from navig.providers.airllm import create_airllm_client

    _bind(create_airllm_client, airllm_config=None)

    called = _plain_calls_in("navig/agent/ai_client.py")
    assert "AirLLMClient" not in called, (
        "ai_client constructs AirLLMClient directly again, omitting the required `config`"
    )
    assert "create_airllm_client" in called


def test_the_after_hook_supplies_every_required_field():
    from navig.engine.hooks import ExecutionEvent

    with pytest.raises(TypeError):
        _bind(
            ExecutionEvent.after, tool_name="t", args={},
            success=True, output=None, elapsed_ms=1.0,
        )  # `error` is keyword-only with no default

    _bind(
        ExecutionEvent.after, tool_name="t", args={},
        success=True, output=None, error=None, elapsed_ms=1.0,
    )

    # And pin the CALL SITE — the binding above proves the signature, not that
    # hook_bridge satisfies it. Reverting the fix must fail this.
    kwargs = _call_keywords("navig/tools/hook_bridge.py", "after")
    assert kwargs and "error" in kwargs[0], (
        f"ExecutionEvent.after(...) in hook_bridge is missing `error`: {kwargs}"
    )


def test_session_overrides_are_fetched_by_ids_not_by_a_session_object():
    from navig.gateway.channels.telegram_sessions import SessionManager

    with pytest.raises(TypeError):
        _bind(SessionManager.get_all_session_overrides, object(), object())  # no user_id

    _bind(SessionManager.get_all_session_overrides, object(), 1, 2, is_group=False)


def test_each_generation_branch_uses_its_own_generator_name():
    """Reusing one name across the three modality branches is what mypy tripped on.

    Not a runtime bug — but the branches are independent, and sharing a name makes the
    audio branch's perfectly valid `kind=`/`duration_s=` look like an error forever.
    """
    from navig.media import generation_service

    src = inspect.getsource(generation_service)
    assert "vgen = VideoGenerator(" in src and "agen = AudioGenerator(" in src, (
        "the modality branches share a generator variable again"
    )
    assert "aud = await agen.generate(" in src


def test_audio_generate_really_accepts_those_kwargs():
    """Proves the finding was an artifact rather than a defect."""
    from navig.tools.audio_generation import AudioGenerator

    _bind(AudioGenerator.generate, object(), "prompt", kind="music", duration_s=3.0)
