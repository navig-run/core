"""The Webflow document — ``navig.webflow/1``.

A Webflow is a browser procedure a person demonstrated in the NAVIG Dock (or wrote by
hand): an ordered list of steps over semantic locators, with parameters for every value
that changes between runs and observable postconditions instead of sleeps. Two engines
replay the same document with no model in the loop: the Dock's executor in the user's
own Chrome (``apps/dock/extension/background/webflows.js``) and navig-browser's
Playwright runner (``navig_browser.webflow_runner``).

The step vocabulary is the Dock agent's (:mod:`navig.agent.browser_turn`): each step is
checked by the same ``_validate`` rules the bridge applies to ``dock_act`` batches, so a
recorded flow can never use a verb or locator the executor does not understand.

Everything a recording produces is untrusted input. :func:`validate_webflow` therefore:

* drops unknown fields and rejects unknown verbs;
* requires every ``{{param}}`` to be declared, and a ``secret`` param to appear only as
  the whole value of a ``fill`` step (never a default, never inside a URL);
* refuses a literal value on a sensitive (password / card) step — the recorder never
  sends one, so a document that carries one was not made by it;
* pins replay to ``origins``: ``start_url`` and every ``navigate`` must stay inside them,
  and a URL may not template its own origin;
* runs the core secret patterns (:func:`navig.recall.redact.redact_secrets`) over notes
  and descriptions, and refuses a literal value they would change.

Pure functions only — the store (:mod:`navig.webflows.store`) and the deck routes call in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "navig.webflow/1"

STEP_ACTIONS = ("navigate", "click", "fill", "select", "key", "wait_for", "assert")
PARAM_TYPES = ("string", "secret", "number", "bool")
LOCATOR_KEYS = ("role", "name", "testid", "css", "has_text", "xpath", "nth")

MAX_STEPS = 200
MAX_PARAMS = 30
MAX_ORIGINS = 10
MAX_FALLBACKS = 5
DEFAULT_TIMEOUT_MS = 10_000
MIN_TIMEOUT_MS, MAX_TIMEOUT_MS = 100, 60_000

_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_PARAM_REF = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_WHOLE_REF = re.compile(r"^\{\{\s*([a-z][a-z0-9_]{0,39})\s*\}\}$")
_ID = re.compile(r"^wf_[a-z0-9][a-z0-9-]{0,59}$")
_STEP_ID = re.compile(r"^s[0-9]{1,4}$")


class WebflowError(ValueError):
    """A document that cannot be stored or replayed; ``errors`` lists every reason."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Validated:
    doc: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def origin_of(url: str) -> str | None:
    """``scheme://host[:port]`` of an http(s) URL, lower-cased; ``None`` otherwise."""
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    default = 443 if parts.scheme == "https" else 80
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != default else "")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:56].strip("-")
    return f"wf_{slug or 'flow'}"


def _redact(text: str) -> str:
    from navig.recall.redact import redact_secrets  # noqa: PLC0415 — pulls core patterns

    return redact_secrets(text)


def _text(raw: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit]


def _locator(raw: Any, where: str, errors: list[str], params: dict[str, dict] | None = None) -> dict[str, Any]:
    """The locator keys of *raw*, checked with the Dock agent's own step rules.

    Text keys may name a param (``has_text: "Order confirmed for {{customer}}"``) so an
    outcome check can follow the run's input; a secret never goes into a locator."""
    if not isinstance(raw, dict):
        errors.append(f"{where}: a locator must be an object")
        return {}
    loc = {k: raw[k] for k in LOCATOR_KEYS if k in raw and raw[k] is not None}
    if not any(k in loc for k in LOCATOR_KEYS if k not in ("name", "nth")):
        errors.append(f"{where}: needs one of role, testid, css, has_text, xpath")
        return {}
    from navig.agent.browser_turn import validate_actions  # noqa: PLC0415

    checked = validate_actions([{"action": "hover", **loc}])
    if checked.errors:
        errors.append(f"{where}: {checked.errors[0].split(': ', 1)[-1]}")
        return {}
    params = params or {}
    for key in ("name", "testid", "css", "has_text", "xpath"):
        for ref in _refs(loc.get(key)):
            if ref not in params:
                errors.append(f"{where}: {{{{{ref}}}}} is not a declared param")
                return {}
            if params[ref].get("type") == "secret":
                errors.append(f"{where}: a secret param cannot go into a locator")
                return {}
    return {k: checked.actions[0][k] for k in LOCATOR_KEYS if k in checked.actions[0]}


