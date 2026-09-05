"""The whole `navig cron` CLI reported nothing — it never unwrapped the json_ok envelope.

Every cron gateway route answers ``json_ok(payload)`` = ``{"ok": …, "data": <payload>,
"error": …}`` (``routes/common.envelope_ok``), but ``commands/cron.py`` read fields straight
off ``response.json()``. The field lives one level down, so every read missed — and a miss
looks exactly like "the daemon has nothing to report":

* ``:51``  ``.get("jobs", [])``      vs ``json_ok({"jobs": …})``      -> `cron list` showed NO jobs
* ``:111`` ``job.get('id')``          vs ``json_ok(job.to_dict())``    -> `cron add` printed "Created job: None"
* ``:160`` ``result.get("success")``  vs ``json_ok({"success": …})``   -> `cron run` reported "Job failed" ON SUCCESS
* ``:224`` ``data.get("cron", {})``   vs ``/status json_ok({"cron": …})`` -> `cron status` blank

Same class as the flux surface (#713); found by a detector correlating each route's
``json_ok`` usage with the CLI call sites that read it. See [[navig-mesh-audit]].
"""

from __future__ import annotations

import unittest

from navig.commands.cron import _unwrap
from navig.gateway_client import unwrap_envelope


def _envelope(payload):
    """Exactly what routes/common.envelope_ok produces."""
    return {"ok": True, "data": payload, "error": None}


class TestUnwrapEnvelope(unittest.TestCase):
    def test_returns_the_payload_from_an_envelope(self):
        self.assertEqual(unwrap_envelope(_envelope({"jobs": [1, 2]})), {"jobs": [1, 2]})

    def test_bare_payload_passes_through(self):
        """A route that returns a raw dict (not every route uses json_ok) still works."""
        self.assertEqual(unwrap_envelope({"jobs": [1]}), {"jobs": [1]})

    def test_bare_list_passes_through(self):
        self.assertEqual(unwrap_envelope([1, 2]), [1, 2])

    def test_none_payload_becomes_empty_dict_so_get_chains_survive(self):
        self.assertEqual(unwrap_envelope(_envelope(None)), {})

    def test_lookalike_is_not_unwrapped(self):
        """Only the full three-key signature counts — a payload that merely has an `ok`
        field must never be mistaken for an envelope."""
        payload = {"ok": True, "jobs": [1]}
        self.assertEqual(unwrap_envelope(payload), payload)
        self.assertEqual(unwrap_envelope({"data": {"x": 1}}), {"data": {"x": 1}})

    def test_non_dict_inputs_pass_through(self):
        for v in (None, 7, "str"):
            self.assertEqual(unwrap_envelope(v), v)

    def test_cron_uses_the_shared_helper(self):
        self.assertEqual(_unwrap(_envelope({"jobs": []})), {"jobs": []})


class TestEachBrokenCronReadNowWorks(unittest.TestCase):
    """The exact expression each command runs, against the exact envelope its route returns."""

    def test_cron_list_finds_jobs(self):
        body = _envelope({"jobs": [{"id": "j1", "name": "nightly", "enabled": True}]})
        self.assertEqual(len(_unwrap(body).get("jobs", [])), 1)
        self.assertEqual(body.get("jobs", []), [], "pre-fix expression: always empty")

    def test_cron_add_reports_the_real_job_id(self):
        body = _envelope({"id": "j42", "next_run": "2026-01-01T00:00:00Z"})
        self.assertEqual(_unwrap(body).get("id"), "j42")
        self.assertIsNone(body.get("id"), "pre-fix expression: 'Created job: None'")

    def test_cron_run_does_not_invert_a_successful_run(self):
        """The worst of the four — a successful run was reported as a failure."""
        body = _envelope({"success": True, "output": "done"})
        self.assertTrue(_unwrap(body).get("success"))
        self.assertFalse(body.get("success"), "pre-fix expression: always 'Job failed'")

    def test_cron_status_sees_the_cron_section(self):
        body = _envelope({"cron": {"jobs": 3, "enabled_jobs": 2}, "uptime": 10})
        self.assertEqual(_unwrap(body).get("cron", {}).get("jobs"), 3)
        self.assertEqual(body.get("cron", {}), {}, "pre-fix expression: blank status")


class TestRoutesReallyUseTheEnvelope(unittest.TestCase):
    """Source guard: if a cron route ever stopped using json_ok, these fixes would be wrong.
    Pins the premise the whole fix rests on."""

    def test_cron_routes_answer_with_json_ok(self):
        import inspect

        from navig.gateway.routes import cron as cron_routes

        src = inspect.getsource(cron_routes)
        self.assertIn('json_ok({"jobs"', src.replace("'", '"'))
        self.assertIn("json_ok(job.to_dict())", src)
