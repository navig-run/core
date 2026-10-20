"""The canonical knowledge vault: one Markdown file per record + an append-only event log.

``vault/<project>/<type>/<id>-<slug>.md`` holds the record's current state as YAML front
matter plus a human body. The free-text body sits between ``recall:body`` markers, so an
operator may edit it and the edit survives a round trip. ``vault/.events/YYYY-MM.jsonl`` is
the history (created · merged · status · reversal …); it is only ever appended to, and a
rebuilt index replays it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from navig.recall.model import Evidence, Record

_BODY_OPEN = "<!-- recall:body -->"
_BODY_CLOSE = "<!-- /recall:body -->"
_SLUG = re.compile(r"[^a-z0-9]+")
_FM = ("id", "type", "title", "summary", "project", "status", "status_basis", "endorsement", "impl",
       "lifecycle", "confidence", "canonical_key", "aliases", "tags", "created", "updated", "ext_id", "attrs")
_MAX_EVIDENCE_RENDER = 12


def _slug(title: str) -> str:
    return _SLUG.sub("-", title.casefold()).strip("-")[:50] or "record"


class Vault:
    def __init__(self, root: Path | None = None):
        if root is None:
            from navig.recall.paths import vault_dir  # noqa: PLC0415

            root = vault_dir()
        self.root = Path(root)

    # ── Records ───────────────────────────────────────────────

    def path_for(self, rec: Record) -> Path:
        return self.root / (rec.project or "shared") / rec.type / f"{rec.id}-{_slug(rec.title)}.md"

    def write(self, rec: Record, *, existing: str = "") -> Path:
        """Write *rec*; when its title (and so its file name) changed, the old file is replaced."""
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        path = self.path_for(rec)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, render(rec), newline="\n")
        if existing and Path(existing) != path:
            try:
                old = Path(existing)
                if old.exists() and old.name.startswith(rec.id):
                    old.unlink()
            except OSError:
                pass  # a stale duplicate is harmless; the event log is the history
        return path

    def remove(self, path: str) -> bool:
        """Delete one record file, only if it lives inside this vault."""
        p = Path(path)
        try:
            p.resolve().relative_to(self.root.resolve())
        except (OSError, ValueError):
            return False
        if p.name.startswith("rk-") and p.is_file():
            p.unlink()
            return True
        return False

    def iter_records(self) -> Iterator[tuple[Path, Record]]:
        if not self.root.exists():
            return
        for p in sorted(self.root.glob("*/*/rk-*.md")):
            try:
                yield p, parse(p.read_text(encoding="utf-8"))
            except (OSError, ValueError, yaml.YAMLError):
                continue

    # ── Events ────────────────────────────────────────────────

    def append_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        d = self.root / ".events"
        d.mkdir(parents=True, exist_ok=True)
        by_month: dict[str, list[str]] = {}
        for e in events:
            by_month.setdefault(str(e["ts"])[:7] or "undated", []).append(json.dumps(e, ensure_ascii=False))
        for month, lines in by_month.items():
            with open(d / f"{month}.jsonl", "a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")

    def iter_events(self) -> Iterator[dict[str, Any]]:
        d = self.root / ".events"
        if not d.exists():
            return
        for p in sorted(d.glob("*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue


    # ── Links ─────────────────────────────────────────────────

    def append_links(self, links: list[tuple[str, str, str]]) -> None:
        if not links:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / ".links.jsonl", "a", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(json.dumps(list(link), ensure_ascii=False) + "\n" for link in links))

    def iter_links(self) -> Iterator[tuple[str, str, str]]:
        p = self.root / ".links.jsonl"
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                a, b, rel = json.loads(line)
                yield str(a), str(b), str(rel)
            except (ValueError, TypeError):
                continue


def make_event(record_id: str, kind: str, ts: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or {}
    key = json.dumps([record_id, kind, ts, data], ensure_ascii=False, sort_keys=True, default=str)
    return {"id": "ev-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16], "record_id": record_id,
            "ts": ts, "kind": kind, "data": data}


def render(rec: Record) -> str:
    fm = {k: getattr(rec, k) for k in _FM}
    fm["evidence"] = [e.to_dict() for e in rec.evidence]
    head = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=1_000_000)
    lines = [f"---\n{head}---", "", f"# {rec.title}", ""]
    if rec.summary:
        lines += ["> " + ln if ln else ">" for ln in rec.summary.splitlines()] + [""]
    lines += [_BODY_OPEN, rec.body, _BODY_CLOSE, ""]
    if rec.evidence:
        lines += ["## Evidence", ""]
        for e in rec.evidence[:_MAX_EVIDENCE_RENDER]:
            quote = " ".join(e.excerpt.split())[:300]
            lines.append(f"- `{e.speaker}` · {e.ts[:10] or 'undated'} · `{e.source_id}#{e.msg_id}` — {quote}")
        if len(rec.evidence) > _MAX_EVIDENCE_RENDER:
            lines.append(f"- … {len(rec.evidence) - _MAX_EVIDENCE_RENDER} more in front matter")
        lines.append("")
    return "\n".join(lines)


def parse(text: str) -> Record:
    if not text.startswith("---\n"):
        raise ValueError("not a recall record (no front matter)")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("unterminated front matter")
    fm = yaml.safe_load(text[4:end]) or {}
    if not isinstance(fm, dict) or not fm.get("id"):
        raise ValueError("front matter without an id")
    rest = text[end + 4:]
    body = ""
    i, j = rest.find(_BODY_OPEN), rest.rfind(_BODY_CLOSE)
    if i >= 0 and j > i:
        body = rest[i + len(_BODY_OPEN):j]
        body = body[1:] if body.startswith("\n") else body
        body = body[:-1] if body.endswith("\n") else body
    return Record(
        id=str(fm["id"]), type=str(fm.get("type", "")), title=str(fm.get("title", "")),
        summary=str(fm.get("summary") or ""), body=body, project=str(fm.get("project") or "shared"),
        status=str(fm.get("status") or "proposed"), status_basis=str(fm.get("status_basis") or "assistant"),
        endorsement=str(fm.get("endorsement") or "none"), impl=str(fm.get("impl") or "unknown"),
        lifecycle=str(fm.get("lifecycle") or "unknown"),
        confidence=float(fm.get("confidence") or 0.5), canonical_key=str(fm.get("canonical_key") or ""),
        aliases=list(fm.get("aliases") or []), tags=list(fm.get("tags") or []),
        created=str(fm.get("created") or ""), updated=str(fm.get("updated") or ""),
        ext_id=str(fm.get("ext_id") or ""), attrs=dict(fm.get("attrs") or {}),
        evidence=[Evidence.from_dict(e) for e in (fm.get("evidence") or []) if isinstance(e, dict)],
    )
