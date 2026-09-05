"""`POST /api/deck/llm-modes` must say when a mode was applied but not SAVED.

`LLMModeRouter.update_mode` is documented as updating "a mode's configuration **in
memory**", so the `try/except` that writes `config.yaml` is the whole of the
persistence. Its failure was a `logger.warning` under an unconditional
`{"ok": True}` — so the caller was told the mode was updated, saw the new config
echoed back, and lost it at the next daemon restart with nothing to explain why.

`ok` deliberately stays True: the mode really IS active in the running process, and
reporting failure would be its own lie. `persisted` carries the part that decides
whether it survives — the same split as `ok`/`complete` on the admin settings PATCH
and `navig backup export`.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from navig.gateway.deck.routes import llm_modes as lm


class _Req:
    def __init__(self, body: Any) -> None:
        self._body = body

    async def json(self) -> Any:
        return self._body


def _router() -> MagicMock:
    router = MagicMock()
    router.update_mode.return_value = True
    router.get_all_modes.return_value = {"coding": {"provider": "openai"}}
    return router


async def _update(*, persist_raises: Exception | None, body: dict | None = None) -> dict:
    import navig.config as config_mod

    cm = MagicMock()
    cm.global_config = {}
    if persist_raises is not None:
        cm.update_global_config.side_effect = persist_raises

    with patch.object(lm, "_get_llm_router", return_value=_router()), patch.object(
        config_mod, "get_config_manager", return_value=cm
    ):
        resp = await lm.handle_deck_llm_modes_update(_Req(body or {"mode": "coding"}))
    return json.loads(resp.body.decode())


async def test_a_failed_persist_is_reported() -> None:
    """The defect: this used to be a log line under `{"ok": true}`."""
    payload = await _update(persist_raises=OSError("read-only file system"))

    assert payload["persisted"] is False
    assert "warning" in payload


async def test_the_warning_says_the_setting_will_revert() -> None:
    """"Could not persist" alone does not tell an operator what they will observe —
    that the mode works now and stops working after a restart."""
    payload = await _update(persist_raises=OSError("read-only file system"))

    assert "restart" in payload["warning"].lower()
    assert "read-only file system" in payload["warning"], (
        "the underlying cause must survive into the response"
    )


async def test_ok_stays_true_because_the_mode_really_is_active() -> None:
    """`update_mode` succeeded in memory. Flipping `ok` to false would be a second
    lie in the other direction — the caller's next request WOULD use the new mode."""
    payload = await _update(persist_raises=OSError("nope"))

    assert payload["ok"] is True
    assert payload["config"] == {"provider": "openai"}


async def test_a_successful_save_reports_persisted_and_no_warning() -> None:
    """The partner. A change that always reported `persisted: false` would satisfy
    the tests above while telling every healthy install its settings are volatile."""
    payload = await _update(persist_raises=None)

    assert payload["ok"] is True
    assert payload["persisted"] is True
    assert "warning" not in payload


async def test_update_mode_is_still_in_memory_only() -> None:
    """Anti-vacuity. Every assertion here rests on the router NOT persisting by
    itself — if that ever changes, the handler's config write becomes a second store
    and this whole shape needs rethinking rather than silently passing."""
    import inspect

    from navig.llm.router import LLMModeRouter

    doc = inspect.getdoc(LLMModeRouter.update_mode) or ""
    assert "in memory" in doc.lower(), (
        "update_mode no longer documents itself as in-memory only — re-check whether "
        "the handler's config write is still the only persistence"
    )


@pytest.mark.parametrize(
    "body,expected",
    [({"mode": ""}, "mode is required"), ({"mode": "not_a_mode"}, "invalid mode")],
)
async def test_input_validation_is_unchanged(body: dict, expected: str) -> None:
    """The persistence reporting is additive; the pre-existing 400s must still fire."""
    with patch.object(lm, "_get_llm_router", return_value=_router()):
        resp = await lm.handle_deck_llm_modes_update(_Req(body))

    assert resp.status == 400
    assert expected in json.loads(resp.body.decode())["error"]


async def test_an_unknown_mode_does_not_reconfigure_a_DIFFERENT_mode() -> None:
    """`resolve_mode` falls back to "big_tasks" for any unrecognised hint — right for
    detecting a mode from prose, wrong for an identifier the caller typed. That made
    the handler's own `invalid mode` guard DEAD CODE, so `{"mode": "codng"}` silently
    reconfigured big_tasks and answered `{"ok": true, "mode": "big_tasks"}`."""
    router = _router()
    with patch.object(lm, "_get_llm_router", return_value=router):
        resp = await lm.handle_deck_llm_modes_update(_Req({"mode": "codng"}))

    assert resp.status == 400, "a typo'd mode was accepted"
    router.update_mode.assert_not_called()
    payload = json.loads(resp.body.decode())
    assert "coding" in payload["valid_modes"], "the 400 should list what IS valid"


async def test_a_real_alias_still_resolves() -> None:
    """The partner. Aliases are a supported spelling — rejecting them would trade the
    silent-wrong-mode bug for a broken API."""
    from navig.llm.router import MODE_ALIASES

    if not MODE_ALIASES:
        pytest.skip("no aliases defined")
    alias = next(iter(MODE_ALIASES))

    router = _router()
    import navig.config as config_mod

    cm = MagicMock()
    cm.global_config = {}
    with patch.object(lm, "_get_llm_router", return_value=router), patch.object(
        config_mod, "get_config_manager", return_value=cm
    ):
        resp = await lm.handle_deck_llm_modes_update(_Req({"mode": alias}))

    assert resp.status == 200, f"alias {alias!r} was rejected"
    assert json.loads(resp.body.decode())["mode"] == MODE_ALIASES[alias]
