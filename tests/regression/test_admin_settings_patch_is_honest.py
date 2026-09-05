"""`POST /api/deck/admin/settings` must not report a save it did not perform.

The validator ended in `# else: silently skip unknown keys`, so a patch naming only
keys the server does not know fell through every branch and returned:

    {"ok": true, "updated": [], "errors": []}      HTTP 200

— indistinguishable from a successful save. One typo (`hybrid_serch`) or one key
renamed in a later version, and the caller is told the setting was stored. The deck
ships only a GET client for this endpoint, so the realistic caller is a script or an
agent — the consumer least able to notice that `updated` came back empty.

Counting an unknown key as an error is the whole fix: the pre-existing
`if errors and not updated` check then turns "nothing landed" into a 400.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from navig.gateway.deck.routes import admin


class _Req:
    def __init__(self, body: Any) -> None:
        self._body = body

    async def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


async def _patch(body: Any) -> tuple[int, dict]:
    resp = await admin.handle_deck_admin_settings_update(_Req(body))
    return resp.status, json.loads(resp.body.decode())


async def test_an_unknown_key_is_not_reported_as_saved() -> None:
    """The defect, in its exact shape."""
    status, payload = await _patch({"index": {"hybrid_serch": True}})

    assert status == 400, "a patch that saved nothing returned a success status"
    assert payload["ok"] is False
    assert any("hybrid_serch" in e for e in payload["errors"])


async def test_the_error_names_the_keys_that_would_have_worked() -> None:
    """A typo is the likeliest cause, so the reply should make the fix obvious
    rather than only saying 'unknown'."""
    _, payload = await _patch({"index": {"hybrid_serch": True}})

    assert "hybrid_search" in payload["errors"][0]


async def test_a_valid_key_is_still_written() -> None:
    """The partner. A change that rejected everything would satisfy the tests above
    and break the endpoint."""
    status, payload = await _patch({"chat_preferences": {"markdown": True}})

    assert status == 200
    assert payload["ok"] is True
    assert payload["complete"] is True
    assert payload["updated"] == ["chat.markdown"]


async def test_a_partial_patch_succeeds_but_is_not_complete() -> None:
    """The valid writes really happened, so this stays a 200 — but a caller that
    only checks `ok` would never learn about the rejected key."""
    status, payload = await _patch(
        {"chat_preferences": {"markdown": True, "no_such_pref": 1}}
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["complete"] is False, "a rejected key must show up somewhere"
    assert payload["updated"] == ["chat.markdown"]
    assert any("no_such_pref" in e for e in payload["errors"])


async def test_an_empty_group_is_an_honest_no_op() -> None:
    """Nothing was asked for, so nothing changing is the correct answer — this must
    NOT be swept into the failure case along with the typo."""
    status, payload = await _patch({"index": {}})

    assert status == 200
    assert payload["ok"] is True and payload["complete"] is True
    assert payload["updated"] == []


async def test_a_wrong_type_is_still_rejected() -> None:
    """Type validation existed and must keep working — the unknown-key branch is
    additive, not a replacement."""
    status, payload = await _patch({"chat_preferences": {"markdown": "yes"}})

    assert status == 400
    assert any("expected boolean" in e for e in payload["errors"])


async def test_invalid_json_is_still_a_400() -> None:
    status, payload = await _patch(ValueError("not json"))

    assert status == 400
    assert payload["ok"] is False


@pytest.mark.parametrize(
    "group,key",
    [
        ("code_interpreter", "network_enabled"),
        ("chat_preferences", "streaming"),
        ("index", "reranking"),
    ],
)
async def test_every_group_accepts_its_own_known_keys(group: str, key: str) -> None:
    """Anti-vacuity: the tests above lean on `chat_preferences`. If another group's
    validation were wired to the wrong key set, only this would catch it."""
    status, payload = await _patch({group: {key: True}})

    assert status == 200, payload
    assert payload["updated"], f"{group}.{key} was accepted but not written"


async def test_an_unknown_GROUP_is_ignored_without_claiming_a_write() -> None:
    """A top-level group the server does not handle is skipped entirely (the
    `if "x" in body` guards). That path reports no updates, which is honest — but it
    must not report updates either."""
    status, payload = await _patch({"no_such_group": {"x": True}})

    assert status == 200
    assert payload["updated"] == []
