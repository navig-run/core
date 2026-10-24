"""The Unified Answer — one response object every surface renders.

This is the frozen wire contract from the NAVIG Reflex plan (§4). One answer is
produced once and rendered per surface:

* ``speech`` — what TTS says (Alexa SSML / Alice ``tts``);
* ``display_text`` — what a screen or transcript shows (OpenAI message content,
  Telegram text, Alice ``text``, the CLI line);
* ``table`` — an optional ``{"columns": [...], "rows": [[...]]}`` grid for CLI and
  deck tables;
* ``json`` — the machine payload (the ``data`` shape when omitted);
* ``directives`` — typed client actions ``{"type": "open"|"highlight"|"play",
  "params": {...}}``;
* ``suggestions`` — follow-ups (Alexa reprompt / Alice buttons / Telegram buttons);
* ``data`` — facts, including ``error``/``hint`` on a failed answer;
* ``plan`` — plan metadata (tool, target, blast radius, ``commit_token``);
* ``ok`` / ``intent`` — outcome and the intent that produced it.

The wire form (:meth:`Answer.as_dict`) adds ``schema_version: "1"``; the class
itself keeps only the ten frozen fields. :func:`validate_answer` is the structural
gate every producer can run, and ``answer.schema.json`` is the machine-readable
copy of the same rules.

Renderers live here too (one public surface, no LLM, no plugin imports):

* :func:`render_cli` — Rich table + line through ``navig.console_helper``;
* :func:`to_json` / :func:`to_payload` — the JSON surface;
* :func:`to_alexa` — SSML (≤5600 chars), simple card, reprompt, directives;
* :func:`to_alice` — text/tts (≤1024), suggestion buttons (≤5 × ≤64 chars),
  directives, session state from a plan;
* :func:`to_openai` — ``display_text`` as content + structured metadata;
* :func:`to_deck` — typed directives + table + machine payload;
* :func:`to_telegram` — text + suggestion buttons.

Two adapters keep producers out of the plumbing: :meth:`Answer.from_home_status`
turns the shipped ``navig home status --json`` payload into an answer, and
:meth:`Answer.from_plan` turns a Reflex ``CommitPlan`` (or its dict) into one.
"""

from __future__ import annotations

import json as _json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Bumped when a field changes meaning; surfaces pin this in their tests.
ANSWER_SCHEMA_VERSION = "1"

#: The ten frozen fields of the model (plan §4).
ANSWER_FIELDS: tuple[str, ...] = (
    "speech",
    "display_text",
    "ok",
    "table",
    "json",
    "directives",
    "suggestions",
    "data",
    "plan",
    "intent",
)

#: The wire fields: the frozen ten plus the version stamp.
ANSWER_WIRE_FIELDS: tuple[str, ...] = (*ANSWER_FIELDS, "schema_version")

#: Directive kinds a surface may act on. Anything else is ignored, not guessed.
DIRECTIVE_TYPES: tuple[str, ...] = ("open", "highlight", "play")

#: Renderer limits (the protocols' documented caps).
ALEXA_MAX_SSML_CHARS = 5600
ALEXA_MAX_CARD_CHARS = 8000
ALICE_MAX_TEXT_CHARS = 1024
ALICE_MAX_BUTTONS = 5
ALICE_MAX_BUTTON_CHARS = 64
TELEGRAM_MAX_MESSAGE_CHARS = 4096

_SSML_ESCAPES: tuple[tuple[str, str], ...] = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&apos;"),
)
_TAG_RE = re.compile(r"<[^>]*>")


