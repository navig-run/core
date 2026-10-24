"""GET/POST /api/deck/mind — the shared NAVIG MIND preference document.

One route for the whole MIND profile, so the OS and ORB read and write the SAME
config (``~/.navig/config.yaml`` ``mind.*``) — never a competing store:

    {
      "enabled": bool,
      "conversation_language": "auto"|"en"|"ru"|"fr",
      "creative_language": "en"|"ru"|"fr",
      "engineering_language": "en"|"ru"|"fr",
      "commentary_language": "en"|"ru"|"fr",
      "lexicon": {"enabled": bool, "density": "off"|"minimal"|"balanced"|"detailed"|"intensive"},
      "digitalghost": {"enabled": bool, "frequency": "off"|"rare"|"balanced"|"deep"|"retrospective"},
      "artifact_language_firewall": true
    }

POST takes any subset of that shape (the firewall is read-only and is rejected if
sent). Every key is whitelisted and type-checked; a bad value is a 400 and NOTHING
is written (validate all, then write). Writes go through ``ConfigManager.set_global``
one leaf at a time. GET returns the *effective* profile (preference hierarchy resolved).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None

logger = logging.getLogger(__name__)

#: Languages a per-context default may take ("auto" only makes sense for conversation).
_LANG_CHOICES = {"en", "ru", "fr"}
_CONVERSATION_CHOICES = {"auto", "en", "ru", "fr"}


def _read() -> dict[str, Any]:
    """The resolved effective profile, firewall included."""
    from navig.mind import get_effective_mind

    return get_effective_mind().to_dict()


def _plan_writes(body: Any) -> tuple[list[tuple[str, Any]], str | None]:
    """Validate *body*; return ``(writes, error)``. Nothing is written here."""
    from navig.mind import DIGITALGHOST_FREQUENCIES, LEXICON_DENSITIES

    if not isinstance(body, dict) or not body:
        return [], "send a JSON object with at least one setting"

    # The firewall is mandatory and never user-writable.
    if "artifact_language_firewall" in body:
        return [], "artifact_language_firewall is read-only (always enforced)"

    allowed = {
        "enabled",
        "conversation_language",
        "creative_language",
        "engineering_language",
        "commentary_language",
        "lexicon",
        "digitalghost",
    }
    if unknown := sorted(set(body) - allowed):
        return [], f"unknown setting(s): {', '.join(unknown)}"

    writes: list[tuple[str, Any]] = []

    def _section(name: str) -> dict | None:
        val = body.get(name)
        return val if isinstance(val, dict) else None

    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            return [], "enabled must be true or false"
        writes.append(("mind.enabled", body["enabled"]))

    for field, choices, path in (
        ("conversation_language", _CONVERSATION_CHOICES, "mind.conversation_language"),
        ("creative_language", _LANG_CHOICES, "mind.creative_language"),
        ("engineering_language", _LANG_CHOICES, "mind.engineering_language"),
        ("commentary_language", _LANG_CHOICES, "mind.commentary_language"),
    ):
        if field in body:
            if body[field] not in choices:
                return [], f"{field} must be one of {', '.join(sorted(choices))}"
            writes.append((path, body[field]))

    if "lexicon" in body:
        sec = _section("lexicon")
        if sec is None or set(sec) - {"enabled", "density"}:
            return [], 'lexicon must be {"enabled": bool, "density": str}'
        if "enabled" in sec and not isinstance(sec["enabled"], bool):
            return [], "lexicon.enabled must be true or false"
        if "density" in sec and sec["density"] not in LEXICON_DENSITIES:
            return [], f"lexicon.density must be one of {', '.join(sorted(LEXICON_DENSITIES))}"
        if "enabled" in sec:
            writes.append(("mind.lexicon.enabled", sec["enabled"]))
        if "density" in sec:
            writes.append(("mind.lexicon.density", sec["density"]))

    if "digitalghost" in body:
        sec = _section("digitalghost")
        if sec is None or set(sec) - {"enabled", "frequency"}:
            return [], 'digitalghost must be {"enabled": bool, "frequency": str}'
        if "enabled" in sec and not isinstance(sec["enabled"], bool):
            return [], "digitalghost.enabled must be true or false"
        if "frequency" in sec and sec["frequency"] not in DIGITALGHOST_FREQUENCIES:
            return [], (
                f"digitalghost.frequency must be one of "
                f"{', '.join(sorted(DIGITALGHOST_FREQUENCIES))}"
            )
        if "enabled" in sec:
            writes.append(("mind.digitalghost.enabled", sec["enabled"]))
        if "frequency" in sec:
            writes.append(("mind.digitalghost.frequency", sec["frequency"]))

    return writes, None


async def handle_mind_get(request: "web.Request") -> "web.Response":
    return web.json_response(await asyncio.to_thread(_read))


async def handle_mind_post(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid JSON body"}, status=400)
    writes, error = _plan_writes(body)
    if error:
        return web.json_response({"error": error}, status=400)

    def _apply() -> dict[str, Any]:
        from navig.config import get_config_manager

        cm = get_config_manager()
        for key, value in writes:
            cm.set_global(key, value)
        return _read()

    try:
        profile = await asyncio.to_thread(_apply)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mind save failed: %s", exc)
        return web.json_response({"error": f"Couldn't save: {exc}"}, status=500)
    return web.json_response(profile)
