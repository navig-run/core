"""Replay a stored Webflow headless — the path a NAVIG goal (or a terminal) takes.

:func:`run_headless` launches a fresh browser through navig-browser, replays the document
with :class:`navig_browser.webflow_runner.WebflowRunner` and stops the browser again. A run
is ``verified`` only when every step passed AND a check proved the outcome;
:func:`exit_code` turns that into the process status a goal subtask is judged by
(0 = verified, nothing else).

Three hosts live here:

**Vault** — a ``secret`` param with a ``vault`` reference fills only when
``allow_vault=True`` — just in time, for the page the field is on (inside the flow's
origins, https, same registrable domain; audited as ``<actor>:webflow:<id>``). The value
never leaves the runner process.

**Repair (Phase D)** — ``repair=True`` lets a failed *acting* step (click / fill / select)
be repaired from a fresh observation of the page (``navig_browser.webflow_repair``): a
provable identity drift or a recorded position, re-matched with the runner's own rules.
Every repair is an incident (``webflow.locator_repair``) and, when the flow is stored, a
NEW revision — nothing is overwritten, and origins / urls / values never change. Checks
(``assert`` / ``wait_for``) are never repaired: a repaired check would certify what the
page no longer shows.

**Checkpoints and resume (Phase E)** — after every step the run writes a sanitized
checkpoint (``<config>/webflows/<id>/runs/<run_id>.json``, :mod:`navig.webflows.runs`);
``resume=<run_id>`` continues it: completed steps are SKIPPED (named in the report), the
browser opens the page the interrupted run left off at, and the same checkpoint finishes
with the run's real status. A run that needs the person (a secret nobody may fill, a vault
login that does not exist) reports ``needs_user`` — exit code 4 — and sends the pause
notification (:mod:`navig.webflows.notify`) instead of pretending it finished or failed
outright. Resume eligibility: :func:`navig.webflows.runs.resume_plan`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from navig.webflows.schema import WebflowError, origin_of, resolve_params

logger = logging.getLogger(__name__)

EXIT = {"verified": 0, "failed": 1, "completed": 3, "needs_user": 4, "stopped": 130}
#: Failure categories that mean "the person has to act" — the run pauses, it does not fail.
NEEDS_HUMAN = ("no_credential", "vault_locked", "needs_user", "challenge")


def exit_code(status: str) -> int:
    """0 only for ``verified``: a run with no check (``completed``) did not prove the goal."""
    return EXIT.get(status, 1)


def vault_backed(doc: dict[str, Any]) -> set[str]:
    return {p["name"] for p in doc.get("params", []) if p.get("type") == "secret" and p.get("vault")}


def resolve_run_values(doc: dict[str, Any], given: dict[str, Any], *, allow_vault: bool) -> tuple[dict[str, Any], set[str]]:
    """(values, params filled from the vault). Raises :class:`WebflowError` on a missing value."""
    from_vault = {n for n in vault_backed(doc) if allow_vault and not given.get(n)}
    trimmed = {**doc, "params": [p for p in doc.get("params", []) if p["name"] not in from_vault]}
    return resolve_params(trimmed, given), from_vault


def secret_provider(doc: dict[str, Any], *, actor: str):
    """The just-in-time vault read for one fill step (see the module docstring)."""
    from navig.browser.origin_match import credential_matches
    from navig.browser.webflow_runner import StepFailed

    specs = {p["name"]: p for p in doc.get("params", [])}

    async def provide(param: str, page_url: str) -> str:
        ref = (specs.get(param) or {}).get("vault")
        if not ref:
            raise StepFailed("denied", f"{param} is not a vault-backed secret of this flow")
        if origin_of(page_url) not in doc["origins"] or not credential_matches(page_url, ref["domain"]):
            raise StepFailed("denied", f"the vault login for {ref['domain']} cannot be used on this page (https and the same site only)")

        def read():
            from navig.vault import resolve_login
            from navig.vault.core import acting_as

            with acting_as(f"{actor}:webflow:{doc.get('id', '?')}"):
                return resolve_login(ref["domain"], ref["username"])

        try:
            login, status = await asyncio.to_thread(read)
        except Exception as exc:  # noqa: BLE001 — locked / unreadable vault: release nothing
            raise StepFailed("vault_locked", f"the NAVIG vault could not be read ({type(exc).__name__})") from None
        if login is None or status != "ok" or not login.password:
            raise StepFailed("no_credential", f"no vault login {ref['username']} for {ref['domain']}")
        logger.info("webflow %s: vault login for %s used by %s", doc.get("id"), ref["domain"], actor)
        return login.password

    return provide


def apply_repairs(doc: dict[str, Any], repairs: list[dict[str, Any]]) -> dict[str, Any]:
    """A copy of *doc* with each repaired step's locator replaced — provenance says repair.

    Only locator keys move (same contract as the runner's repair): origins, start_url,
    params, values, expects and every other behavioural field are carried through untouched,
    and the already-failed fallbacks of a repaired step are dropped (the new locator is
    verified, the stale ones are not).
    """
    from navig.browser.webflow_repair import REPAIRABLE

    steps = [dict(s) for s in doc["steps"]]
    for r in repairs:
        idx = r.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(steps):
            continue
        step = steps[idx]
        for key in [k for k in step if k in REPAIRABLE]:
            step.pop(key)
        step.update(r.get("to") or {})
        step.pop("fallbacks", None)
    out = {**doc, "steps": steps}
    prov = dict(out.get("provenance") or {})
    prov.update({"source": "repair", "recorder": "navig webflow run"})
    out["provenance"] = prov
    return out


def _needs_human_reason(doc: dict[str, Any], failed: dict[str, Any]) -> str:
    """A secret-free explanation for the pause notification, from the failed step."""
    category, step_id = failed.get("category"), str(failed.get("id", "?"))
    if category == "challenge":
        label = failed.get("challenge") or "a human check"
        return (f"the site is asking for a {label} — do it in the Dock or the browser, "
                "then resume")
    if category == "vault_locked":
        return "the NAVIG vault could be read only as locked"
    spec = next((p for p in doc.get("params", []) if p.get("type") == "secret" and p.get("vault")), None)
    if category == "no_credential" and spec:
        ref = spec["vault"]
        return (f"the vault has no login for {ref['domain']} — "
                f"add it: navig vault login add {ref['domain']} -u {ref['username']}")
    return f"step {step_id} needs you"


def _flow_dir(doc: dict[str, Any]):
    from navig.webflows.store import WebflowStore

    if not doc.get("id"):
        return None
    return WebflowStore()._dir(doc["id"])  # id validation lives there


async def run_headless(
    doc: dict[str, Any],
    given: dict[str, Any] | None = None,
    *,
    allow_vault: bool = False,
    headless: bool = True,
    actor: str = "cli",
    ignore_https_errors: bool = False,
    on_step=None,
    repair: bool = False,
    max_repairs: int = 3,
    resume: str | None = None,
    checkpoint: bool = True,
    run_id: str | None = None,
    secret_provider_override: Any = None,
) -> dict[str, Any]:
    """Replay *doc* (already validated) in a fresh browser; returns the run report dict.

    ``resume`` continues a checkpointed run; ``repair`` opts in to bounded locator repair —
    both leave evidence in the report. ``checkpoint=False`` writes no run-state files.
    """
    from navig.browser.controller import BrowserConfig, BrowserController
    from navig.browser.webflow_runner import WebflowRunner
    from navig.webflows.runs import load as load_cp
    from navig.webflows.runs import new_run_id, resume_plan
    from navig.webflows.runs import write as write_cp

    flow_dir = _flow_dir(doc) if checkpoint else None
    if checkpoint and flow_dir is None:
        checkpoint = False  # a flow not saved yet has nowhere to keep a checkpoint

    run_id, start_index, start_url = run_id or new_run_id(), 0, None
    if resume:
        if not checkpoint:
            raise WebflowError(["this flow has no store to resume from"])
        cp = load_cp(flow_dir, resume)
        plan = resume_plan(cp, doc) if cp else None
        if plan is None:
            raise WebflowError([f"run {resume} cannot be resumed (unknown, terminal, or its page is outside this flow's sites)"])
        if cp.get("revision") and doc.get("revision") and cp["revision"] != doc["revision"]:
            # Continuing on a DIFFERENT revision would run the old next_index against a
            # step list that moved — a wrong click waiting to happen. Align or refuse.
            raise WebflowError([f"run {resume} started on revision {cp['revision']} of this flow — "
                                f"resume it on that one (--revision {cp['revision']}), or run fresh"])
        run_id, (start_index, start_url) = resume, plan

    # Resolved AFTER the resume plan: params belong to the revision the run continues.
    values, from_vault = resolve_run_values(doc, given or {}, allow_vault=allow_vault)
    provider = secret_provider_override or (secret_provider(doc, actor=actor) if from_vault else None)

    executed: list[Any] = []
    controller: Any = None

    def _last_url() -> str:
        pg = getattr(controller, "page", None)
        return str(getattr(pg, "url", "") or "")

    def _cp_payload(status: str) -> dict[str, Any]:
        # next_index = the first step NOT completed: the step after the last OK one. A
        # FAILED step stays the next one to run — resuming must retry it, not skip it.
        done = [r for r in executed if r.ok]
        return {
            "run_id": run_id, "flow_id": doc.get("id"), "revision": doc.get("revision"),
            "next_index": (done[-1].index + 1) if done else start_index,
            "status": status, "last_url": _last_url(),
            "steps": [dict(index=r.index, id=r.id, action=r.action, ok=r.ok, ms=r.ms,
                           category=r.category) for r in executed],
        }

    def _write(status: str) -> None:
        if not checkpoint:
            return
        try:
            write_cp(flow_dir, _cp_payload(status))
        except Exception:  # noqa: BLE001 — a checkpoint problem must not kill the run
            logger.warning("webflow checkpoint write failed", exc_info=True)

    def _callback(res):
        # The checkpoint lands BEFORE the caller sees the step: an interruption after it
        # never re-runs what is already recorded (resume skips exactly these steps).
        executed.append(res)
        _write("running")
        if res.repaired:
            from navig.core.incidents import record

            record("webflow.locator_repair", flow_id=doc.get("id"), revision=doc.get("revision"),
                   step=res.id, action=res.action, run_id=run_id, actor=actor,
                   **{k: res.repaired[k] for k in ("from", "to", "why")})
        if on_step is not None:
            return on_step(res)
        return None

    controller = BrowserController(BrowserConfig(headless=headless, ignore_https_errors=ignore_https_errors))
    try:
        await controller.start()
    except Exception as exc:  # noqa: BLE001 — name the capability gap, never a traceback
        first = (str(exc).splitlines() or [type(exc).__name__])[0]
        raise WebflowError([f"no browser to run this flow on — install it once: "
                            f"python -m playwright install chromium ({first})"]) from exc
    report_dict: dict[str, Any] | None = None
    try:
        report = await WebflowRunner(controller).run(
            doc, values, on_step=_callback, vault_params=from_vault, secret_provider=provider,
            repair=repair, max_repairs=max_repairs, start_index=start_index, start_url=start_url,
        )
        # §9: a step that found nothing may really be standing in front of a challenge.
        # Classify it HERE, on the step OBJECTS (the page is still open): the checkpoint
        # is written from those objects, and mutating only the report copy left the two
        # disagreeing (checkpoint "not_found", report "challenge").
        if report.status == "failed":
            bad = next((st for st in report.steps if not st.ok and st.index >= 0), None)
            if bad is not None and bad.category in ("not_found", "ambiguous", "timeout", "page"):
                from navig.browser.webflow_repair import detect_challenge

                label = await detect_challenge(controller.page)
                if label:
                    bad.category = "challenge"
                    bad.challenge = label
                    bad.error = f"{bad.error or 'the step failed'} — the site shows {label}"
        report_dict = report.to_dict()
    except (KeyboardInterrupt, asyncio.CancelledError):  # process death: keep the evidence
        _write("interrupted")
        raise
    except WebflowError:
        raise  # our own refusals (resume guard etc.) pass through untouched
    except Exception as exc:  # noqa: BLE001 — an unreadable start page / engine escape
        first = (str(exc).splitlines() or [type(exc).__name__])[0]
        raise WebflowError([f"the run could not continue: {first}"]) from exc
    finally:
        await controller.stop()

    out = report_dict  # the try above re-raises on interruption, so this is always a dict

    # A repair that worked becomes a NEW revision of the stored flow (never in place).
    if repair and out.get("repairs") and doc.get("id"):
        try:
            from navig.webflows.store import WebflowStore

            saved = WebflowStore().save(apply_repairs(doc, out["repairs"]), wf_id=doc["id"])
            out["saved_revision"] = saved["revision"]
        except Exception as exc:  # noqa: BLE001 — the run stays valid evidence either way
            logger.warning("saving the repaired flow failed: %s", exc)
            out["saved_revision"] = None

    # The person has to act: pause (exit 4 + one notification), never pretend.
    failed = next((s for s in out["steps"] if s.get("ok") is False and s.get("index", -1) >= 0), None)
    if out["status"] == "failed" and failed and failed.get("category") in NEEDS_HUMAN:
        out["status"] = "needs_user"
        _write("needs_user")
        from navig.webflows.notify import notify_needs_user

        reason = _needs_human_reason(doc, failed)
        await notify_needs_user(flow_id=doc.get("id") or "unsaved",
                                flow_name=str(doc.get("name") or doc.get("id")),
                                reason=reason, run_id=run_id)
    else:
        _write(out["status"])

    out["run_id"] = run_id
    if resume:
        skipped = sum(1 for st in out["steps"] if st.get("category") == "skipped")
        out["resumed"] = {"run_id": resume, "skipped": skipped, "from": start_url}
    return out


__all__ = [
    "EXIT",
    "NEEDS_HUMAN",
    "WebflowError",
    "apply_repairs",
    "exit_code",
    "resolve_run_values",
    "run_headless",
    "secret_provider",
    "vault_backed",
]