@dataclass
class Answer:
    """One turn's answer, produced once and rendered by every surface."""

    speech: str
    display_text: str = ""
    ok: bool = True
    table: dict[str, Any] | None = None
    json: Any = None
    directives: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] | None = None
    intent: str = ""

    @property
    def text(self) -> str:
        """Alias for :attr:`speech` (the transcript/CLI-friendly name)."""
        return self.speech

    def written(self) -> str:
        """What a screen shows: ``display_text`` when set, else the spoken line."""
        return self.display_text or self.speech

    def as_dict(self) -> dict[str, Any]:
        """The wire envelope: frozen fields, version stamp, sensible defaults."""
        payload = {
            "speech": self.speech,
            "display_text": self.written(),
            "ok": self.ok,
            "table": self.table,
            "json": self.json if self.json is not None else dict(self.data),
            "directives": [dict(item) for item in self.directives],
            "suggestions": [str(item) for item in self.suggestions],
            "data": dict(self.data),
            "plan": dict(self.plan) if isinstance(self.plan, Mapping) else self.plan,
            "intent": self.intent,
            "schema_version": ANSWER_SCHEMA_VERSION,
        }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Answer:
        table = payload.get("table")
        plan = payload.get("plan")
        data = payload.get("data")
        return cls(
            speech=str(payload.get("speech") or ""),
            display_text=str(payload.get("display_text") or ""),
            ok=bool(payload.get("ok", True)),
            table=dict(table) if isinstance(table, Mapping) else None,
            json=payload.get("json"),
            directives=[
                dict(item) for item in payload.get("directives") or []
                if isinstance(item, Mapping)
            ],
            suggestions=[str(item) for item in payload.get("suggestions") or []],
            data=dict(data) if isinstance(data, Mapping) else {},
            plan=dict(plan) if isinstance(plan, Mapping) else None,
            intent=str(payload.get("intent") or ""),
        )

    # ── adapters ─────────────────────────────────────────────────────────

    @classmethod
    def from_home_status(
        cls,
        payload: Mapping[str, Any],
        *,
        intent: str = "home.status",
    ) -> Answer:
        """Turn ``navig home status --json`` into one answer (no plugin edit).

        The payload is the shipped plugin's envelope:
        ``{ok, count, devices: [snapshot…], providers: [readiness…], paths}``.
        Readiness is reported as facts (``data.providers`` mapping); the answer
        never invents a device or a state the plugin did not report.
        """
        devices = [row for row in payload.get("devices") or [] if isinstance(row, Mapping)]
        providers = [row for row in payload.get("providers") or [] if isinstance(row, Mapping)]
        readiness = {
            str(row.get("provider") or ""): bool(row.get("ready"))
            for row in providers
            if row.get("provider")
        }
        names: list[str] = []
        lines: list[str] = []
        rows: list[list[str]] = []
        for snapshot in devices:
            name = str(snapshot.get("name") or snapshot.get("device") or "device")
            room = str(snapshot.get("room") or "")
            provider = str(snapshot.get("provider") or "")
            state = snapshot.get("state")
            power = ""
            if isinstance(state, Mapping):
                power = str(state.get("power") or "")
            label = power or ("unknown" if not snapshot.get("ok") else "")
            status = "online" if snapshot.get("ok") else "offline"
            names.append(f"{name} ({label})" if label else name)
            lines.append(f"{name}: {label or status}")
            rows.append([name, room or "—", provider or "—", status])
        count = len(devices)
        online = sum(1 for snapshot in devices if snapshot.get("ok"))
        speech = (
            f"{count} device(s), {online} online" if count
            else "no devices in the home inventory"
        )
        return cls(
            speech=speech,
            display_text="\n".join(lines) or speech,
            ok=payload.get("ok") is not False,
            table={
                "columns": ["Device", "Room", "Provider", "Status"],
                "rows": rows,
            } if rows else None,
            json=dict(payload),
            data={
                "count": count,
                "online": online,
                "names": names,
                "providers": readiness,
                "paths": payload.get("paths") or {},
                "next_step": "next: navig endpoints list",
            },
            suggestions=["navig endpoints list"],
            intent=intent,
        )

    @classmethod
    def from_plan(
        cls,
        plan: Any,
        *,
        speech: str = "",
        display_text: str = "",
        ok: bool = True,
        data: Mapping[str, Any] | None = None,
        intent: str = "reflex.plan",
    ) -> Answer:
        """Turn a Reflex ``CommitPlan`` (dataclass or dict) into the answer envelope.

        The plan keeps its exact shape under ``plan``; a token adds the one command
        that finishes it to ``suggestions``. Nothing here executes the plan.
        """
        payload = plan.as_dict() if callable(getattr(plan, "as_dict", None)) else dict(plan)
        token = str(payload.get("token") or "")
        tool = str(payload.get("tool") or "action")
        blast = payload.get("blast_radius")
        count: int | None = None
        if isinstance(blast, Mapping):
            raw_count = blast.get("count")
            if isinstance(raw_count, (int, float)):
                count = int(raw_count)
        elif isinstance(blast, int):
            count = blast
        if count is None:
            targets = payload.get("targets")
            count = len(targets) if isinstance(targets, (list, tuple)) else 1
        spoken = speech or (
            f"Plan ready: {tool} · {count} target(s) · token {token}"
            if token
            else f"Plan ready: {tool} · {count} target(s)"
        )
        facts = dict(data) if isinstance(data, Mapping) else {}
        facts.setdefault("token", token)
        facts.setdefault("tool", tool)
        return cls(
            speech=spoken,
            display_text=display_text or spoken,
            ok=ok,
            json={"plan": payload},
            data=facts,
            plan=dict(payload),
            suggestions=[f"navig ask --commit {token}"] if token else [],
            intent=intent,
        )


# ── validation ───────────────────────────────────────────────────────────


