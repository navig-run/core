"""Redaction for conversation prose.

``redact_secrets`` runs before anything is archived or indexed. It reuses the core
secret patterns minus the MySQL ``-p <value>`` rule, which in prose destroys ordinary text
such as ``-p 8080`` or ``npm -p``. PII redaction (``redact_pii``) is a separate, stricter
tier applied only to text that leaves the machine.
"""

from __future__ import annotations

import re

_PROSE_UNSAFE = {r"-p\s+([^\s]+)"}

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?![\w.])")

_patterns: list | None = None


def _prose_patterns() -> list:
    global _patterns
    if _patterns is None:
        from navig.core.security import DEFAULT_REDACT_PATTERNS  # noqa: PLC0415

        _patterns = [(p, r) for p, r in DEFAULT_REDACT_PATTERNS if p.pattern not in _PROSE_UNSAFE]
    return _patterns


def redact_secrets(text: str) -> str:
    if not text:
        return text
    from navig.core.security import redact_sensitive_text  # noqa: PLC0415

    return redact_sensitive_text(text, patterns=_prose_patterns())


def redact_pii(text: str) -> str:
    """Secrets plus e-mail addresses and phone numbers — for payloads sent off the machine."""
    text = redact_secrets(text)
    text = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[phone]", text)
