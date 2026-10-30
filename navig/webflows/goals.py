"""A NAVIG agent goal that runs a Webflow — discovery, selection and the subtask it becomes.

``webflow_subtask({"id"|"match", "params", "allow_vault"})`` resolves the flow in this host's
store (an exact id, or the one flow whose name / sites / description contain every word of
``match``), checks the params against the flow (declared, never a secret), and returns the
subtask ``{"description", "command"}`` for :meth:`navig.agent.goals.GoalPlanner.decompose_goal`.

The command is ``navig webflow run <id> -p name=value … --json``, every part shell-quoted.
The agent's goal loop judges it by its exit code, and ``navig webflow run`` exits 0 only
when the flow is *verified* — a check proved the outcome on the page — so the goal cannot be
reported complete on a run that merely executed its steps.

A miss raises :class:`WebflowGoalError` carrying the candidate flows, so the caller (an agent
over MCP) can pick one instead of guessing.
"""

from __future__ import annotations

import shlex
from typing import Any

from navig.webflows.schema import WebflowError


class WebflowGoalError(WebflowError):
    def __init__(self, errors: list[str], candidates: list[dict[str, Any]] | None = None):
        super().__init__(errors)
        self.candidates = candidates or []


def _candidates(store, rows) -> list[dict[str, Any]]:
    out = []
    for r in rows[:20]:
        doc = store.get(r.id) or {}
        out.append({
            "id": r.id, "name": r.name, "sites": list(r.origins), "revision": r.revision,
            "description": doc.get("description", ""),
            "params": [{"name": p["name"], "type": p["type"], "required": p.get("required", True) and "default" not in p,
                        **({"vault": True} if p.get("vault") else {})} for p in doc.get("params", [])],
            "verifiable": any(s["action"] == "assert" for s in doc.get("steps", [])),
        })
    return out


def find_webflows(match: str, store=None) -> list[dict[str, Any]]:
    """Flows whose name, sites or description contain every word of *match* (all when empty)."""
    from navig.webflows.store import WebflowStore

    store = store or WebflowStore()
    words = [w for w in str(match or "").lower().split() if w]
    rows = []
    for r in store.list():
        doc = store.get(r.id) or {}
        hay = f"{r.name} {' '.join(r.origins)} {doc.get('description', '')}".lower()
        if all(w in hay for w in words):
            rows.append(r)
    return _candidates(store, rows)


def webflow_subtask(spec: Any, store=None) -> dict[str, str]:
    """The goal subtask that runs (and verifies) the flow *spec* names. Raises on any doubt."""
    from navig.webflows.store import WebflowStore

    store = store or WebflowStore()
    if not isinstance(spec, dict):
        raise WebflowGoalError(["webflow must be an object: {id | match, params?, allow_vault?}"])
    wf_id, match = str(spec.get("id") or "").strip(), str(spec.get("match") or "").strip()
    if wf_id:
        try:
            doc = store.get(wf_id)
        except WebflowError:
            doc = None
        if doc is None:
            raise WebflowGoalError([f"no webflow {wf_id!r}"], find_webflows("", store))
    elif match:
        found = find_webflows(match, store)
        if len(found) != 1:
            why = "no webflow matches" if not found else f"{len(found)} webflows match"
            raise WebflowGoalError([f"{why} {match!r} — name one by id"], found or find_webflows("", store))
        doc = store.get(found[0]["id"])
    else:
        raise WebflowGoalError(["webflow needs an id or a match"], find_webflows("", store))

    params = spec.get("params") or {}
    if not isinstance(params, dict):
        raise WebflowGoalError(["webflow params must be an object of name: value"])
    declared = {p["name"]: p for p in doc["params"]}
    errors = []
    for name, value in params.items():
        if name not in declared:
            errors.append(f"{name!r} is not a parameter of {doc['id']}")
        elif declared[name]["type"] == "secret":
            errors.append(f"{name!r} is a secret — it is never passed to a goal (vault-backed secrets use allow_vault)")
        elif not isinstance(value, (str, int, float, bool)):
            errors.append(f"{name!r} must be a plain value")
    if errors:
        raise WebflowGoalError(errors)

    argv = ["navig", "webflow", "run", doc["id"]]
    for name, value in params.items():
        argv += ["-p", f"{name}={str(value).lower() if isinstance(value, bool) else value}"]
    if spec.get("allow_vault") is True:
        argv.append("--allow-vault")
    argv.append("--json")
    verifiable = any(s["action"] == "assert" for s in doc["steps"])
    return {
        "description": f"Run webflow {doc['name']!r} (r{doc.get('revision')}) and verify its outcome"
        + ("" if verifiable else " — WARNING: it has no check, so it can never verify"),
        "command": " ".join(shlex.quote(a) for a in argv),
    }
