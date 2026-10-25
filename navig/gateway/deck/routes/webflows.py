"""
Deck Webflow endpoints — the store the Dock's recorder saves into and every replayer reads.

GET    /api/deck/webflows                    → {"ok", "webflows": [summary…]}
POST   /api/deck/webflows                    {webflow} → {"ok", "webflow"}            (new flow)
GET    /api/deck/webflows/{id}?revision=N    → {"ok", "webflow", "revisions": [1, 2…]}
PUT    /api/deck/webflows/{id}               {webflow} → {"ok", "webflow"}            (next revision)
DELETE /api/deck/webflows/{id}               → {"ok"}
GET    /api/deck/webflows/{id}/export        → the canonical navig.webflow/1 JSON (a download)
POST   /api/deck/webflows/{id}/secret        {param, page_url, revision?} → {"ok", "value"}
                                             the vaulted login a secret param names, for the
                                             Dock's fill step only (see handle_deck_webflow_secret)

Every write is validated and sanitized by :func:`navig.webflows.validate_webflow` — a
document carrying a secret, a step outside its origins or an unknown verb is a 400 with
every reason, and nothing is written. Store calls run in a worker thread (disk I/O).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _err(msg: str, status: int = 500, **extra: Any) -> "web.Response":
    return web.json_response({"ok": False, "error": msg, **extra}, status=status)


def _store():
    from navig.webflows import WebflowStore

    return WebflowStore()


async def _body(request: "web.Request") -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return body if isinstance(body, dict) else None


async def handle_deck_webflows(request: "web.Request") -> "web.Response":
    try:
        rows = await asyncio.to_thread(lambda: _store().list())
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck webflow list failed")
        return _err(str(exc))
    return web.json_response({"ok": True, "webflows": [r.to_dict() for r in rows]})


async def _save(request: "web.Request", wf_id: str | None) -> "web.Response":
    from navig.webflows import WebflowError

    body = await _body(request)
    if body is None:
        return _err("invalid JSON body", status=400)
    if wf_id is None:
        body.pop("id", None)  # POST always creates; an imported id is not trusted to overwrite
    else:
        store = _store()
        if not await asyncio.to_thread(lambda: store.revisions(wf_id)):
            return _err(f"no webflow {wf_id!r}", status=404)
    try:
        doc = await asyncio.to_thread(lambda: _store().save(body, wf_id=wf_id))
    except WebflowError as exc:
        return _err("this webflow cannot be saved", status=400, errors=exc.errors)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck webflow save failed")
        return _err(str(exc))
    return web.json_response({"ok": True, "webflow": doc})


async def handle_deck_webflow_create(request: "web.Request") -> "web.Response":
    return await _save(request, None)


async def handle_deck_webflow_update(request: "web.Request") -> "web.Response":
    from navig.webflows import WebflowError

    wf_id = request.match_info.get("wf_id", "")
    try:
        _store()._dir(wf_id)
    except WebflowError as exc:
        return _err(str(exc), status=400)
    return await _save(request, wf_id)


async def handle_deck_webflow_get(request: "web.Request") -> "web.Response":
    from navig.webflows import WebflowError

    wf_id = request.match_info.get("wf_id", "")
    rev_raw = request.query.get("revision")
    try:
        revision = int(rev_raw) if rev_raw else None
    except ValueError:
        return _err("revision must be a number", status=400)
    store = _store()
    try:
        doc = await asyncio.to_thread(lambda: store.get(wf_id, revision))
        revs = await asyncio.to_thread(lambda: store.revisions(wf_id))
    except WebflowError as exc:
        return _err(str(exc), status=400)
    if doc is None:
        return _err(f"no webflow {wf_id!r}" + (f" revision {revision}" if revision else ""), status=404)
    return web.json_response({"ok": True, "webflow": doc, "revisions": revs})


async def handle_deck_webflow_delete(request: "web.Request") -> "web.Response":
    from navig.webflows import WebflowError

    wf_id = request.match_info.get("wf_id", "")
    try:
        removed = await asyncio.to_thread(lambda: _store().delete(wf_id))
    except WebflowError as exc:
        return _err(str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deck webflow delete failed")
        return _err(str(exc))
    if not removed:
        return _err(f"no webflow {wf_id!r}", status=404)
    return web.json_response({"ok": True})


async def handle_deck_webflow_export(request: "web.Request") -> "web.Response":
    from navig.webflows import WebflowError

    wf_id = request.match_info.get("wf_id", "")
    try:
        text = await asyncio.to_thread(lambda: _store().export(wf_id))
    except WebflowError as exc:
        return _err(str(exc), status=400)
    if text is None:
        return _err(f"no webflow {wf_id!r}", status=404)
    return web.Response(
        text=text,
        content_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{wf_id}.webflow.json"'},
    )


def _refuse(msg: str, status: int, category: str) -> "web.Response":
    return web.json_response({"ok": False, "error": msg, "category": category}, status=status,
                             headers={"Cache-Control": "no-store"})


async def handle_deck_webflow_secret(request: "web.Request") -> "web.Response":
    """Release the vaulted website password a flow's ``secret`` param names — to one fill step.

    The only path by which a decrypted value leaves the vault for a webflow, so every check
    fails closed (403 ``denied`` / 404 ``no_credential``) and the run stops before typing:

    * local only, and only from the paired NAVIG Dock: the request must carry an extension
      Origin (the deck middleware already refuses an extension that is not the paired one,
      and a website cannot forge one). A caller with no Origin — the OS app, an agent's HTTP
      client — is refused: an agent never receives the value.
    * the param is a ``secret`` of the STORED flow with a ``vault`` reference (a caller
      cannot name an arbitrary login);
    * the page the Dock is about to fill is inside the flow's origins, and the login binds to
      it — https and the same registrable domain (``credential_matches``, the rule autofill
      uses);
    * the vault read is attributed to ``dock:webflow:<id>`` in the vault's audit log.

    The value goes only into the response body (``no-store``); it is never logged.
    """
    from navig.gateway.deck.auth import is_local_request
    from navig.webflows import WebflowError, origin_of

    if not is_local_request(request):
        return _refuse("a vault login is released only on this computer", 403, "denied")
    origin = request.headers.get("Origin", "")
    if not (origin.startswith("chrome-extension://") or origin.startswith("moz-extension://")):
        return _refuse("only the NAVIG Dock can use a vault login for a webflow", 403, "denied")
    wf_id = request.match_info.get("wf_id", "")
    body = await _body(request)
    if body is None:
        return _refuse("invalid JSON body", 400, "invalid")
    param, page_url, rev = str(body.get("param") or ""), str(body.get("page_url") or ""), body.get("revision")
    if rev is not None and (isinstance(rev, bool) or not isinstance(rev, int)):
        return _refuse("revision must be a number", 400, "invalid")
    try:
        doc = await asyncio.to_thread(lambda: _store().get(wf_id, rev))
    except WebflowError as exc:
        return _refuse(str(exc), 400, "invalid")
    if doc is None:
        return _refuse(f"no webflow {wf_id!r}", 404, "not_found")
    spec = next((p for p in doc["params"] if p["name"] == param), None)
    if not spec or spec["type"] != "secret" or not spec.get("vault"):
        return _refuse(f"{param!r} is not a vault-backed secret of this flow", 403, "denied")
    ref = spec["vault"]
    from navig.browser.origin_match import credential_matches

    if origin_of(page_url) not in doc["origins"] or not credential_matches(page_url, ref["domain"]):
        return _refuse(f"the vault login for {ref['domain']} cannot be used on this page (https and the same site only)",
                       403, "denied")

    def read():
        from navig.vault import resolve_login
        from navig.vault.core import acting_as

        with acting_as(f"dock:webflow:{wf_id}"):
            return resolve_login(ref["domain"], ref["username"])

    try:
        login, status = await asyncio.to_thread(read)
    except Exception as exc:  # noqa: BLE001 — a locked / unreadable vault: say so, release nothing
        logger.warning("webflow %s: vault read failed (%s)", wf_id, type(exc).__name__)
        return _refuse("the NAVIG vault could not be read (is it locked?)", 423, "vault_locked")
    if login is None or status != "ok" or not login.password:
        return _refuse(f"no vault login {ref['username']} for {ref['domain']} — add it with: navig vault login add {ref['domain']} -u {ref['username']}",
                       404, "no_credential")
    logger.info("webflow %s: released the vault login for %s to the Dock (param %s)", wf_id, ref["domain"], param)
    return web.json_response({"ok": True, "value": login.password}, headers={"Cache-Control": "no-store"})

