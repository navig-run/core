"""Regression: Telegram cards must never be rewritten with their HTML tags showing.

Two separate leaks reached the operator's chat on 2026-10-09:

1. **🔄 Refresh on /status.** Re-rendering within the same minute produces the
   identical card, so ``editMessageText`` is refused with *message is not
   modified*. ``edit_message`` treated every refusal as an HTML parse error and
   resent without ``parse_mode``. That resend is NOT identical (the tags are now
   literal text), so Telegram accepted it, and the card turned into raw
   ``<b>…</b>``. The fallback must fire for markup parse errors only.
2. **"Did you know" tips.** The capability promoter wrote HTML into a
   ``Notification.message``, which is rendered through ``md_to_html``, and that
   escapes HTML. The tags arrived as literal text.

These tests drive the REAL ``_api_call`` through a scripted session, so the
refusal reason is recorded by the production code and not by the test.
"""

from __future__ import annotations

from navig.gateway.channels.telegram import TelegramChannel


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 400 if not payload.get("ok") else 200

    async def json(self) -> dict:
        return self._payload

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class _Session:
    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.bodies: list[dict] = []

    def post(self, url: str, json=None) -> _Resp:
        self.bodies.append(dict(json or {}))
        return _Resp(self._payloads.pop(0))


def _channel(payloads: list[dict]) -> tuple[TelegramChannel, _Session]:
    ch = TelegramChannel.__new__(TelegramChannel)  # no network, no config reads
    session = _Session(payloads)
    ch._session = session
    ch.base_url = "https://api.telegram.org/botTEST"
    return ch, session


def _refused(description: str) -> dict:
    return {"ok": False, "error_code": 400, "description": description}


_OK = {"ok": True, "result": {"message_id": 7}}
_CARD = "🧭 <b>NAVIG Status</b> — <i>Session Snapshot</i>"


async def test_identical_refresh_is_not_resent_as_raw_html():
    ch, session = _channel([
        _refused("Bad Request: message is not modified: specified new message content "
                 "and reply markup are exactly the same"),
        _OK,  # what the old code's plain resend would have been granted
    ])

    result = await ch.edit_message(1, 7, _CARD, parse_mode="HTML")

    assert result is None
    assert len(session.bodies) == 1, (
        "a 'not modified' refusal must not trigger the no-parse_mode resend; "
        f"bodies sent: {session.bodies}"
    )


async def test_a_real_markup_error_still_falls_back_to_plain_text():
    ch, session = _channel([
        _refused("Bad Request: can't parse entities: Unsupported start tag \"x\""),
        _OK,
    ])

    result = await ch.edit_message(1, 7, "<x>broken", parse_mode="HTML")

    assert result == {"message_id": 7}
    assert len(session.bodies) == 2
    assert "parse_mode" not in session.bodies[1]


async def test_send_does_not_strip_markup_for_a_non_markup_refusal():
    ch, session = _channel([_refused("Bad Request: chat not found"), _OK])

    result = await ch.send_message(1, _CARD, parse_mode="HTML")

    assert result is None
    assert len(session.bodies) == 1


async def test_capability_tip_renders_as_formatting_not_literal_tags():
    from navig.agent.proactive.capability_promo import CapabilityPromoter, FeatureInfo
    from navig.gateway.notifications import Notification

    promoter = CapabilityPromoter()
    feature = next(f for f in promoter.features if isinstance(f, FeatureInfo))
    body = promoter._build_promotion_message(feature)

    rendered = Notification(type="reminder", title="NAVIG — Did you know",
                            message=body).to_telegram_message()

    assert "&lt;" not in rendered, f"HTML was escaped into literal tags: {rendered!r}"
    assert f"<b>{feature.name}</b>" in rendered
    assert "<code>" in rendered and feature.example_command in rendered