def _validate_table(table: Any) -> list[str]:
    if table is None:
        return []
    if not isinstance(table, Mapping):
        return ["table: must be an object or null"]
    problems: list[str] = []
    columns = table.get("columns")
    rows = table.get("rows")
    if not isinstance(columns, list):
        problems.append("table.columns must be a list")
    elif any(not isinstance(column, str) for column in columns):
        problems.append("table.columns must contain strings")
    if not isinstance(rows, list):
        problems.append("table.rows must be a list")
    elif isinstance(columns, list):
        for index, row in enumerate(rows):
            if not isinstance(row, list):
                problems.append(f"table.rows[{index}]: must be a list")
            elif len(row) != len(columns):
                problems.append(
                    f"table.rows[{index}]: {len(row)} cells for {len(columns)} columns")
            elif any(not isinstance(cell, str) for cell in row):
                problems.append(f"table.rows[{index}]: cells must be strings")
    return problems


def _directive_type(directive: Mapping[str, Any]) -> str:
    # ``type`` is the contract; ``kind`` is tolerated so an older producer still
    # validates while it migrates.
    return str(directive.get("type") or directive.get("kind") or "")


def validate_answer(answer: Answer | Mapping[str, Any]) -> list[str]:
    """Return every structural problem with *answer*; ``[]`` means valid."""
    if isinstance(answer, Answer):
        payload: Mapping[str, Any] = answer.as_dict()
    elif isinstance(answer, Mapping):
        payload = answer
    else:
        return ["answer must be a mapping or an Answer"]
    problems: list[str] = []
    unknown = set(payload) - set(ANSWER_WIRE_FIELDS)
    if unknown:
        problems.append(f"unknown answer fields {sorted(unknown)}")
    if not isinstance(payload.get("speech"), str):
        problems.append("speech must be a string")
    if not isinstance(payload.get("display_text"), str):
        problems.append("display_text must be a string")
    if not isinstance(payload.get("ok"), bool):
        problems.append("ok must be a boolean")
    if not isinstance(payload.get("data"), Mapping):
        problems.append("data must be an object")
    problems.extend(_validate_table(payload.get("table")))
    directives = payload.get("directives")
    if not isinstance(directives, list):
        problems.append("directives must be a list")
    else:
        for index, directive in enumerate(directives):
            if not isinstance(directive, Mapping):
                problems.append(f"directives[{index}]: must be an object")
                continue
            if _directive_type(directive) not in DIRECTIVE_TYPES:
                problems.append(
                    f"directives[{index}]: type must be one of {list(DIRECTIVE_TYPES)}")
            params = directive.get("params")
            if params is not None and not isinstance(params, Mapping):
                problems.append(f"directives[{index}].params must be an object")
    suggestions = payload.get("suggestions")
    if not isinstance(suggestions, list) or any(
        not isinstance(item, str) for item in suggestions
    ):
        problems.append("suggestions must be a list of strings")
    plan = payload.get("plan")
    if plan is not None and not isinstance(plan, Mapping):
        problems.append("plan must be an object or null")
    if not isinstance(payload.get("intent"), str):
        problems.append("intent must be a string")
    version = payload.get("schema_version")
    if version is not None and version != ANSWER_SCHEMA_VERSION:
        problems.append(
            f"schema_version must be {ANSWER_SCHEMA_VERSION!r}, got {version!r}")
    return problems


# ── renderers ────────────────────────────────────────────────────────────


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    if limit <= 0:
        return ""
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _ssml_text(text: str) -> str:
    escaped = str(text or "")
    for raw, replacement in _SSML_ESCAPES:
        escaped = escaped.replace(raw, replacement)
    return f"<speak>{escaped}</speak>"


def _plain(text: str) -> str:
    """Strip markup tags for a tts field that must not contain any."""
    return _TAG_RE.sub("", str(text or "")).replace("&amp;", "&").strip()


def _directive_payload(directive: Mapping[str, Any]) -> dict[str, Any] | None:
    kind = _directive_type(directive)
    if kind not in DIRECTIVE_TYPES:
        return None
    return {"type": kind, "params": dict(directive.get("params") or {})}


def render_cli(answer: Answer | Mapping[str, Any]) -> None:
    """Render an answer for a terminal: house table + one styled line + nudge.

    Uses ``navig.console_helper`` (never a bare ``print``), leaves exactly one
    free-text column wrappable, and keeps literal Rich markup out of data cells.
    """
    from rich.markup import escape

    from navig import console_helper as ch

    if isinstance(answer, Mapping):
        answer = Answer.from_dict(answer)
    if answer.table:
        from navig.console_helper import Table

        columns = [str(column) for column in answer.table.get("columns") or []]
        rows = [list(row) for row in answer.table.get("rows") or []]
        grid = Table(box=None, show_header=True, padding=(0, 2))
        for index, column in enumerate(columns):
            grid.add_column(column, no_wrap=index < len(columns) - 1)
        for row in rows:
            grid.add_row(*[escape(str(cell)) for cell in row])
        ch.console.print(grid)
    line = answer.written()
    if line:
        style = "green" if answer.ok else "red"
        ch.console.print(f"[{style}]{escape(line)}[/{style}]")
    hint = str(answer.data.get("hint") or "")
    if hint:
        ch.dim(escape(hint))
    next_step = str(answer.data.get("next_step") or "")
    if next_step:
        ch.dim(escape(next_step))
    if answer.suggestions:
        ch.dim(escape("next: " + "  ·  ".join(answer.suggestions)))


