"""The ONE deck-side authorization adapter: identity, classification, policy, lock, audit.

Every deck route that executes something (shell, docker mutation, config change) routes
through :func:`authorize_deck` instead of hand-rolling its own checks. It composes the
existing pieces — never reimplements them:

* **identity** — server-derived from the deck auth middleware's request keys
  (``deck_user_id`` / ``deck_auth_local`` / ``deck_brain_device``). The client-settable
  ``X-Actor`` header is deliberately ignored on deck paths.
* **classification** — :meth:`ApprovalPolicy.classify_command` maps a shell command to
  ``never | dangerous | safe | confirm``; :func:`action_for_shell` turns that into the
  PolicyGate slug vocabulary (``<prefix>.never`` / ``<prefix>.dangerous`` / ``<prefix>``).
  This is the single mapping table between the command-classification vocabulary and the
  policy-slug vocabulary; ``console_helper``'s display levels and the ApprovalGate's tool
  vocabulary are intentionally NOT merged into it.
* **policy + approval + audit** — ``NavigGateway.policy_check`` (the one composite:
  PolicyGate → cooldown → ApprovalManager fail-closed → AuditLog → billing).
* **host lock** — :func:`navig.core.host_lock.try_guard_remote`, the non-raising sibling
  of ``guard_remote`` for HTTP callers.

Order: policy first, then lock — a policy DENY must not claim a lock. :func:`authorize_deck`
returns ``None`` when the caller may proceed, or an ready-made ``web.Response`` (403/409/429/503)
to return unchanged.

:func:`authorize_deck_deferred` is the same decision for routes a person clicks in the deck:
an approval wait answers **202 pending** with the request id at once (the OS client gives up
after 15 s, while a wait may last the approval policy's 15 minutes), the decision and the
action run in the background, and ``GET /api/deck/approvals/{id}`` reports the outcome.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)


def deck_actor(request: "web.Request") -> str:
    """Who is asking, for policy and audit: a linked machine, a deck user, or local.

    Server-derived only — the ``X-Actor`` header is never consulted here, so a remote
    caller cannot choose the identity recorded against its own action.
    """
    device = request.get("deck_brain_device")
    if device:
        return f"brain-device:{device}"
    if request.get("deck_auth_local"):
        return "deck:local"
    user_id = request.get("deck_user_id")
    return f"deck:{user_id}" if user_id else "deck:unknown"


def audit_log_for(request: "web.Request"):
    """The running gateway's audit log, or a fresh handle on the same file."""
    gateway = request.app.get("gateway") if hasattr(request.app, "get") else None
    log = getattr(gateway, "audit_log", None)
    if log is None:
        from navig.gateway.audit_log import AuditLog

        log = AuditLog()
    return log


def action_for_shell(
    command: str,
    prefix: str,
    *,
    gw=None,
) -> tuple[str, str]:
    """Map a shell command to ``(policy_slug, level)`` under *prefix*.

    Levels come from the live ``ApprovalPolicy`` (the same instance the deck console
    and the approval manager use, falling back to a default construction). The slug is
    what PolicyGate rules match on:

    ======== ==========================================================
    level    slug
    ======== ==========================================================
    never    ``<prefix>.never``   (hard-denied by PolicyGate baseline)
    dangerous ``<prefix>.dangerous`` (baseline REQUIRE_APPROVAL)
    safe     ``<prefix>``
    confirm  ``<prefix>``
    ======== ==========================================================
    """
    level = "confirm"
    policy = None
    am = getattr(gw, "approval_manager", None) if gw else None
    if am is not None:
        policy = getattr(am, "policy", None)
    if policy is None:
        try:
            from navig.approval.policies import ApprovalPolicy

            policy = ApprovalPolicy()
        except Exception:  # noqa: BLE001 — classification must never crash a route
            policy = None
    if policy is not None:
        try:
            lv = policy.classify_command(command)
            level = lv.value if hasattr(lv, "value") else str(lv)
        except Exception:  # noqa: BLE001
            level = "confirm"
    if level == "never":
        return f"{prefix}.never", level
    if level == "dangerous":
        return f"{prefix}.dangerous", level
    return prefix, level


