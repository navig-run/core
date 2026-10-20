"""Navig Capture v1 — the one schema every browser capture speaks.

The Dock (context menus, the selection sheet, Lenses) POSTs captures to
``/api/deck/inbox/capture``. A v1 capture is written as a Markdown note whose YAML
frontmatter carries where it came from, so Recall's ``capture`` source can read it back
with its project, page and message ids::

    {v: 1, kind: page|selection|chat|prompt|table|link, project?, url, title,
     content_md | content | selection, hint?, note?, id?,
     meta | source: {site?, conversation_id?, message_ids?, role?},
     client: {id, version}, captured_at}

A body without ``v`` (or any v1 field) is a **legacy** capture and is written exactly as
before (``# title`` / ``Source: url`` / content), so older extensions keep working.

Validation is strict about size and shape (a capture is user content from a web page):
content ≤ 2 MB, short single-line fields, a project id that is a plain slug, at most 500
message ids. Unknown keys are ignored, never written.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

CAPTURE_VERSION = 1
FRONTMATTER_KEY = "navig_capture"
KINDS = ("page", "selection", "chat", "prompt", "table", "link")
HINTS = ("auto", "prompt", "idea", "decision", "term", "table", "note")
ROLES = ("user", "assistant", "tool", "system", "")

MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_TITLE = 300
MAX_URL = 4096
MAX_NOTE = 4000
MAX_SHORT = 200
MAX_IDS = 500

_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_V1_KEYS = ("v", "kind", "project", "meta", "source", "client", "captured_at", "content_md", "hint", "id")


class CaptureError(ValueError):
    """A capture the route refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Capture:
    kind: str
    url: str = ""
    title: str = ""
    content: str = ""
    v: int | None = None  # None = a legacy (pre-v1) payload
    id: str = ""
    project: str = ""
    hint: str = ""
    note: str = ""
    captured_at: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    client: dict[str, str] = field(default_factory=dict)

    @property
    def is_v1(self) -> bool:
        return self.v is not None


def _line(value: Any, limit: int, name: str, *, cut: bool = False) -> str:
    """A single-line string field: newlines folded; over *limit* it is refused (or, with
    *cut*, shortened — a page title is the page's, not something the caller can fix)."""
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        raise CaptureError(f"{name} must be a string")
    s = " ".join(str(value).split())
    if len(s) > limit:
        if cut:
            return s[: limit - 1].rstrip() + "…"
        raise CaptureError(f"{name} is longer than {limit} characters")
    return s


def _iso(value: Any) -> str:
    s = _line(value, 64, "captured_at")
    if not s:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureError("captured_at must be an ISO 8601 timestamp") from exc
    return s


