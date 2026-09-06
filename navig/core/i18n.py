"""navig.core.i18n — user-facing strings, in the operator's ONE global language.

Composed from the two pieces that already exist rather than replacing either:

* :mod:`navig.core.language` answers *which* language (``user.language``, set
  from chat with ``/lang``, and the name→code bridge "Russian" → ``ru``);
* :class:`navig.agent.conv.localization.LocalizationStore` answers *what the
  string is*, with lazy loading, an English fallback per key, and a promise
  never to raise.

This binds them, so a surface needs one object instead of repeating the
resolve-language-then-look-up dance — which is how three subsystems ended up
shipping hardcoded English while the operator's language was Russian.

⚠ **A translated string must never be able to break the thing it labels.** Every
lookup here degrades: an unknown key returns the key, a missing pool returns the
fallback, a locale file with a broken ``{placeholder}`` returns the unformatted
text. A reminder in the wrong language is a blemish; a reminder that does not
arrive is a broken feature.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Shared strings that are not owned by one feature (boot lines, greetings).
DEFAULT_LOCALES_ROOT = Path(__file__).resolve().parent.parent / "locales"


def current_language() -> str:
    """The ISO code for the operator's global output language, or ``"en"``.

    ``user.language`` holds a NAME ("Russian") because a human wrote it, so it
    goes through ``language_code``. Read via the resolver rather than the
    validated config view — ``_load_global_config()`` drops the whole ``user``
    subtree (#1192), which would make every surface silently English forever.
    """
    try:
        from navig.core.language import language_code, resolve_language  # noqa: PLC0415

        return language_code(resolve_language()) or "en"
    except Exception:  # noqa: BLE001 — a language we cannot resolve is English
        return "en"


class Translator:
    """Localized strings from one locale root, in the current global language."""

    def __init__(self, locales_root: Path | None = None) -> None:
        self._root = locales_root or DEFAULT_LOCALES_ROOT
        self._store: Any = None

    def _backing(self) -> Any:
        if self._store is None:
            from navig.agent.conv.localization import LocalizationStore  # noqa: PLC0415

            self._store = LocalizationStore(locales_root=self._root)
        return self._store

    def t(self, key: str, **fields: object) -> str:
        """One localized string, formatted with *fields*."""
        text = self._backing().get(key, current_language())
        if not fields:
            return text
        try:
            return text.format(**fields)
        except (KeyError, IndexError, ValueError):
            logger.debug("locale placeholder mismatch for %r", key)
            return text

    def pool(self, key: str) -> list[str]:
        """A localized pool of variants — boot lines, greetings, nudges."""
        return self._backing().get_list(key, current_language())

    def pick(self, key: str, *, fallback: list[str] | None = None) -> str:
        """One variant at random from *key*'s pool.

        Falls back to *fallback* when the pool is empty, so a surface keeps
        working against a locale file that has not been written yet.
        """
        import random  # noqa: PLC0415

        options = self.pool(key) or (fallback or [])
        if not options:
            return ""
        return options[random.randrange(len(options))]

    def reset(self) -> None:
        """Drop the cached store — the language or the files changed."""
        self._store = None


#: The shared translator for cross-cutting strings.
_shared: Translator | None = None


def shared() -> Translator:
    """The process-wide :class:`Translator` over ``navig/locales``."""
    global _shared
    if _shared is None:
        _shared = Translator()
    return _shared


def t(key: str, **fields: object) -> str:
    """Shortcut for ``shared().t(...)``."""
    return shared().t(key, **fields)


def pick(key: str, *, fallback: list[str] | None = None) -> str:
    """Shortcut for ``shared().pick(...)``."""
    return shared().pick(key, fallback=fallback)