async def authorize_deck(
    request: "web.Request",
    action: str,
    *,
    raw_input: str = "",
    host: str | None = None,
    command: str | None = None,
    on_pending: Callable[[str], None] | None = None,
) -> "web.Response | None":
    """Authorize a deck-side action. ``None`` = proceed; a Response = return it unchanged.

    *action* is a PolicyGate slug (see :func:`action_for_shell`). *host*, when given,
    additionally takes the host lock for this session (409 when another session holds
    it). *raw_input* reaches the audit log hashed, never stored.
    """
    gateway = request.app.get("gateway") if hasattr(request.app, "get") else None
    gw = getattr(gateway, "policy_check", None) if gateway else None
    if gw is None:
        # The deck routes only exist while a gateway serves them; if that wiring is
        # broken, fail closed with an actionable error rather than executing ungated.
        return web.json_response(
            {
                "ok": False,
                "error": "authorization unavailable (gateway not wired)",
                "error_code": "auth_unavailable",
            },
            status=503,
        )

    actor = deck_actor(request)
    extra: dict[str, Any] = {}
    if on_pending is not None:
        # What the approver sees: the actual command, not only the policy slug.
        what = (command or raw_input or "").strip()
        extra = {
            "on_pending": on_pending,
            "description": f"{action} on {host}: {what}"[:240] if host else f"{action}: {what}"[:240],
        }
    block = await gateway.policy_check(action, actor, raw_input=raw_input, **extra)
    if block is not None:
        return block

    if host:
        try:
            from navig.config import get_config_manager
            from navig.core.host_lock import describe, try_guard_remote
        except Exception as exc:  # noqa: BLE001 — cannot verify the lock ⇒ do not execute
            logger.exception("host lock unavailable for %r", host)
            return web.json_response(
                {
                    "ok": False,
                    "error": f"host lock unavailable: {exc}",
                    "error_code": "lock_unavailable",
                },
                status=503,
            )
        conflict = try_guard_remote(get_config_manager(), host, command)
        if conflict is not None:
            return web.json_response(
                {
                    "ok": False,
                    "error": f"host '{host}' is locked by another session",
                    "error_code": "host_locked",
                    "detail": describe(conflict, host),
                },
                status=409,
            )
    return None


# ── Deferred authorization: answer "pending" instead of holding the request ──

#: request id → job. In memory, like the deploy state: a restart forgets pending jobs,
#: and the approval they waited on dies with the process too (fail closed).
_APPROVAL_JOBS: dict[str, dict[str, Any]] = {}
_JOB_TTL_S = 3600.0
_JOB_MAX = 200


def _prune_jobs(now: float) -> None:
    finished = [
        (job["updated"], rid) for rid, job in _APPROVAL_JOBS.items()
        if job["status"] not in ("pending_approval", "running")
    ]
    for updated, rid in finished:
        if now - updated > _JOB_TTL_S:
            _APPROVAL_JOBS.pop(rid, None)
    if len(_APPROVAL_JOBS) > _JOB_MAX:
        for _updated, rid in sorted(finished)[: len(_APPROVAL_JOBS) - _JOB_MAX]:
            _APPROVAL_JOBS.pop(rid, None)


def _body(resp: "web.Response") -> dict[str, Any]:
    try:
        return json.loads(resp.body) if resp.body else {}
    except (TypeError, ValueError):
        return {}


def _approval_timeout(request: "web.Request") -> float | None:
    gateway = request.app.get("gateway") if hasattr(request.app, "get") else None
    policy = getattr(getattr(gateway, "approval_manager", None), "policy", None)
    timeout = getattr(policy, "timeout_seconds", None)
    return float(timeout) if isinstance(timeout, (int, float)) else None


