"""Regression guard for the ${VAULT:...} substitution path.

get_vault().get_secret() returns a SecretStr (NOT a str; str() is the masked "***").
resolve_refs feeds the replacement straight into re.sub, which requires a real str, so a
raw SecretStr raised TypeError and broke the whole substitution API — even under
strict=False, because re.sub joins the pieces AFTER _replace's try/except. #601 fixed it
by revealing the plaintext in _fetch_secret. The existing pure-helpers test never
exercised substitution, which is why the crash went unnoticed; these lock the fix in.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from navig.vault import resolver
from navig.vault.secret_str import SecretStr


def _vault(**secrets):
    """A fake vault whose get_secret returns SecretStr(value) (KeyError if unknown)."""
    v = MagicMock()
    v.get_secret.side_effect = lambda label: SecretStr(secrets[label])
    return v


def test_resolve_refs_substitutes_revealed_plaintext():
    with patch("navig.vault.core.get_vault", return_value=_vault(**{"openai/api_key": "sk-live-123"})):
        out = resolver.resolve_refs("Authorization: Bearer ${VAULT:openai/api_key}")
    assert out == "Authorization: Bearer sk-live-123"  # plaintext, no TypeError


def test_resolve_refs_never_substitutes_the_mask():
    with patch("navig.vault.core.get_vault", return_value=_vault(**{"k": "real-secret"})):
        out = resolver.resolve_refs("${VAULT:k}")
    assert out == "real-secret" and "***" not in out  # reveal(), not str(SecretStr)


def test_resolve_refs_strict_false_still_resolves_a_present_secret():
    # strict=False's contract is "never raise". Pre-#601 it raised TypeError even when the
    # secret resolved fine (the SecretStr blew up at re.sub's join, outside _replace's try).
    with patch("navig.vault.core.get_vault", return_value=_vault(**{"k": "v"})):
        out = resolver.resolve_refs("${VAULT:k}", strict=False)
    assert out == "v"


def test_resolve_refs_strict_false_leaves_a_missing_token_in_place():
    with patch("navig.vault.core.get_vault", return_value=_vault()):  # any label -> KeyError
        out = resolver.resolve_refs("keep ${VAULT:missing}", strict=False)
    assert out == "keep ${VAULT:missing}"


def test_resolve_dict_recurses_and_reveals():
    with patch("navig.vault.core.get_vault", return_value=_vault(**{"k": "V"})):
        out = resolver.resolve_dict({"a": "${VAULT:k}", "b": ["${VAULT:k}", 1]})
    assert out == {"a": "V", "b": ["V", 1]}
