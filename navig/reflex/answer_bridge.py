"""The Answer boundary — one contract, switchable implementation.

``core/navig/ui/answer.py`` (nav-core) is the canonical ``Answer`` model. The
router is built in parallel with it, so this module is the *one-line switch*:

* when ``navig.ui.answer`` is importable, ``make_answer`` constructs **that** class
  (arguments are filtered to the fields the class actually declares, so an extra
  router field can never break construction);
* otherwise it constructs :class:`FallbackAnswer`, a minimal duck type with the same
  frozen field names and ``as_dict()`` shape.

Consumers only use ``make_answer`` / ``answer_as_dict`` / ``answer_from_dict`` and
treat the result as an opaque object, so swapping the implementation is one import.
No rendering happens here — surfaces render, this module carries the data.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, Iterable, Mapping

#: The frozen field set from the plan (§4). Order matches the wire contract.
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

SCHEMA_VERSION = "1"


@dataclasses.dataclass
class FallbackAnswer:
    """Stand-in for ``navig.ui.answer.Answer`` until nav-core's module lands."""

    speech: str
    display_text: str = ""
    ok: bool = True
    table: dict[str, Any] | None = None
    json: Any = None
    directives: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    suggestions: list[str] = dataclasses.field(default_factory=list)
    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    plan: dict[str, Any] | None = None
    intent: str = ""

    def as_dict(self) -> dict[str, Any]:
        return answer_payload(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FallbackAnswer":
        return cls(
            speech=str(payload.get("speech") or ""),
            display_text=str(payload.get("display_text") or ""),
            ok=bool(payload.get("ok", True)),
            table=payload.get("table") if isinstance(payload.get("table"), dict) else None,
            json=payload.get("json"),
            directives=[
                dict(item)
                for item in payload.get("directives") or []
                if isinstance(item, Mapping)
            ],
            suggestions=[str(item) for item in payload.get("suggestions") or []],
            data=dict(payload.get("data") or {}),
            plan=payload.get("plan") if isinstance(payload.get("plan"), dict) else None,
            intent=str(payload.get("intent") or ""),
        )


def answer_class() -> type:
    """The canonical Answer class when nav-core has shipped it, else the fallback."""
    try:
        from navig.ui.answer import Answer  # noqa: PLC0415 — optional by design

        return Answer
    except Exception:  # noqa: BLE001 — absent/broken UI layer must not break routing
        return FallbackAnswer


def _accepted_fields(cls: type) -> set[str]:
    if dataclasses.is_dataclass(cls):
        return {field.name for field in dataclasses.fields(cls)}
    try:
        return set(inspect.signature(cls).parameters)
    except (TypeError, ValueError):  # pragma: no cover — exotic class objects
        return set(ANSWER_FIELDS)


def make_answer(**fields: Any) -> Any:
    """Build an Answer, passing only the fields the resolved class accepts."""
    cls = answer_class()
    accepted = _accepted_fields(cls)
    payload = {name: value for name, value in fields.items() if name in accepted}
    payload.setdefault("speech", "")
    return cls(**payload)


def answer_as_dict(answer: Any) -> dict[str, Any]:
    """Serialize an Answer-shaped object (dataclass, dict, or ``as_dict()`` provider)."""
    if isinstance(answer, Mapping):
        return dict(answer)
    as_dict = getattr(answer, "as_dict", None)
    if callable(as_dict):
        return dict(as_dict())
    if dataclasses.is_dataclass(answer):
        return dataclasses.asdict(answer)
    return dict(vars(answer))


def answer_payload(answer: Any) -> dict[str, Any]:
    """The wire envelope: frozen fields + ``schema_version``."""
    source = answer if isinstance(answer, Mapping) else answer_as_dict(answer)
    payload: dict[str, Any] = {}
    for name in ANSWER_FIELDS:
        if name in source:
            payload[name] = source[name]
        elif name in ("table", "plan"):
            payload[name] = None
        elif name in ("directives", "suggestions"):
            payload[name] = []
        elif name == "data":
            payload[name] = {}
        elif name in ("display_text", "intent"):
            payload[name] = ""
        elif name == "ok":
            payload[name] = True
        else:
            payload[name] = None
    payload["speech"] = str(payload.get("speech") or "")
    if not payload.get("display_text"):
        payload["display_text"] = payload["speech"]
    if payload.get("json") is None:
        payload["json"] = payload.get("data") or {}
    payload.setdefault("schema_version", SCHEMA_VERSION)
    return payload


def answer_from_dict(payload: Mapping[str, Any]) -> Any:
    """Rebuild the stored Answer (a replay must return the same object shape)."""
    cls = answer_class()
    from_dict = getattr(cls, "from_dict", None)
    if callable(from_dict):
        try:
            return from_dict(dict(payload))
        except Exception:  # noqa: BLE001 — a shape mismatch falls back to the field constructor
            pass
    return make_answer(**{name: payload.get(name) for name in ANSWER_FIELDS if name in payload})


def problems(answer: Any) -> list[str]:
    """Structural validation — delegates to nav-core's ``validate_answer`` when shipped."""
    try:
        from navig.ui.answer import validate_answer  # noqa: PLC0415

        return list(validate_answer(answer_as_dict(answer)))
    except ImportError:
        return _fallback_problems(answer_as_dict(answer))
    except Exception as exc:  # noqa: BLE001 — a validator crash is a finding, not a pass
        return [f"validate_answer failed: {exc}"]


def _fallback_problems(payload: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload.get("speech"), str) or not str(payload.get("speech")).strip():
        issues.append("speech must be a non-empty string")
    if not isinstance(payload.get("ok"), bool):
        issues.append("ok must be a bool")
    for name in ("directives", "suggestions"):
        if not isinstance(payload.get(name), (list, tuple)):
            issues.append(f"{name} must be a list")
    data = payload.get("data")
    if data is not None and not isinstance(data, Mapping):
        issues.append("data must be an object")
    for item in payload.get("directives") or ():
        if not isinstance(item, Mapping) or "type" not in item:
            issues.append("each directive must be an object with a type")
            break
    table = payload.get("table")
    if table is not None:
        columns = table.get("columns") if isinstance(table, Mapping) else None
        rows = table.get("rows") if isinstance(table, Mapping) else None
        if not isinstance(columns, list) or not isinstance(rows, list):
            issues.append("table must carry columns[] and rows[]")
        elif any(len(row) != len(columns) for row in rows):
            issues.append("a table row is wider or narrower than its columns")
    return issues


def directives(*items: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Typed directive builder (``open``/``highlight``/``play`` only)."""
    allowed = {"open", "highlight", "play"}
    out: list[dict[str, Any]] = []
    for item in items:
        kind = str(item.get("type") or "")
        if kind in allowed:
            out.append({"type": kind, "params": dict(item.get("params") or {})})
    return out


def suggestions(items: Iterable[Any]) -> list[str]:
    return [str(item) for item in items if str(item).strip()]


__all__ = [
    "ANSWER_FIELDS",
    "FallbackAnswer",
    "SCHEMA_VERSION",
    "answer_as_dict",
    "answer_class",
    "answer_from_dict",
    "answer_payload",
    "directives",
    "make_answer",
    "problems",
    "suggestions",
]
