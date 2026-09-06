"""LocalizationStore: JSON-backed i18n with lazy loading and graceful fallback."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_LOCALES_ROOT = Path(__file__).parent / "locales"


class LocalizationStore:
    """
    Loads locale strings from navig/agent/conv/locales/{lang}.json on first use per language.
    Owns a per-language cache to avoid redundant disk reads.
    Guarantees: get() never raises; returns the key itself when any lookup fails.
    """

    def __init__(self, locales_root: Path | None = None) -> None:
        self._root: Path = locales_root if locales_root is not None else _DEFAULT_LOCALES_ROOT
        # `object` values, not `str`: a locale entry may be a single string or a
        # POOL of variants (see get_list).
        self._cache: dict[str, dict[str, object]] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    def get(self, key: str, lang: str) -> str:
        """Return the localized string for *key* in *lang*.

        Falls back to English, then to the raw key if still not found.
        """
        value = self._load_lang(lang).get(key)
        if isinstance(value, str):
            return value
        if lang != "en":
            value = self._load_lang("en").get(key)
            if isinstance(value, str):
                return value
        return key

    def get_list(self, key: str, lang: str) -> list[str]:
        """Return a localized LIST for *key* — a pool of rotating variants.

        Boot announcements and greetings are pools, not single strings, and a
        pool that has to be spelled out as ``boot.0``…``boot.9`` cannot be
        shortened or extended by editing the locale file alone. Same fallback
        chain as :meth:`get`; an empty list when nothing resolves, so a caller
        can test it rather than handle an exception.
        """
        for candidate in (lang, "en") if lang != "en" else (lang,):
            value = self._load_lang(candidate).get(key)
            if isinstance(value, list) and value:
                return [str(v) for v in value]
        return []

    def preload(self, *langs: str) -> None:
        """Eagerly load locale files to ensure zero disk I/O on first get()."""
        for lang in langs:
            self._load_lang(lang)

    # ── Internal ────────────────────────────────────────────────────────────

    def _load_lang(self, lang: str) -> dict[str, object]:
        if lang in self._cache:
            return self._cache[lang]
        path = self._root / f"{lang}.json"
        data: dict[str, object] = {}
        try:
            with path.open(encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                # A list value is PRESERVED rather than stringified: `str([...])`
                # turned a pool of variants into its own repr, which is why this
                # store could only ever hold single strings.
                data = {
                    str(k): ([str(i) for i in v] if isinstance(v, list) else str(v))
                    for k, v in loaded.items()
                }
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
            logger.debug("Locale file unavailable for %r: %s", lang, exc)
        self._cache[lang] = data
        return data