def to_payload(answer: Answer | Mapping[str, Any]) -> dict[str, Any]:
    """The ``emit_json``-compatible dict (same shape as ``Answer.as_dict``)."""
    return answer.as_dict() if isinstance(answer, Answer) else dict(answer)


def to_json(answer: Answer | Mapping[str, Any], *, indent: int | None = 2) -> str:
    """The JSON document for a surface that speaks JSON."""
    return _json.dumps(to_payload(answer), ensure_ascii=False, indent=indent)


def to_alexa(answer: Answer) -> dict[str, Any]:
    """Alexa custom-skill response: SSML speech, card, reprompt, directives."""
    overhead = len("<speak></speak>")
    speech = _ssml_text(_clip(answer.speech, ALEXA_MAX_SSML_CHARS - overhead))
    response: dict[str, Any] = {
        "outputSpeech": {"type": "SSML", "ssml": speech},
    }
    card_text = answer.written()
    if card_text:
        response["card"] = {
            "type": "Simple",
            "title": "NAVIG",
            "content": _clip(card_text, ALEXA_MAX_CARD_CHARS),
        }
    if answer.suggestions:
        reprompt = _ssml_text(_clip(answer.suggestions[0], 300))
        response["reprompt"] = {"outputSpeech": {"type": "SSML", "ssml": reprompt}}
    directives = [
        item for directive in answer.directives
        if (item := _directive_payload(directive)) is not None
    ]
    if directives:
        response["directives"] = directives
    response["shouldEndSession"] = bool(
        answer.ok and not answer.suggestions and answer.plan is None)
    return {"version": "1.0", "response": response}


def to_alice(answer: Answer) -> dict[str, Any]:
    """Yandex Dialogs response: text/tts, suggestion buttons, directives, state."""
    text = _clip(answer.written(), ALICE_MAX_TEXT_CHARS)
    tts = _clip(_plain(answer.speech), ALICE_MAX_TEXT_CHARS)
    body: dict[str, Any] = {
        "text": text,
        "tts": tts,
    }
    buttons = [
        {"title": _clip(title, ALICE_MAX_BUTTON_CHARS), "payload": {}, "hide": True}
        for title in answer.suggestions[:ALICE_MAX_BUTTONS]
        if str(title).strip()
    ]
    if buttons:
        body["buttons"] = buttons
    directives = [
        item for directive in answer.directives
        if (item := _directive_payload(directive)) is not None
    ]
    if directives:
        body["directives"] = directives
    body["end_session"] = bool(
        answer.ok and not answer.suggestions and answer.plan is None)
    payload: dict[str, Any] = {"version": "1.0", "response": body}
    if answer.plan is not None:
        payload["session_state"] = dict(answer.plan)
    return payload


def to_openai(answer: Answer) -> dict[str, Any]:
    """OpenAI-style response: ``display_text`` as content, facts as metadata."""
    return {
        "content": answer.written(),
        "metadata": {
            "ok": answer.ok,
            "intent": answer.intent,
            "data": dict(answer.data),
            "suggestions": list(answer.suggestions),
            "directives": [dict(item) for item in answer.directives],
            "plan": dict(answer.plan) if isinstance(answer.plan, Mapping) else answer.plan,
            "schema_version": ANSWER_SCHEMA_VERSION,
        },
    }


def to_deck(answer: Answer) -> dict[str, Any]:
    """Deck payload: typed directives + table + machine JSON (no suggestions)."""
    return {
        "ok": answer.ok,
        "speech": answer.speech,
        "display_text": answer.written(),
        "directives": [dict(item) for item in answer.directives],
        "table": answer.table,
        "json": answer.json if answer.json is not None else dict(answer.data),
        "plan": answer.plan,
        "intent": answer.intent,
    }


def to_telegram(answer: Answer) -> dict[str, Any]:
    """Telegram payload: plain text + inline suggestion buttons (no markup)."""
    return {
        "text": _clip(answer.written(), TELEGRAM_MAX_MESSAGE_CHARS),
        "buttons": [{"text": str(title)} for title in answer.suggestions if title],
    }
