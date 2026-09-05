"""The gateway's storage dir must follow NAVIG_CONFIG_DIR, not a hardcoded ``~/.navig``.

``GatewayConfig`` defaulted ``storage_dir`` to the literal string ``"~/.navig"``, so an
install that moved its config got a **split brain**: config resolved through
``paths.config_dir()`` (which honours ``NAVIG_CONFIG_DIR``) while the gateway's own state —
``events.json``, ``task_queue.json``, ``mesh_peers.json``, sessions — stayed in the real
home. The default install is unaffected either way, since ``config_dir()`` *is* ``~/.navig``
there, which is exactly why it survived: it looks correct on the only machine anyone tests
on.

It also reached into the operator's real home **from the test suite**. An audit of every
read under the real ``~/.navig`` during a full run recorded 22 of them arriving through this
one line — ``NavigGateway.__init__`` builds a ``SystemEventQueue`` on ``storage_dir`` and
``mkdir``s it, so merely constructing a gateway in a test read (and could create) files in
the operator's home. The sibling scar is two comments up in ``server.py``: parsing a config
used to *mint a token into the operator's real config.yaml*.

``tests/platform/test_no_hardcoded_home.py`` guards the same assumption but matches the
expression ``Path.home() / ".navig"`` on the AST; this site spelled it as a string constant
fed to ``.expanduser()``, so it walked straight past. The guard protects a *shape*, not the
surface.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.gateway.server import GatewayConfig


def _storage_dir(raw: dict | None = None) -> Path:
    return GatewayConfig({"gateway": dict(raw or {})}).storage_dir


def test_storage_dir_follows_a_custom_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression: with NAVIG_CONFIG_DIR set, state must not go to the real home."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))

    resolved = _storage_dir()

    assert resolved == tmp_path, (
        f"gateway storage resolved to {resolved} instead of the configured "
        f"{tmp_path} — config and gateway state would live in different places"
    )
    assert resolved != Path("~/.navig").expanduser(), (
        "gateway state still resolves to the operator's real home under a custom "
        "NAVIG_CONFIG_DIR — this is the split brain, and in the suite it means "
        "constructing a gateway reads and mkdirs into their live ~/.navig"
    )


def test_a_default_install_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compatibility: with no env var the answer is still ~/.navig.

    Without this the fix could 'pass' by moving everyone's state somewhere new.
    """
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)

    assert _storage_dir() == Path("~/.navig").expanduser()


def test_an_explicit_storage_dir_still_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Precedence is unchanged: config beats the derived default."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    explicit = tmp_path / "elsewhere"

    assert _storage_dir({"storage_dir": str(explicit)}) == explicit