def _vault_ref(raw: Any, where: str, origins: list[str], errors: list[str]) -> dict[str, str] | None:
    """``{"domain", "username"}`` — which vaulted website login fills this secret. The domain
    must bind (same registrable domain) to one of the flow's origins: a flow can never aim a
    credential at a site it does not act on. The value itself is never in the document."""
    if not isinstance(raw, dict):
        errors.append(f"{where}: vault must be an object with domain and username")
        return None
    domain = str(raw.get("domain") or "").strip().lower()
    username = str(raw.get("username") or "").strip()
    if not re.match(r"^[a-z0-9.-]{1,253}$", domain) or not 0 < len(username) <= 200:
        errors.append(f"{where}: vault needs a host name (domain) and a username")
        return None
    from navig.browser.origin_match import same_registrable_domain  # noqa: PLC0415

    if not any(same_registrable_domain(urlsplit(o).hostname or "", domain) for o in origins):
        errors.append(f"{where}: the vault login for {domain} does not belong to this flow's sites")
        return None
    return {"domain": domain, "username": username}


def _params(raw: Any, errors: list[str], origins: list[str] | None = None) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append("params must be a list")
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, p in enumerate(raw[: MAX_PARAMS + 1]):
        where = f"param {i + 1}"
        if i == MAX_PARAMS:
            errors.append(f"at most {MAX_PARAMS} params")
            break
        if not isinstance(p, dict):
            errors.append(f"{where}: not an object")
            continue
        name = str(p.get("name") or "")
        if not _PARAM_NAME.match(name):
            errors.append(f"{where}: name must be lower_snake_case (got {name!r})")
            continue
        if name in seen:
            errors.append(f"{where}: {name!r} is declared twice")
            continue
        seen.add(name)
        ptype = p.get("type", "string")
        if ptype not in PARAM_TYPES:
            errors.append(f"{where}: type must be one of {', '.join(PARAM_TYPES)}")
            continue
        item: dict[str, Any] = {"name": name, "type": ptype}
        label = _text(p.get("label"), 80)
        if label:
            item["label"] = label
        if "default" in p and p["default"] is not None:
            if ptype == "secret":
                errors.append(f"{where}: a secret param cannot have a default")
                continue
            default = p["default"]
            if not isinstance(default, (str, int, float, bool)):
                errors.append(f"{where}: default must be a plain value")
                continue
            if isinstance(default, str) and _redact(default) != default:
                errors.append(f"{where}: the default looks like a secret — make it a secret param")
                continue
            item["default"] = default
        item["required"] = bool(p.get("required", "default" not in item))
        if p.get("vault") is not None:
            if ptype != "secret":
                errors.append(f"{where}: only a secret param can come from the vault")
                continue
            ref = _vault_ref(p["vault"], where, origins or [], errors)
            if ref is None:
                continue
            item["vault"] = ref
        out.append(item)
    return out


def _refs(value: Any) -> list[str]:
    return _PARAM_REF.findall(value) if isinstance(value, str) else []


def _url_ok(url: str, origins: list[str], where: str, errors: list[str]) -> bool:
    """The URL's origin is literal and inside *origins*."""
    head = url.split("://", 1)[-1].split("/", 1)[0]
    if "{{" in url.split("://", 1)[0] or "{{" in head:
        errors.append(f"{where}: a parameter cannot choose the site — only the path or query")
        return False
    origin = origin_of(_PARAM_REF.sub("x", url))
    if origin is None:
        errors.append(f"{where}: only http(s) addresses can be opened")
        return False
    if origin not in origins:
        errors.append(f"{where}: {origin} is not one of this flow's origins")
        return False
    return True


def _timeout(raw: Any, where: str, errors: list[str]) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        errors.append(f"{where}: timeout_ms must be a number")
        return None
    return int(max(MIN_TIMEOUT_MS, min(MAX_TIMEOUT_MS, raw)))


