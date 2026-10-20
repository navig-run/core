"""Voice calls — the last rung of an unacknowledged emergency.

Pushover can ring through silent mode and repeat until acknowledged, and an SMS
follows if nobody does; but a phone in a bag at 4am is still a phone in a bag. A
call is the rung that actually wakes a person.

Twilio places it (`POST /2010-04-01/Accounts/{sid}/Calls.json`) with TwiML
``<Say>``, so the message is spoken rather than sent. **Off by default**: a call
spends money per attempt, so it has to be turned on deliberately
(`navig notify pushover call <minutes>`), never inferred from a send.

Credentials come from the vault (``twilio_account_sid`` / ``twilio_auth_token``),
never config or logs. The calling number is, in order: the vault's
``twilio_from_number``, a cached choice, or the first voice-capable number on
the account (discovered once and cached — an account may own several).

Nothing here raises: a failed call is reported and the escalation is recorded,
because losing an alarm to a network blip is the exact failure this exists to
prevent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.sax.saxutils import escape as xml_escape

logger = logging.getLogger("navig.notify.call")

#: Twilio's REST API. Chosen per-call (the account is read from the vault).
API = "https://api.twilio.com/2010-04-01"
VOICE_FROM_LABEL = "call_from_number"
SETTING_CALL_MIN = "pushover_call_min"

#: Spoken messages must stay short — a TTS paragraph nobody finishes is not an alert.
_SPOKEN_MAX = 400


@dataclass(frozen=True)
class Outcome:
    ok: bool
    detail: str
    call_sid: str = ""


def _auth() -> tuple[str, str]:
    from navig.vault.core import get_vault, reveal_secret

    v = get_vault()
    if v is None:
        return "", ""
    try:
        return (reveal_secret(v, "twilio_account_sid") or "").strip(), (
            reveal_secret(v, "twilio_auth_token") or ""
        ).strip()
    except Exception:  # noqa: BLE001 — vault locked/unavailable reads as "not configured"
        logger.debug("twilio credentials unreadable", exc_info=True)
        return "", ""


def credentials_ok() -> bool:
    sid, tok = _auth()
    return bool(sid and tok)


# ── numbers ──────────────────────────────────────────────────────────────────


def _cached_from() -> str:
    from navig.notify import prefs

    return (prefs.get_raw(VOICE_FROM_LABEL, "") or "").strip()


def _discover_from() -> str:
    """The account's first voice-capable number (cached; "" when there is none)."""
    sid, tok = _auth()
    if not (sid and tok):
        return ""
    try:
        import requests

        resp = requests.get(
            # API already carries the /2010-04-01 date segment — doubling it 404s.
            f"{API}/Accounts/{sid}/IncomingPhoneNumbers.json?PageSize=50",
            auth=(sid, tok),
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("twilio number discovery failed: %s", exc)
        return ""
    if int(resp.status_code) != 200:  # noqa: PLR2004 — HTTP codes are not magic here
        logger.warning("twilio number discovery: HTTP %s", resp.status_code)
        return ""
    for n in data.get("incoming_phone_numbers", []):
        if (n.get("capabilities") or {}).get("voice"):
            found = str(n.get("phone_number") or "")
            if found:
                return found
    return ""


def from_number() -> str:
    """Where calls come from: vault override → cached choice → discovered."""
    from navig.vault.core import get_vault, reveal_secret

    try:
        v = get_vault()
        direct = (reveal_secret(v, "twilio_from_number") or "").strip() if v else ""
    except Exception:  # noqa: BLE001
        direct = ""
    if direct:
        return direct
    cached = _cached_from()
    if cached:
        return cached
    found = _discover_from()
    if found:
        from navig.notify import prefs

        try:
            prefs.set_setting(VOICE_FROM_LABEL, found)
        except Exception:  # noqa: BLE001 — a cached miss is not worth failing over
            logger.debug("could not cache the voice number", exc_info=True)
    return found


def _target() -> str:
    """Who gets called — the phone number already configured for SMS.

    One number, one field: a second "call target" would be a knob nobody needs,
    and it would drift from the number the SMS escalation uses.
    """
    from navig.notify import prefs

    return prefs.get_target("sms").strip()


def is_ready() -> tuple[bool, str]:
    """``(can_place_a_call, why_not)`` — used by the CLI/Mini App before offering it."""
    if not credentials_ok():
        return False, "Twilio credentials are missing (they live in the vault)"
    if not _target():
        return False, "no phone number to call — set one in Notifications → Channels → SMS"
    if not from_number():
        return False, "no voice-capable Twilio number on this account"
    return True, ""


def call_after_min() -> int | None:
    """Minutes after the emergency to call; ``None`` = calls are switched off.

    ``0`` means "call as soon as it escalates" — only a *configured* value counts,
    so an install that never opted in never spends money.
    """
    from navig.notify import prefs

    raw = prefs.get_raw(SETTING_CALL_MIN, "")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return None
    return minutes if minutes >= 0 else None


# ── the call ─────────────────────────────────────────────────────────────────

_TIMEOUT = 15


def _twiml(text: str) -> str:
    spoken = xml_escape(text.replace("\\n", " ")[:_SPOKEN_MAX])
    # No voice forced: Twilio picks a locale-appropriate one from the number's country.
    return f"<Response><Say>{spoken}</Say></Response>"


def place_call(text: str, *, to: str | None = None, origin: str | None = None) -> Outcome:
    """Place one spoken call. Never raises; an unconfigured setup reports honestly."""
    sid, tok = _auth()
    if not (sid and tok):
        return Outcome(False, "Twilio credentials are missing (they live in the vault)")
    called = (to or "").strip() or _target()
    if not called:
        return Outcome(
            False, "no phone number to call — set one in Notifications → Channels → SMS"
        )
    sending = (origin or "").strip() or from_number()
    if not sending:
        return Outcome(False, "no voice-capable Twilio number on this account")

    try:
        import requests

        resp = requests.post(
            f"{API}/2010-04-01/Accounts/{sid}/Calls.json",
            data={"To": called, "From": sending, "Twiml": _twiml(text)},
            auth=(sid, tok),
            timeout=_TIMEOUT,
        )
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("twilio call failed to reach the API: %s", exc)
        return Outcome(False, f"Twilio unreachable: {exc}")

    if int(resp.status_code) in (200, 201):  # noqa: PLR2004
        sid_out = str(body.get("sid") or "")
        logger.info("twilio call placed (%s → %s, %s)", sending, called, sid_out[:14])
        return Outcome(True, f"calling {called}", call_sid=sid_out)
    # Twilio returns 400 with a human-readable message; never log the auth material.
    errs = body.get("message") or body.get("code") or "rejected by Twilio"
    logger.warning("twilio call rejected: %s", errs)
    return Outcome(False, str(errs))
