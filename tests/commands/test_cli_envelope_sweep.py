"""Finishing the CLI json_ok-envelope sweep: `navig gateway` and `navig browser`.

Gateway routes answer ``json_ok(payload)`` = ``{"ok":…, "data":<payload>, "error":…}``, so a
CLI reading a field straight off ``response.json()`` always misses — and a miss looks exactly
like "the daemon has nothing to report". After flux (#713) and cron (#714), seven more sites:

``commands/gateway.py``
  * ``:830``  ``.get("sessions", [])``   vs ``/sessions``           -> `gateway sessions` listed NONE
  * ``:1112`` ``.get("heartbeat"/"config")`` vs ``/status``          -> heartbeat status BLANK
  * ``:1165`` ``.get("suppressed"/"issues")`` vs ``/heartbeat/trigger`` -> never suppressed, issues hidden
  * ``:1193`` ``.get("history", [])``    vs ``/heartbeat/history``  -> history always EMPTY
  * ``:1278`` ``.get("pending", [])``    vs ``/approval/pending``   -> **pending approvals INVISIBLE**

``commands/browser.py``
  * ``:46``  ``.get("started")/.get("has_page")`` vs ``/browser/status``     -> always "not started"
  * ``:104`` ``.get('path')``            vs ``/browser/screenshot``  -> "Screenshot saved: unknown"

**The exception that must never be swept:** ``_safe_get_error`` reads ``data.get("error")``
off the RAW body — and that is CORRECT, because ``envelope_error`` puts the message at the
**top level** (``{"ok": False, "data": None, "error": <message>, "error_code": …}``).
Unwrapping there would replace every browser error message with "Unknown error". The last
test class pins that so a future "helpful" sweep can't break it.
"""

from __future__ import annotations

import unittest

from navig.commands.browser import _safe_get_error
from navig.commands.browser import _unwrap as browser_unwrap
from navig.commands.gateway import _unwrap as gateway_unwrap


def _ok(payload):
    """Exactly routes/common.envelope_ok."""
    return {"ok": True, "data": payload, "error": None}


def _err(message):
    """Exactly routes/common.envelope_error."""
    return {"ok": False, "data": None, "error": message, "error_code": "internal_error"}


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class TestGatewayCliReads(unittest.TestCase):
    """Each command's exact expression against the exact envelope its route returns."""

    def test_sessions_are_listed(self):
        body = _ok({"sessions": [{"key": "s1"}, {"key": "s2"}], "total": 2})
        self.assertEqual(len(gateway_unwrap(body).get("sessions", [])), 2)
        self.assertEqual(body.get("sessions", []), [], "pre-fix: always empty")

    def test_heartbeat_status_sections(self):
        body = _ok({"heartbeat": {"enabled": True}, "config": {"interval": 30}})
        self.assertEqual(gateway_unwrap(body).get("heartbeat", {}).get("enabled"), True)
        self.assertEqual(body.get("heartbeat", {}), {}, "pre-fix: blank status")

    def test_heartbeat_trigger_flags(self):
        body = _ok({"suppressed": True, "issues": ["disk"]})
        self.assertTrue(gateway_unwrap(body).get("suppressed"))
        self.assertIsNone(body.get("suppressed"), "pre-fix: never reported suppressed")

    def test_heartbeat_history(self):
        body = _ok({"history": [{"at": "t1"}]})
        self.assertEqual(len(gateway_unwrap(body).get("history", [])), 1)
        self.assertEqual(body.get("history", []), [], "pre-fix: always empty")

    def test_pending_approvals_are_visible(self):
        """The most consequential: an operator could not see what awaited their decision."""
        body = _ok({"pending": [{"id": "req-1", "tool": "shell"}]})
        self.assertEqual(len(gateway_unwrap(body).get("pending", [])), 1)
        self.assertEqual(body.get("pending", []), [], "pre-fix: approvals invisible")


class TestBrowserCliReads(unittest.TestCase):
    def test_status_flags(self):
        body = _ok({"started": True, "has_page": True})
        self.assertTrue(browser_unwrap(body).get("started"))
        self.assertIsNone(body.get("started"), "pre-fix: always 'not started'")

    def test_screenshot_path(self):
        body = _ok({"path": "/tmp/shot.png"})
        self.assertEqual(browser_unwrap(body).get("path", "unknown"), "/tmp/shot.png")
        self.assertEqual(body.get("path", "unknown"), "unknown", "pre-fix: always 'unknown'")


class TestErrorHelperMustNotUnwrap(unittest.TestCase):
    """THE EXCEPTION. `envelope_error` carries the message at the TOP level with data=None,
    so `_safe_get_error` must read the raw body. Unwrapping would turn every browser error
    into "Unknown error" — this pins it against a future sweep."""

    def test_error_message_is_read_from_the_top_level(self):
        self.assertEqual(_safe_get_error(_Resp(_err("browser is not running"), 503)),
                         "browser is not running")

    def test_unwrapping_an_error_envelope_would_lose_the_message(self):
        """Demonstrates WHY the exception exists, so nobody 'fixes' it."""
        self.assertEqual(browser_unwrap(_err("boom")), {})
        self.assertEqual(browser_unwrap(_err("boom")).get("error", "Unknown error"),
                         "Unknown error")

    def test_non_json_body_still_degrades_to_a_useful_message(self):
        class Bad:
            status_code = 500

            def json(self):
                raise ValueError("not json")

        self.assertIn("500", _safe_get_error(Bad()))


class TestRoutesReallyUseTheEnvelope(unittest.TestCase):
    """Pins the premise every fix above rests on."""

    def test_the_routes_answer_with_json_ok(self):
        import inspect

        from navig.gateway.routes import approval, browser, heartbeat

        self.assertIn("json_ok", inspect.getsource(approval))
        self.assertIn('json_ok({"history"', inspect.getsource(heartbeat).replace("'", '"'))
        self.assertIn('"started"', inspect.getsource(browser))

    def test_envelope_error_puts_the_message_at_the_top_level(self):
        from navig.gateway.routes.common import envelope_error

        env = envelope_error("nope", code="bad")
        self.assertEqual(env["error"], "nope")
        self.assertIsNone(env["data"])
