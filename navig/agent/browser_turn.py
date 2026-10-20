"""
browser_turn.py — one turn of the NAVIG Dock browser agent.

The Dock extension runs the observe → think → act loop in the browser (it owns the
page, the debugger and the takeover overlay); the daemon owns the *thinking*. Each
turn the extension sends the chat so far plus a fresh page observation (URL, title,
a compact accessibility tree with ``[ref_N]`` handles); this module turns that into
provider messages and parses the model's reply back into a validated action batch.

Two pure functions, so the protocol is testable without a model or a browser:

* :func:`build_turn_messages` — system prompt + capped history + the observation
  folded into the last user turn (strict user/assistant alternation, which some
  providers require).
* :func:`parse_actions` — finds the fenced ``actions`` block and validates every
  step against :data:`ACTION_SPECS`; unknown verbs and malformed fields are
  reported, never executed.

The model call itself goes through :func:`navig.providers.inference.astream_via_connection`
(the operator's AI connections, vault-held credentials).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# Caps — the request is an untrusted local payload; keep every piece bounded so one
# huge page cannot blow the model's context or the daemon's memory.
MAX_HISTORY_MESSAGES = 24
MAX_MESSAGE_CHARS = 8_000
MAX_TREE_CHARS = 40_000
MAX_SELECTION_CHARS = 2_000
MAX_ACTIONS_PER_TURN = 12

# A target can be a [ref_N] from the outline OR a locator the page resolves to exactly one
# element (ambiguous / missing → the step fails, nothing is clicked): role (+ name),
# testid, css, has_text, xpath; `nth` (1-based) picks one of several matches.
LOCATOR_TARGETS = ("role", "testid", "css", "has_text", "xpath")
_LOC: dict[str, tuple[type, ...]] = {
    "role": (str,), "name": (str,), "testid": (str,), "css": (str,), "has_text": (str,),
    "xpath": (str,), "nth": (int,),
}
# How to answer a JS dialog the step may open (default: dismiss; beforeunload: accept).
_DIALOG: dict[str, tuple[type, ...]] = {"dialog": (str,), "prompt_text": (str,)}

# verb → {field: allowed type(s)}. Fields not listed are dropped.
ACTION_SPECS: dict[str, dict[str, tuple[type, ...]]] = {
    "click": {"ref": (str,), "x": (int, float), "y": (int, float), "button": (str,), "clicks": (int,), **_LOC, **_DIALOG},
    "type": {"ref": (str,), "text": (str,), "clear": (bool,), "submit": (bool,), **_LOC, **_DIALOG},
    "key": {"keys": (str,), **_DIALOG},
    "scroll": {"direction": (str,), "amount": (int, float), "ref": (str,), **_LOC},
    "hover": {"ref": (str,), "x": (int, float), "y": (int, float), **_LOC},
    "select": {"ref": (str,), "value": (str,), **_LOC},
    "fill": {"ref": (str,), "value": (str, bool, int, float), **_LOC},
    "wait_for": {**_LOC, "url_contains": (str,), "gone": (bool,), "timeout_ms": (int, float)},
    "extract": {"ref": (str,), **_LOC, "attr": (str,), "max": (int,)},
    "upload": {"ref": (str,), **_LOC, "files": (list,)},
    "navigate": {"url": (str,), **_DIALOG},
    "wait": {"ms": (int, float)},
    "read_page": {"filter": (str,)},
    "get_text": {},
    "done": {"summary": (str,)},
    "ask_user": {"question": (str,)},
}

# Fields a verb cannot run without (a tuple of alternatives = any one suffices).
_REQUIRED: dict[str, tuple[tuple[str, ...], ...]] = {
    "click": (("ref", "x", *LOCATOR_TARGETS),),
    "type": (("text",),),
    "key": (("keys",),),
    "hover": (("ref", "x", *LOCATOR_TARGETS),),
    "select": (("ref", *LOCATOR_TARGETS), ("value",)),
    "fill": (("ref", *LOCATOR_TARGETS), ("value",)),
    "wait_for": ((*LOCATOR_TARGETS, "url_contains"),),
    "extract": (("ref", *LOCATOR_TARGETS),),
    "upload": (("ref", *LOCATOR_TARGETS), ("files",)),
    "navigate": (("url",),),
    "ask_user": (("question",),),
}

_REF_RE = re.compile(r"^ref_\d{1,7}$")
_FENCE_RE = re.compile(r"```(?:actions|json)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class ParsedActions:
    actions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    found_block: bool = False


def _clip(text: Any, limit: int) -> str:
    s = str(text or "")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def system_prompt() -> str:
    """Guardrail floor first, then the Dock agent prompt (builtin prompt store)."""
    from navig.agent.conv.guardrails import guardrail_floor_minimal
    from navig.prompts.loader import load_prompt

    return f"{guardrail_floor_minimal()}\n\n{load_prompt('browser/dock_agent')}"


def render_observation(obs: dict[str, Any] | None) -> str:
    """The ``<page>`` block the prompt describes. Empty string when there is none."""
    if not isinstance(obs, dict) or not obs:
        return ""
    url = _clip(obs.get("url"), 2_000)
    title = _clip(obs.get("title"), 300).replace('"', "'")
    tree = _clip(obs.get("tree"), MAX_TREE_CHARS)
    parts = [f'<page url="{url}" title="{title}">']
    selection = _clip(obs.get("selection"), MAX_SELECTION_CHARS).strip()
    if selection:
        parts.append(f"(selection) {selection}")
    if tree:
        parts.append(tree)
    parts.append("</page>")
    return "\n".join(parts)


def build_turn_messages(
    history: list[dict[str, Any]] | None,
    observation: dict[str, Any] | None,
    *,
    system: str | None = None,
) -> list[dict[str, str]]:
    """Provider messages for one turn.

    *history* is the side panel's transcript (``user``/``assistant`` only — any other
    role, e.g. a smuggled ``system``, is dropped). Consecutive same-role turns are
    merged and a leading assistant turn removed, so the result strictly alternates
    and starts with ``user``. The observation is appended to the final user turn.

    Raises ``ValueError`` when there is no user turn to answer.
    """
    turns: list[dict[str, str]] = []
    for m in (history or [])[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = _clip(m.get("content"), MAX_MESSAGE_CHARS).strip()
        if not content:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + content
        else:
            turns.append({"role": role, "content": content})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    if not turns or turns[-1]["role"] != "user":
        raise ValueError("the last message must be from the user")

    page = render_observation(observation)
    if page:
        turns[-1]["content"] += "\n\n" + page
    return [{"role": "system", "content": system if system is not None else system_prompt()}, *turns]


def _validate(step: Any, index: int) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(step, dict):
        return None, f"step {index + 1}: not an object"
    verb = step.get("action")
    if verb not in ACTION_SPECS:
        return None, f"step {index + 1}: unknown action {verb!r}"
    spec = ACTION_SPECS[verb]
    out: dict[str, Any] = {"action": verb}
    for key, types in spec.items():
        if key not in step:
            continue
        val = step[key]
        if isinstance(val, bool) and bool not in types:
            return None, f"step {index + 1} ({verb}): {key} has the wrong type"
        if not isinstance(val, types):
            return None, f"step {index + 1} ({verb}): {key} has the wrong type"
        out[key] = val
    for alternatives in _REQUIRED.get(verb, ()):
        if not any(k in out for k in alternatives):
            return None, f"step {index + 1} ({verb}): needs {' or '.join(alternatives)}"
    if "ref" in out and not _REF_RE.match(out["ref"]):
        return None, f"step {index + 1} ({verb}): {out['ref']!r} is not a ref"
    if ("x" in out) != ("y" in out):
        return None, f"step {index + 1} ({verb}): x and y go together"
    if "name" in out and "role" not in out:
        return None, f"step {index + 1} ({verb}): name goes with role"
    for key in ("css", "xpath", "has_text", "name", "testid"):
        if key in out and not (0 < len(out[key]) <= 500):
            return None, f"step {index + 1} ({verb}): {key} must be 1-500 characters"
    if "nth" in out:
        out["nth"] = max(1, min(int(out["nth"]), 50))
    if "dialog" in out and out["dialog"] not in ("accept", "dismiss"):
        return None, f"step {index + 1} ({verb}): dialog must be accept or dismiss"
    if verb == "upload":
        files = out["files"]
        if not (1 <= len(files) <= 10) or not all(isinstance(f, str) and 0 < len(f) <= 1024 for f in files):
            return None, f"step {index + 1} (upload): files must be 1-10 file paths"
    if verb == "wait_for":
        out["timeout_ms"] = max(0, min(int(out.get("timeout_ms", 5_000)), 15_000))
    if verb == "extract":
        out["max"] = max(1, min(int(out.get("max", 50)), 200))
    if verb == "wait":
        out["ms"] = max(0, min(int(out.get("ms", 500)), 5_000))
    if verb == "scroll" and out.get("direction", "down") not in ("up", "down"):
        return None, f"step {index + 1} (scroll): direction must be up or down"
    if verb == "click":
        if out.get("button", "left") not in ("left", "right"):
            return None, f"step {index + 1} (click): button must be left or right"
        out["clicks"] = max(1, min(int(out.get("clicks", 1)), 3))
    return out, None


def parse_actions(text: str) -> ParsedActions:
    """Parse the LAST fenced ``actions`` (or ``json``) block of a model reply.

    Accepts a JSON array or ``{"actions": [...]}``. Every step is validated; an
    invalid step stops the batch there (later steps may depend on it) and is
    reported in ``errors``. A reply with no block is a plain answer — no actions,
    no error.
    """
    result = ParsedActions()
    blocks = _FENCE_RE.findall(text or "")
    if not blocks:
        return result
    result.found_block = True
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError as exc:
        result.errors.append(f"the actions block is not valid JSON ({exc.msg})")
        return result
    if isinstance(data, dict):
        data = data.get("actions") if isinstance(data.get("actions"), list) else [data]
    if not isinstance(data, list):
        result.errors.append("the actions block must be a JSON array")
        return result
    checked = validate_actions(data)
    checked.found_block = True
    return checked


def validate_actions(steps: list[Any], *, allowed: frozenset[str] | None = None) -> ParsedActions:
    """Validate a batch of steps with the same rules the side panel's model replies get.

    Used by :func:`parse_actions` and by the Dock bridge (``dock_act``), so an outside
    agent and the side panel can never drive the page with two different vocabularies.
    *allowed* narrows the verbs (the bridge has no ``done`` / ``ask_user``). An invalid
    step stops the batch there — later steps may depend on it.
    """
    result = ParsedActions()
    for i, step in enumerate(steps[:MAX_ACTIONS_PER_TURN]):
        action, err = _validate(step, i)
        if err is None and allowed is not None and action["action"] not in allowed:
            err = f"step {i + 1}: {action['action']!r} is not available here"
        if err:
            result.errors.append(err)
            break
        result.actions.append(action)
    if len(steps) > MAX_ACTIONS_PER_TURN:
        result.errors.append(f"only the first {MAX_ACTIONS_PER_TURN} steps run per turn")
    return result


def strip_actions_block(text: str) -> str:
    """The reply's prose with the fenced actions block removed (for the chat bubble)."""
    return _FENCE_RE.sub("", text or "").strip()