def _expect(raw: Any, where: str, errors: list[str], params: dict[str, dict]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        errors.append(f"{where}: expect must be an object")
        return {}
    out: dict[str, Any] = {}
    if any(k in raw for k in LOCATOR_KEYS):
        out.update(_locator(raw, where, errors, params))
    if raw.get("url_contains") is not None:
        uc = str(raw["url_contains"])
        if not 0 < len(uc) <= 500:
            errors.append(f"{where}: url_contains must be 1-500 characters")
        else:
            out["url_contains"] = uc
    if raw.get("gone") is True:
        out["gone"] = True
    t = _timeout(raw.get("timeout_ms"), where, errors)
    if t is not None:
        out["timeout_ms"] = t
    if not any(k in out for k in ("url_contains", *LOCATOR_KEYS)):
        errors.append(f"{where}: expect needs url_contains or a locator")
        return {}
    return out


def _step(raw: Any, i: int, params: dict[str, dict], origins: list[str], errors: list[str]) -> dict[str, Any] | None:
    where = f"step {i + 1}"
    if not isinstance(raw, dict):
        errors.append(f"{where}: not an object")
        return None
    action = raw.get("action")
    if action not in STEP_ACTIONS:
        errors.append(f"{where}: unknown action {action!r}")
        return None
    where = f"step {i + 1} ({action})"
    n_err = len(errors)
    step: dict[str, Any] = {"id": raw["id"] if isinstance(raw.get("id"), str) and _STEP_ID.match(raw["id"]) else f"s{i + 1}",
                            "action": action}

    if action in ("click", "fill", "select") or (action in ("wait_for", "assert") and any(k in raw for k in LOCATOR_KEYS)):
        step.update(_locator(raw, where, errors, params))
        fallbacks = raw.get("fallbacks") or []
        if not isinstance(fallbacks, list):
            errors.append(f"{where}: fallbacks must be a list")
        else:
            fb = [_locator(f, f"{where} fallback {j + 1}", errors, params) for j, f in enumerate(fallbacks[:MAX_FALLBACKS])]
            if fb := [f for f in fb if f]:
                step["fallbacks"] = fb

    sensitive = raw.get("sensitive") is True
    if action in ("fill", "select"):
        value = raw.get("value")
        if isinstance(value, (bool, int, float)):
            if action == "select":
                value = str(value)
        elif not isinstance(value, str):
            errors.append(f"{where}: needs a value")
            value = None
        if value is not None:
            refs = _refs(value)
            for name in refs:
                if name not in params:
                    errors.append(f"{where}: {{{{{name}}}}} is not a declared param")
            secret_refs = [n for n in refs if params.get(n, {}).get("type") == "secret"]
            if secret_refs:
                whole = _WHOLE_REF.match(value) if isinstance(value, str) else None
                if action != "fill" or not whole:
                    errors.append(f"{where}: a secret param must be the whole value of a fill step")
                sensitive = True
            elif sensitive:
                errors.append(f"{where}: a sensitive field takes a secret param, never a literal value")
            elif isinstance(value, str) and not refs and _redact(value) != value:
                errors.append(f"{where}: this value looks like a secret — make it a secret param")
            step["value"] = value
    elif action == "navigate":
        url = str(raw.get("url") or "")
        if any(params.get(n, {}).get("type") == "secret" for n in _refs(url)):
            errors.append(f"{where}: a secret param cannot go into a URL")
        for name in _refs(url):
            if name not in params:
                errors.append(f"{where}: {{{{{name}}}}} is not a declared param")
        if _url_ok(url, origins, where, errors):
            step["url"] = url
    elif action == "key":
        keys = str(raw.get("keys") or "")
        if not 0 < len(keys) <= 40 or not re.match(r"^[A-Za-z0-9+ ]+$", keys):
            errors.append(f"{where}: keys must be a key chord such as Enter or Control+a")
        else:
            step["keys"] = keys
    elif action in ("wait_for", "assert"):
        if raw.get("url_contains") is not None:
            step["url_contains"] = str(raw["url_contains"])[:500]
        if raw.get("gone") is True:
            step["gone"] = True
        if not any(k in step for k in ("url_contains", *LOCATOR_KEYS)):
            errors.append(f"{where}: needs url_contains or a locator")

    if sensitive:
        step["sensitive"] = True
    t = _timeout(raw.get("timeout_ms"), where, errors)
    if t is not None:
        step["timeout_ms"] = t
    if raw.get("expect") is not None:
        exp = _expect(raw["expect"], f"{where} expect", errors, params)
        if exp:
            step["expect"] = exp
    note = _redact(_text(raw.get("note"), 300))
    if note:
        step["note"] = note
    return step if len(errors) == n_err else None


def validate_webflow(raw: Any) -> Validated:
    """Normalize *raw* into a ``navig.webflow/1`` document, or say why it cannot be one.

    Idempotent: ``validate_webflow(validate_webflow(x).doc).doc == validate_webflow(x).doc``.
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return Validated(errors=["a webflow must be a JSON object"])
    schema = raw.get("schema", SCHEMA)
    if schema != SCHEMA:
        return Validated(errors=[f"unsupported schema {schema!r} (this NAVIG reads {SCHEMA})"])

    name = _text(raw.get("name"), 120)
    if not name:
        errors.append("a webflow needs a name")
    wf_id = raw.get("id")
    if wf_id is not None and not (isinstance(wf_id, str) and _ID.match(wf_id)):
        errors.append(f"id must look like wf_<slug> (got {wf_id!r})")
        wf_id = None

    origins_raw = raw.get("origins")
    origins: list[str] = []
    if not isinstance(origins_raw, list) or not origins_raw:
        errors.append("origins must list the site(s) this flow may act on")
    else:
        for o in origins_raw[:MAX_ORIGINS]:
            norm = origin_of(o) if isinstance(o, str) else None
            if norm is None:
                errors.append(f"origin {o!r} is not an http(s) origin")
            elif norm not in origins:
                origins.append(norm)

    params = _params(raw.get("params"), errors, origins)
    by_name = {p["name"]: p for p in params}

    start_url = str(raw.get("start_url") or "")
    if not start_url:
        errors.append("a webflow needs a start_url")
    elif origins and _PARAM_REF.search(start_url):
        errors.append("start_url cannot contain parameters")
    elif origins:
        _url_ok(start_url, origins, "start_url", errors)

    steps_raw = raw.get("steps")
    steps: list[dict[str, Any]] = []
    if not isinstance(steps_raw, list) or not steps_raw:
        errors.append("a webflow needs at least one step")
    elif len(steps_raw) > MAX_STEPS:
        errors.append(f"at most {MAX_STEPS} steps")
    else:
        for i, s in enumerate(steps_raw):
            step = _step(s, i, by_name, origins, errors)
            if step:
                steps.append(step)
        ids = [s["id"] for s in steps]
        if len(set(ids)) != len(ids):
            # Renumber rather than refuse: ids only label steps for the editor and reports.
            for i, s in enumerate(steps):
                s["id"] = f"s{i + 1}"

    doc: dict[str, Any] = {"schema": SCHEMA}
    if wf_id:
        doc["id"] = wf_id
    doc["name"] = name
    description = _redact(_text(raw.get("description"), 2000))
    if description:
        doc["description"] = description
    if isinstance(raw.get("revision"), int) and not isinstance(raw["revision"], bool) and raw["revision"] > 0:
        doc["revision"] = raw["revision"]
    doc["origins"] = origins
    doc["start_url"] = start_url
    doc["params"] = params
    doc["steps"] = steps
    prov = raw.get("provenance")
    if isinstance(prov, dict):
        p = {k: _text(prov[k], 80) for k in ("source", "recorder", "recorded_at") if prov.get(k)}
        if p:
            doc["provenance"] = p
    for k in ("created_at", "updated_at"):
        if isinstance(raw.get(k), str):
            doc[k] = raw[k][:40]
    return Validated(doc=doc if not errors else {}, errors=errors)


def require_valid(raw: Any) -> dict[str, Any]:
    """:func:`validate_webflow` that raises :class:`WebflowError` instead of returning errors."""
    v = validate_webflow(raw)
    if not v.ok:
        raise WebflowError(v.errors)
    return v.doc


def dumps(doc: dict[str, Any]) -> str:
    """Canonical text of a validated document (stable across repeated round trips)."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def loads(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WebflowError([f"not JSON: {exc}"]) from exc
    return require_valid(raw)


def substitute(value: Any, params: dict[str, Any]) -> Any:
    """``{{name}}`` → the run's value (a whole-value reference keeps the value's type)."""
    if not isinstance(value, str):
        return value
    whole = _WHOLE_REF.match(value)
    if whole and whole.group(1) in params:
        return params[whole.group(1)]
    return _PARAM_REF.sub(lambda m: str(params.get(m.group(1), m.group(0))), value)


def resolve_params(doc: dict[str, Any], given: dict[str, Any]) -> dict[str, Any]:
    """The run's parameter values: *given*, then defaults; raises on a missing required one."""
    out: dict[str, Any] = {}
    missing: list[str] = []
    for p in doc.get("params", []):
        name = p["name"]
        if name in given and given[name] not in (None, ""):
            v = given[name]
            if p["type"] == "number":
                try:
                    v = float(v) if "." in str(v) else int(v)
                except ValueError:
                    raise WebflowError([f"{name} must be a number"]) from None
            elif p["type"] == "bool":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            out[name] = v
        elif "default" in p:
            out[name] = p["default"]
        elif p.get("required", True):
            missing.append(name)
    if missing:
        raise WebflowError([f"missing value for {', '.join(missing)}"])
    return out
