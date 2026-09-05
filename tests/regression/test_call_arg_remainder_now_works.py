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


def test_the_app_recovery_path_uses_the_host_free_lister():
    """`list_apps` needs a host; this path has none and wants 'are there any apps?'."""
    from navig.config import ConfigManager

    with pytest.raises(TypeError):
        _bind(ConfigManager.list_apps, object())  # no host_name

    _bind(ConfigManager.list_apps_from_files, object())  # navig_dir is optional

    # On the AST, not the text: the fix carries a comment that NAMES the old call, so a
    # substring scan passes even with the code reverted. (Verified — the first version of
    # this test did exactly that and survived its own teeth check.)
    called = _method_calls_in("navig/cli/recovery.py", "require_active_app")
    assert "list_apps_from_files" in called, (
        f"require_active_app calls {sorted(called)} — it must use list_apps_from_files()"
    )
    assert "list_apps" not in called, (
        "the recovery path calls list_apps() again — it needs a host_name and raises "
        "TypeError past its hasattr guard, so 'no apps configured' never renders"
    )


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
