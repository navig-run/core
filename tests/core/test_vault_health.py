"""A missing vault engine must not be reported as a missing credential.

Sibling of ``tests/telegram/test_vault_unavailable.py``, which covers the Telegram surface.
This covers the shared helper and the Cloudflare surfaces that use it.

The bug: navig-vault is a hard dependency, but pip does not retroactively install new
dependencies into an existing environment. When it goes missing, every credential read comes
back empty — because those reads deliberately swallow errors so a caller can ask "is this
set?" without handling a locked vault — and the caller then blames the user's configuration.
``navig lighthouse deploy`` said "No Cloudflare API token", and `miniapp` said "no Cloudflare
credential — run `navig lighthouse login`", both over a token that was present and intact.
"""

from __future__ import annotations

import pytest

from navig.core import vault_health


def test_reason_is_none_when_the_vault_opens(monkeypatch):
    """The common case must stay silent, or every message grows a false caveat."""
    monkeypatch.setattr(vault_health, "vault_unavailable_reason", lambda: None)
    assert vault_health.explain_missing_credential("X", "do Y") == "no X — do Y"


def test_a_missing_engine_is_reported(monkeypatch):
    import navig.vault  # noqa: F401 - ensure the module exists to patch through

    def boom(*_a, **_k):
        raise ImportError("NAVIG's vault requires the navig-vault engine")

    monkeypatch.setattr("navig.vault.get_vault", boom)
    assert "navig-vault" in (vault_health.vault_unavailable_reason() or "")


def test_a_locked_vault_is_NOT_reported_as_a_missing_engine(monkeypatch):
    """Locked is a different problem with a different repair.

    Reporting it here would tell a user with a perfectly installed engine to reinstall it.
    Per-label handling in each caller already covers the locked case.
    """
    def boom(*_a, **_k):
        raise RuntimeError("vault is locked")

    monkeypatch.setattr("navig.vault.get_vault", boom)
    assert vault_health.vault_unavailable_reason() is None


def test_the_message_says_the_credentials_are_probably_fine(monkeypatch):
    """The whole point: do not send someone to re-create a credential they still have."""
    monkeypatch.setattr(vault_health, "vault_unavailable_reason", lambda: "engine gone")
    msg = vault_health.explain_missing_credential(
        "Cloudflare credential", "run `navig lighthouse login`"
    )
    assert "engine gone" in msg
    assert "present and intact" in msg
    assert "run `navig lighthouse login`" not in msg, (
        "the configuration repair must NOT be advertised when the engine is the problem -- "
        "that is the entire bug this exists to prevent"
    )


def test_the_install_command_is_not_duplicated(monkeypatch):
    """The ImportError text already carries it; saying it twice reads as noise."""
    monkeypatch.setattr(
        vault_health,
        "vault_unavailable_reason",
        lambda: "requires the navig-vault engine. Install it with: pip install navig-vault",
    )
    msg = vault_health.explain_missing_credential("X", "do Y")
    assert msg.count("pip install navig-vault") == 1


# ── the surfaces that consume it ─────────────────────────────────────────────


def test_resolve_cf_token_returns_empty_when_the_engine_is_missing(monkeypatch):
    """Pins the precondition the fix rests on: the read degrades silently to ''.

    If this ever stops being true the callers below no longer need the distinction, and
    this file should be revisited rather than left asserting a hypothetical.
    """
    from navig.commands import lighthouse

    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    def boom(*_a, **_k):
        raise ImportError("NAVIG's vault requires the navig-vault engine")

    monkeypatch.setattr("navig.vault.get_vault", boom)
    assert lighthouse.resolve_cf_token() == ""


@pytest.mark.parametrize("what", ["Cloudflare API token", "Cloudflare credential"])
def test_cloudflare_surfaces_use_the_shared_explanation(what):
    """The message the user sees comes from one place, so it cannot drift per command."""
    from pathlib import Path

    for rel in ("core/navig/commands/lighthouse.py", "core/navig/commands/miniapp.py"):
        src = Path(__file__).resolve().parents[3].joinpath(rel).read_text(encoding="utf-8")
        assert "explain_missing_credential" in src, (
            f"{rel} reports a missing Cloudflare credential without the engine/config "
            f"distinction -- a missing navig-vault will read as 'you never configured this'"
        )


def test_deploy_does_not_prompt_for_a_new_token_when_the_engine_is_missing():
    """The costliest form of this bug: being walked into minting a duplicate credential.

    `navig lighthouse deploy` reads the token, gets "" because the engine is gone, and used
    to go straight into the interactive "let's create a Cloudflare API token" flow -- so the
    user ends up with a second token while the first sits unreadable in the vault. The check
    has to happen BEFORE _prompt_for_token(), which is what this pins.
    """
    from pathlib import Path as _P

    src = _P(__file__).resolve().parents[3].joinpath(
        "core/navig/commands/lighthouse.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    # The CALL, not the `def` -- the definition sits near the top of the file and would make
    # this assertion pass no matter where the guard went.
    guard = next(i for i, l in enumerate(lines) if "engine_gone = vault_unavailable_reason()" in l)
    prompt = next(i for i, l in enumerate(lines) if "= _prompt_for_token()" in l)
    assert guard < prompt, (
        "the engine check must come before the interactive token prompt -- otherwise a "
        "missing vault engine walks the user into creating a duplicate Cloudflare token"
    )