async def authorize_deck_deferred(
    request: "web.Request",
    action: str,
    run: Callable[[], Awaitable["web.Response"]],
    *,
    raw_input: str = "",
    host: str | None = None,
    command: str | None = None,
) -> "web.Response":
    """Authorize, then ``run()`` — without holding the request open for an approval.

    Every decision that is immediate keeps today's exact response: a policy DENY, no
    approval manager (403, fail closed), a cooldown (429), a held host lock (409), or
    an ALLOW (``run()``'s own 200). Only when an approval request becomes PENDING does
    this answer **202** ``{"status": "pending_approval", "request_id": …}`` straight
    away; the decision, the host-lock check (taken when it is approved, not when it was
    asked for) and ``run()`` continue in the background, and
    ``GET /api/deck/approvals/{request_id}`` returns ``pending_approval`` → ``running``
    → ``done`` (with the result) / ``denied`` / ``expired`` / ``failed``.
    """
    loop = asyncio.get_running_loop()
    created: asyncio.Future[str] = loop.create_future()

    def _on_pending(request_id: str) -> None:
        if not created.done():
            created.set_result(request_id)

    async def _decide_and_run() -> tuple[str, "web.Response"]:
        block = await authorize_deck(
            request, action, raw_input=raw_input, host=host, command=command,
            on_pending=_on_pending,
        )
        if block is not None:
            return "blocked", block
        rid = created.result() if created.done() else None
        if rid is not None and rid in _APPROVAL_JOBS:
            _APPROVAL_JOBS[rid].update(status="running", updated=time.time())
        return "ran", await run()

    from navig.core.background import spawn

    task = spawn(_decide_and_run(), name=f"deck-approval:{action}")
    done, _pending = await asyncio.wait({task, created}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        _kind, response = task.result()  # an exception here surfaces exactly as before
        return response

    request_id = created.result()
    started = time.time()
    actor = deck_actor(request)
    _prune_jobs(started)
    _APPROVAL_JOBS[request_id] = {
        "request_id": request_id, "action": action, "host": host, "actor": actor,
        "status": "pending_approval", "created": started, "updated": started,
        "result": None, "error": None,
    }
    timeout = _approval_timeout(request)

    def _finish(t: "asyncio.Task[tuple[str, web.Response]]") -> None:
        job = _APPROVAL_JOBS.get(request_id)
        if job is None:
            return
        job["updated"] = time.time()
        if t.cancelled():
            job.update(status="failed", error="cancelled")
            return
        exc = t.exception()
        if exc is not None:
            job.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            return
        kind, resp = t.result()
        body = _body(resp)
        if kind == "ran":
            job.update(status="done", result=body.get("data", body), http_status=resp.status)
            return
        code = body.get("error_code")
        if code == "approval_denied":
            waited = job["updated"] - started
            expired = timeout is not None and waited >= timeout - 1.0
            job.update(status="expired" if expired else "denied", error=body.get("error"))
        else:
            job.update(status="failed", error=body.get("error"), error_code=code,
                       detail=body.get("detail"), http_status=resp.status)

    task.add_done_callback(_finish)
    logger.info("deck %s by %s is waiting for approval %s", action, actor, request_id)
    return web.json_response(
        {
            "ok": True,
            "data": {
                "status": "pending_approval",
                "request_id": request_id,
                "action": action,
                "status_url": f"/api/deck/approvals/{request_id}",
            },
        },
        status=202,
    )


async def handle_deck_approval_status(request: "web.Request") -> "web.Response":
    """GET /api/deck/approvals/{request_id} — the outcome of a deferred deck action.

    Only the actor who asked can read it: the result can carry command output.
    """
    request_id = request.match_info.get("request_id", "")
    job = _APPROVAL_JOBS.get(request_id)
    if job is None or job.get("actor") != deck_actor(request):
        return web.json_response(
            {"ok": False, "error": "no such approval job", "error_code": "not_found"}, status=404,
        )
    public = {k: v for k, v in job.items() if k != "actor"}
    return web.json_response({"ok": True, "data": public})
