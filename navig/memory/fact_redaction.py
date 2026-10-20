"""navig.memory.fact_redaction — keep secrets out of the long-term fact store.

Every key-fact write funnels through :func:`redact_fact` (called inside
``KeyFactStore.upsert`` / ``update_content``), so the auto-extractor, the agent and
MCP memory tools, the CLI and the deck all get the same treatment without each caller
remembering to.

Policy (owner decision, 2026-10-16): a fact that carries a secret is **stored with the
secret value replaced** by ``***REDACTED***`` — the surrounding fact ("uses 1Password
for work") is kept, the credential is not. The redacted text is what reaches the FTS
index and the embedding provider, so the value never lands anywhere on disk. A fact
that is *nothing but* a secret once redacted is refused with
:class:`SensitiveContentError` — an empty "password is ***" row has no value to keep.

Rules, in order:

1. ``navig.core.security.DEFAULT_REDACT_PATTERNS`` — provider API keys, bearer and
   basic auth, PEM blocks, connection strings (the same set the logs use).
2. ``navig.hooks.security_scan`` credential literals (``password = "…"``).
3. Spoken secrets — ``password is hunter2`` / ``PIN: 4821`` / ``my 2fa code was 112233``.
4. Payment card numbers that pass the Luhn check, and IBANs that pass mod-97.
"""

from __future__ import annotations

import re

from navig.core.security import redact_sensitive_text

PLACEHOLDER = "***REDACTED***"


class SensitiveContentError(ValueError):
    """The fact was only a secret; after redaction nothing worth storing remains."""


# "password is X", "PIN: 4821", "passcode = X", "my 2fa code was 112233".
# A separator (is/was/=/:) is REQUIRED: "pin the tab" or "token budget" must not match.
_SPOKEN_SECRET = re.compile(
    r"(?i)\b("
    r"pass(?:word|wd|code|phrase)|pin(?:\s+code)?|secret\s+key|client\s+secret|"
    r"api[\s_-]?key|access[\s_-]?key|private[\s_-]?key|(?:auth|access|bearer|api)[\s_-]?token|"
    r"cvv|cvc|security\s+code|otp|one[\s-]time\s+code|(?:2fa|mfa|totp|verification)\s+code|"
    r"recovery\s+(?:code|phrase)|seed\s+phrase|ssn|social\s+security\s+number"
    r")(\s*(?:is|was|are|=|:)\s*)"
    r"(?![*]{3}REDACTED)"
    r"([\"'`]?)([^\s\"'`]{3,})\3",
)

# Card candidates: 13–19 digits, optionally grouped by spaces or dashes.
_CARD_CANDIDATE = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")

# IBAN candidates: country code, 2 check digits, 11–30 alphanumerics (spaces allowed).
_IBAN_CANDIDATE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")

# Words that carry no information once the secret is gone.
_LABEL_WORDS = frozenset(
    {
        "password", "passwd", "passcode", "passphrase", "pwd", "pin", "code", "secret",
        "key", "api", "access", "private", "token", "auth", "bearer", "cvv", "cvc",
        "security", "otp", "one", "time", "2fa", "mfa", "totp", "verification",
        "recovery", "seed", "phrase", "ssn", "social", "number", "card", "iban",
        "the", "my", "his", "her", "their", "our", "your", "user", "user's", "users",
        "is", "was", "are", "and", "for", "of", "to", "a", "an", "it", "its", "this",
        "that", "with", "redacted",
    }
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'_-]{2,}")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _iban_ok(candidate: str) -> bool:
    s = candidate.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    rearranged = s[4:] + s[:4]
    try:
        numeric = "".join(str(int(c, 36)) for c in rearranged)
    except ValueError:
        return False
    return int(numeric) % 97 == 1


def _redact_cards(text: str) -> str:
    def _sub(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return PLACEHOLDER
        return m.group(0)

    return _CARD_CANDIDATE.sub(_sub, text)


def _redact_ibans(text: str) -> str:
    return _IBAN_CANDIDATE.sub(lambda m: PLACEHOLDER if _iban_ok(m.group(0)) else m.group(0), text)


def _redact_spoken(text: str) -> str:
    return _SPOKEN_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{PLACEHOLDER}", text)


def _redact_credential_literals(text: str) -> str:
    # Lazy: hooks.security_scan is a leaf module, but keep memory imports light.
    from navig.hooks.security_scan import _SECRET_PATTERNS

    for pattern, _reason in _SECRET_PATTERNS:
        text = pattern.sub(PLACEHOLDER, text)
    return text


def _normalise_placeholders(text: str) -> str:
    # security.py keeps a hint of the kind ("sk-***REDACTED***"); that is fine to keep.
    # Collapse runs like "***REDACTED*** ***REDACTED***" into one.
    return re.sub(r"(?:\*{3}REDACTED\*{3}[\s,;]*){2,}", PLACEHOLDER + " ", text).strip()


def redact_fact(content: str) -> tuple[str, bool]:
    """Return ``(text_to_store, was_redacted)`` for a key-fact *content*.

    Raises :class:`SensitiveContentError` when the fact holds nothing but a secret.
    Never logs or returns the original value.
    """
    if not content:
        return content, False
    text = redact_sensitive_text(content)
    text = _redact_credential_literals(text)
    text = _redact_spoken(text)
    text = _redact_cards(text)
    text = _redact_ibans(text)
    if text == content:
        return content, False
    text = _normalise_placeholders(text)
    if not has_substance(text):
        raise SensitiveContentError("fact contains only a secret — not stored")
    return text, True


def has_substance(text: str) -> bool:
    """True when *text* still says something once secrets and secret labels are ignored."""
    stripped = re.sub(r"\S*\*{3}REDACTED\*{3}\S*", " ", text)
    return any(w.lower() not in _LABEL_WORDS for w in _WORD.findall(stripped))


def contains_secret(content: str) -> bool:
    """True when :func:`redact_fact` would change *content* (or refuse it)."""
    try:
        return redact_fact(content)[1]
    except SensitiveContentError:
        return True
