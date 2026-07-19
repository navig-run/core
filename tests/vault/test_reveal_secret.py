"""Unit tests for navig.vault.core.reveal_secret.

Regression guard for the SecretStr footgun: get_secret() returns a SecretStr, and
callers that did `(vault.get_secret(x) or "").strip()` triggered AttributeError
(SecretStr has no .strip()) which their `except` swallowed — so present keys were
reported absent. This helper is the single correct unwrap.
"""

from __future__ import annotations

from navig.vault.core import reveal_secret
from navig.vault.secret_str import SecretStr


class _StubVault:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping

    def get_secret(self, label: str):
        if label not in self._mapping:
            raise KeyError(f"Vault item not found: {label!r}")
        return self._mapping[label]


def test_reveal_secret_unwraps_and_strips_secretstr():
    vault = _StubVault({"brave": SecretStr("  sk-brave-123  ")})
    assert reveal_secret(vault, "brave") == "sk-brave-123"


def test_reveal_secret_accepts_plain_str():
    vault = _StubVault({"x": "  plain-value  "})
    assert reveal_secret(vault, "x") == "plain-value"


def test_reveal_secret_missing_label_returns_empty():
    assert reveal_secret(_StubVault({}), "absent") == ""


def test_reveal_secret_none_returns_empty():
    assert reveal_secret(_StubVault({"x": None}), "x") == ""


def test_reveal_secret_empty_secretstr_returns_empty():
    assert reveal_secret(_StubVault({"x": SecretStr("")}), "x") == ""


def test_reveal_secret_never_raises_on_broken_vault():
    class _Broken:
        def get_secret(self, label):  # noqa: ANN001, ANN201
            raise RuntimeError("vault locked")

    assert reveal_secret(_Broken(), "anything") == ""
