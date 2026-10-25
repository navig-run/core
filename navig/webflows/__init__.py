"""Webflows — browser procedures learned from a demonstration and replayed without a model.

* :mod:`navig.webflows.schema` — the ``navig.webflow/1`` document and its validation /
  sanitizing rules (shared step vocabulary with :mod:`navig.agent.browser_turn`).
* :mod:`navig.webflows.store` — ``<config_dir>/webflows/<id>/r<N>.json``, one immutable
  file per revision.

Recorder and the in-browser replay live in the NAVIG Dock extension
(``apps/dock/extension/background/webflows.js``); the headless replay lives in
navig-browser (``navig_browser.webflow_runner``); the deck routes are
``navig.gateway.deck.routes.webflows``.
"""

from __future__ import annotations

from navig.webflows.schema import (
    SCHEMA,
    STEP_ACTIONS,
    Validated,
    WebflowError,
    dumps,
    loads,
    origin_of,
    require_valid,
    resolve_params,
    substitute,
    validate_webflow,
)
from navig.webflows.store import WebflowStore, WebflowSummary, webflows_dir

__all__ = [
    "SCHEMA",
    "STEP_ACTIONS",
    "Validated",
    "WebflowError",
    "WebflowStore",
    "WebflowSummary",
    "dumps",
    "loads",
    "origin_of",
    "require_valid",
    "resolve_params",
    "substitute",
    "validate_webflow",
    "webflows_dir",
]