def _meta(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CaptureError("meta must be an object")
    out: dict[str, Any] = {}
    for key in ("site", "conversation_id"):
        v = _line(raw.get(key), MAX_SHORT, f"meta.{key}")
        if v:
            out[key] = v
    role = _line(raw.get("role"), 20, "meta.role").lower()
    if role not in ROLES:
        raise CaptureError(f"meta.role must be one of: {', '.join(r for r in ROLES if r)}")
    if role:
        out["role"] = role
    ids = raw.get("message_ids")
    if ids is not None:
        if not isinstance(ids, list):
            raise CaptureError("meta.message_ids must be a list")
        if len(ids) > MAX_IDS:
            raise CaptureError(f"meta.message_ids holds more than {MAX_IDS} ids")
        clean = [_line(i, MAX_SHORT, "meta.message_ids[]") for i in ids]
        if any(clean):
            out["message_ids"] = [i for i in clean if i]
    imgs = raw.get("images")
    if imgs is not None:
        if not isinstance(imgs, list):
            raise CaptureError("meta.images must be a list")
        urls = [_line(u, MAX_URL, "meta.images[]") for u in imgs[:50]]
        urls = [u for u in urls if u.startswith(("http://", "https://"))]
        if urls:
            out["images"] = urls
    return out


def _client(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CaptureError("client must be an object")
    out = {}
    for key in ("id", "version"):
        v = _line(raw.get(key), 64, f"client.{key}")
        if v:
            out[key] = v
    return out


def is_v1_payload(body: dict[str, Any]) -> bool:
    return any(k in body for k in _V1_KEYS)


def parse_body(body: Any) -> Capture:
    """Validate a request body into a :class:`Capture` (raises :class:`CaptureError`)."""
    if not isinstance(body, dict):
        body = {}
    raw_content = body.get("content_md")
    if raw_content is None:
        raw_content = body.get("content")
    if raw_content is None:
        raw_content = body.get("selection")
    if raw_content is not None and not isinstance(raw_content, str):
        raise CaptureError("content must be a string")
    content = (raw_content or "").strip()
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise CaptureError(f"content is larger than {MAX_CONTENT_BYTES // (1024 * 1024)} MB", 413)
    url = _line(body.get("url"), MAX_URL, "url")
    title = _line(body.get("title"), MAX_TITLE, "title", cut=True) or url[:MAX_TITLE] or "Captured item"

    if not is_v1_payload(body):
        kind = _line(body.get("type"), 40, "type") or "page"
        return Capture(kind=kind, url=url, title=title, content=content)

    v = body.get("v", CAPTURE_VERSION)
    if v != CAPTURE_VERSION:
        raise CaptureError(f"unsupported capture version {v!r} (this NAVIG speaks v{CAPTURE_VERSION})")
    kind = _line(body.get("kind") or body.get("type") or "page", 40, "kind").lower()
    if kind not in KINDS:
        raise CaptureError(f"kind must be one of: {', '.join(KINDS)}")
    project = _line(body.get("project"), 120, "project")
    if project and not _PROJECT_RE.match(project):
        raise CaptureError("project must be a project id (letters, digits, '.', '_' or '-')")
    hint = _line(body.get("hint"), 20, "hint").lower()
    if hint and hint not in HINTS:
        raise CaptureError(f"hint must be one of: {', '.join(HINTS)}")
    note = str(body.get("note") or "").strip()
    if len(note) > MAX_NOTE:
        raise CaptureError(f"note is longer than {MAX_NOTE} characters")
    cid = _line(body.get("id"), 64, "id")
    if cid and not _ID_RE.match(cid):
        raise CaptureError("id must be 8-64 letters, digits, '_' or '-'")
    meta = _meta(body.get("meta") if body.get("meta") is not None else body.get("source"))
    if not content and kind not in ("page", "link"):
        raise CaptureError(f"a {kind} capture needs content")
    return Capture(
        kind=kind, url=url, title=title, content=content, v=CAPTURE_VERSION,
        id=cid or f"cap-{secrets.token_hex(8)}", project=project, hint=hint if hint != "auto" else "",
        note=note, captured_at=_iso(body.get("captured_at")), meta=meta, client=_client(body.get("client")),
    )


def frontmatter(cap: Capture) -> dict[str, Any]:
    fm: dict[str, Any] = {FRONTMATTER_KEY: CAPTURE_VERSION, "id": cap.id, "kind": cap.kind}
    for key in ("project", "title", "url", "hint", "note", "captured_at"):
        val = getattr(cap, key)
        if val:
            fm[key] = val
    fm.update(cap.meta)
    if cap.client:
        fm["client"] = dict(cap.client)
    return fm


def render_note(cap: Capture) -> str:
    """The note written to the inbox. Legacy captures keep the pre-v1 layout byte for byte."""
    lines = [f"# {cap.title}", ""]
    if cap.url:
        lines += [f"Source: {cap.url}", ""]
    if cap.content:
        lines.append(cap.content)
    body = "\n".join(lines) + "\n"
    if not cap.is_v1:
        return body
    import yaml  # noqa: PLC0415

    head = yaml.safe_dump(frontmatter(cap), sort_keys=False, allow_unicode=True, width=10_000)
    return f"---\n{head}---\n\n{body}"


def read_note(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split a note into (v1 frontmatter or None, body). Anything else is (None, text)."""
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end < 0:
        return None, text
    import yaml  # noqa: PLC0415

    try:
        fm = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None, text
    if not isinstance(fm, dict) or fm.get(FRONTMATTER_KEY) != CAPTURE_VERSION:
        return None, text
    rest = text[end + 4:]
    nl = rest.find("\n")
    return fm, rest[nl + 1:] if nl >= 0 else ""


def note_content(fm: dict[str, Any], body: str) -> str:
    """The captured content of a v1 note body, without the header lines the writer added
    (``# title`` and ``Source: url``)."""
    lines = body.lstrip("\n").split("\n")
    title = str(fm.get("title") or "")
    if lines and lines[0] == f"# {title}":
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    if lines and fm.get("url") and lines[0] == f"Source: {fm['url']}":
        lines = lines[1:]
    return "\n".join(lines).strip()


def slug_for(title: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in title.lower())[:48].strip("-") or "capture"
