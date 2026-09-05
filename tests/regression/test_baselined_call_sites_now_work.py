"""Eight calls that could only ever raise TypeError, now doing what they were written to do.

Both groups were found by the `call-arg` gate (#1032) and baselined as "real, needs a
product decision". The decision turned out to be readable from the surrounding code rather
than a matter of taste, so they are fixed here.

**Group 1 — `Vault.get_secret(provider, field, caller=…)`** (4 sites). `get_secret` takes
ONE argument, a label. The reader with that shape is `Vault.get(provider, profile_id=None,
caller=…)`, which returns the whole `Credential`; the "field" is a key inside `.data`, not a
positional argument. Every call raised into a bare `except`, so the vault was never
consulted and the resolvers silently returned nothing.

**Group 2 — `set_ai_state(channel=…)`** (4 sites). There is no `channel` parameter, and
`mode` is required. Two of the four are NOT inside a try, so the evening-log reply crashed.

⚠ The subtle part of group 2 is `persona`. The UPSERT is::

    ON CONFLICT(user_id) DO UPDATE SET ... persona = excluded.persona

so a call that omits `persona` writes NULL over whatever the user had. Passing it is not
tidiness — it is the difference between updating context and wiping a setting.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# ── Group 1: the vault resolvers ────────────────────────────────────────────────────


class _Cred:
    def __init__(self, data: dict[str, str]) -> None:
        self.data = data


class _Vault:
    """Records how it was called; only implements the REAL Vault surface.

    Deliberately does not accept the old broken shape — a stub permissive enough to
    swallow `get_secret(a, b, caller=…)` is what let the defect hide in the first place.
    """

    def __init__(self, creds: dict[str, dict[str, str]]) -> None:
        self._creds = creds
        self.calls: list[tuple[str, str]] = []

    def get(self, provider: str, profile_id: str | None = None, caller: str = "unknown"):
        self.calls.append((provider, caller))
        data = self._creds.get(provider)
        return _Cred(data) if data is not None else None


@pytest.fixture
def vault(monkeypatch):
    def _install(creds):
        v = _Vault(creds)
        import navig.vault as vault_pkg

        monkeypatch.setattr(vault_pkg, "get_vault", lambda *a, **k: v)
        return v

    return _install


def test_the_telegram_token_resolver_reads_the_vault(vault):
    v = vault({"telegram": {"bot_token": "123:ABC"}})

    from navig.messaging.secrets import _resolve_telegram_token_from_legacy_store

    assert _resolve_telegram_token_from_legacy_store() == "123:ABC"
    assert v.calls, "the vault was never consulted — the call raised before reaching it"


def test_the_telegram_token_resolver_tries_every_field(vault):
    """token → bot_token → api_key, from ONE credential rather than one lookup per key."""
    v = vault({"telegram": {"api_key": "fallback-key"}})

    from navig.messaging.secrets import _resolve_telegram_token_from_legacy_store

    assert _resolve_telegram_token_from_legacy_store() == "fallback-key"
    assert len(v.calls) == 1, f"expected a single credential fetch, got {v.calls}"


def test_the_telegram_uid_resolver_reads_the_vault(vault):
    v = vault({"telegram": {"uid": "42"}})

    from navig.messaging.secrets import _resolve_telegram_uid_from_legacy_store

    assert _resolve_telegram_uid_from_legacy_store() == "42"
    assert v.calls


def test_a_missing_credential_is_not_an_error(vault):
    vault({})

    from navig.messaging.secrets import _resolve_telegram_token_from_legacy_store

    assert _resolve_telegram_token_from_legacy_store() == ""


# ── Group 2: set_ai_state ───────────────────────────────────────────────────────────


def test_set_ai_state_is_called_with_a_satisfiable_signature():
    """Bind the real signature against the shape the fixed call sites use.

    `inspect.signature().bind()` is the check that matters: it fails for exactly the
    reasons the old calls did — an unexpected `channel`, or a missing `mode`.
    """
    import inspect

    from navig.store.runtime import RuntimeStore

    sig = inspect.signature(RuntimeStore.set_ai_state)

    sig.bind(
        SimpleNamespace(),  # self
        user_id=1,
        chat_id=2,
        mode="active",
        persona="assistant",
        context={"eve_pending": {"active": False}},
    )

    with pytest.raises(TypeError):
        sig.bind(SimpleNamespace(), user_id=1, channel="telegram", chat_id="2", context={})


def test_persona_is_not_erased_by_a_context_update(tmp_path, monkeypatch):
    """The UPSERT overwrites `persona` with whatever is passed — so it must be passed.

    This is the real damage a "just drop the bad kwarg" fix would have caused: the eve
    flows update *context*, and would have silently cleared the user's persona each time.
    """
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))

    from navig.store.runtime import RuntimeStore

    store = RuntimeStore()
    store.set_ai_state(user_id=7, chat_id=9, mode="active", persona="operator",
                       context={"a": 1})

    state = store.get_ai_state(7)
    assert state is not None
    store.set_ai_state(
        user_id=7,
        chat_id=9,
        mode=state.get("mode") or "active",
        persona=state.get("persona") or "assistant",
        context={"a": 2},
    )

    after = store.get_ai_state(7)
    assert after["persona"] == "operator", (
        "the persona was overwritten by a context-only update — the UPSERT sets "
        "persona = excluded.persona, so every caller must pass it through"
    )
    assert after["context"] == {"a": 2}
    assert after["mode"] == "active"


def test_every_set_ai_state_call_site_passes_mode_and_persona():
    """Pin the CALL SITES, not just the store's behaviour.

    The test above proves the store overwrites `persona`, and that a caller which passes it
    is safe. Neither fact stops someone deleting the argument from a call site — so assert
    the sites themselves, on the AST.

    `mode` is required (its absence is what raised TypeError); `persona` is required in
    practice, because omitting it writes NULL over the user's setting.
    """
    import ast
    from pathlib import Path

    core = Path(__file__).resolve().parents[2] / "navig"
    files = [
        core / "gateway" / "channels" / "telegram_keyboards.py",
        core / "gateway" / "channels" / "telegram_commands.py",
    ]

    seen = 0
    offenders: list[str] = []
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "set_ai_state":
                continue
            seen += 1
            kwargs = {k.arg for k in node.keywords if k.arg}
            missing = {"mode", "persona"} - kwargs
            if missing:
                offenders.append(f"{f.name}:{node.lineno} missing {sorted(missing)}")
            if "channel" in kwargs:
                offenders.append(f"{f.name}:{node.lineno} passes `channel`, which does not exist")

    assert seen >= 4, f"expected the 4 known call sites, found {seen} — did they move?"
    assert not offenders, (
        "set_ai_state call sites that cannot succeed or would erase the persona:\n  "
        + "\n  ".join(offenders)
    )
