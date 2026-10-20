"""Project analyst — the agent-layer seam for "re-analyse with AI" in the Projects hub.

Three analyses, all grounded in the project's context pack
(:mod:`navig.projects.context_pack`) and all PROPOSALS — nothing here writes a
project file. The hub shows the result (a brief, a diff, a list of todos) and the
operator accepts or rejects it; accepting goes through the normal fingerprint-
guarded doc/todo routes.

* :func:`brief` — summary, current state, next 3 actions, risks, stale docs.
  Staleness is MEASURED here (days and commits since each plan doc was edited)
  and handed to the model as facts, so the model never has to guess it.
* :func:`reanalyze_doc` — the full updated text of one plan doc.
* :func:`extract_todos` — concrete todos from briefs/inbox/docs, deduplicated
  against what the project already tracks.

Prompts live in the builtin store (``builtin/prompts/project/analyst_*.md``).
All inference stays in ``navig/agent/`` (architectural law). Raises
:class:`AnalystUnavailableError` when no LLM backend is usable, which routes
map to a 503.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "AnalystUnavailableError", "brief", "doc_freshness", "extract_todos", "reanalyze_doc", "run_pattern",
]

_PACK_CAP = 30000
_DOC_CAP = 24000
_SOURCES_CAP = 20000
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", re.DOTALL)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class AnalystUnavailableError(RuntimeError):
    """No LLM backend is configured or reachable."""


def _prompt(slug: str) -> str:
    from navig.prompts.loader import load_prompt  # noqa: PLC0415

    text = load_prompt(slug)
    # load_prompt returns a "Warning: Prompt X not found." STRING on a miss — sending
    # that as a system prompt would run the model with no instructions at all.
    if not text or text.startswith("Warning: Prompt"):
        raise RuntimeError(f"the {slug} prompt is missing from this install")
    return text


def _is_unavailable(exc: Exception) -> bool:
    try:
        from navig.llm.liveness import classify_probe_error  # noqa: PLC0415

        status, _ = classify_probe_error(exc)
        return status in ("nokey", "unreachable")
    except Exception:  # noqa: BLE001 — the classifier is best-effort
        return False


def _call(system: str, user: str, *, max_tokens: int, temperature: float = 0.2) -> str:
    from navig.llm.generate import llm_generate  # noqa: PLC0415 — lazy, keeps boot fast

    try:
        raw = llm_generate(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            mode="planning", temperature=temperature, max_tokens=max_tokens,
        )
    except Exception as exc:
        if _is_unavailable(exc):
            raise AnalystUnavailableError(
                "No AI backend is configured or reachable — connect a provider "
                "(Connections app, or `navig connect`) and retry."
            ) from exc
        logger.warning("project analysis failed: %s", exc)
        raise RuntimeError(f"analysis failed: {exc}") from exc
    text = (raw or "").strip()
    m = _FENCE_RE.match(text)
    text = m.group(1).strip() if m else text
    if not text:
        raise RuntimeError("the model returned an empty answer")
    return text


def _parse_json(text: str) -> dict[str, Any]:
    for candidate in (text, *(_JSON_RE.findall(text)[:1])):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    raise RuntimeError("the model did not return the requested JSON")


def _str_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


# ── facts the model must not guess ──────────────────────────────────────────


def doc_freshness(root: Path, doc_index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Days and commits since each canonical plan doc was last edited."""
    from navig.projects import gitinfo  # noqa: PLC0415

    now = time.time()
    out: list[dict[str, Any]] = []
    for d in doc_index:
        if d.get("canonical") is None or d.get("group") != "plans":
            continue
        mtime = float(d.get("mtime") or 0)
        out.append({
            "path": d["path"],
            "days_since_edit": int((now - mtime) // 86400) if mtime else None,
            "commits_since_edit": gitinfo.commits_since(root, mtime) if mtime else None,
        })
    return out


def _facts_block(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return "FACTS: no canonical plan docs."
    lines = ["FACTS (measured, not estimated):"]
    for f in facts:
        commits = f["commits_since_edit"]
        lines.append(f"- {f['path']}: last edited {f['days_since_edit']} day(s) ago"
                     + (f", {commits} commit(s) since" if commits is not None else ""))
    return "\n".join(lines)


# ── analyses ────────────────────────────────────────────────────────────────


def brief(pack: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    """A one-minute brief: summary, state, next actions, risks, stale docs."""
    user = f"{_facts_block(facts)}\n\nCONTEXT PACK:\n{pack.get('markdown', '')[:_PACK_CAP]}"
    data = _parse_json(_call(_prompt("project/analyst_brief"), user, max_tokens=1200))
    stale = []
    for s in data.get("stale_docs") or []:
        if isinstance(s, dict) and s.get("path"):
            stale.append({"path": str(s["path"]), "why": str(s.get("why") or "")})
    result = {
        "summary": str(data.get("summary") or "").strip(),
        "state": str(data.get("state") or "").strip(),
        "next_actions": _str_list(data.get("next_actions"), 3),
        "risks": _str_list(data.get("risks"), 3),
        "stale_docs": stale[:6],
    }
    md = [f"**{result['summary']}**", "", result["state"]]
    if result["next_actions"]:
        md += ["", "Next:"] + [f"- {a}" for a in result["next_actions"]]
    if result["risks"]:
        md += ["", "Risks:"] + [f"- {r}" for r in result["risks"]]
    if stale:
        md += ["", "Stale docs:"] + [f"- {s['path']} — {s['why']}" for s in stale]
    result["markdown"] = "\n".join(md).strip()
    return result


def reanalyze_doc(pack: dict[str, Any], doc_path: str, content: str) -> dict[str, Any]:
    """The proposed full new text of one plan doc (the caller shows a diff)."""
    if len(content) > _DOC_CAP:
        raise ValueError("this document is too long to re-analyse in one pass")
    user = (f"DOCUMENT TO UPDATE: {doc_path}\n-----\n{content}\n-----\n\n"
            f"CONTEXT PACK:\n{pack.get('markdown', '')[:_PACK_CAP - len(content) // 2]}")
    proposed = _call(_prompt("project/analyst_reanalyze_doc"), user,
                     max_tokens=min(8000, max(1500, len(content) // 2)))
    if content.endswith("\n") and not proposed.endswith("\n"):
        proposed += "\n"
    return {"path": doc_path, "proposed": proposed, "changed": proposed.strip() != content.strip()}


_OUTPUT_CONTRACT = {
    "ideas": ('\n\nRespond with ONLY a JSON object, no code fence:\n'
              '{"ideas": [{"text": "...", "why": "...", "tags": ["..."], "source": "..."}]}'),
    "todos": ('\n\nRespond with ONLY a JSON object, no code fence:\n'
              '{"todos": [{"text": "...", "heading": "..."}]}'),
    "doc": "\n\nRespond with ONLY the markdown — no code fence, no commentary.",
    "plan": "\n\nRespond with ONLY the markdown — no code fence, no commentary.",
}


def run_pattern(
    pattern: dict[str, Any],
    pack: dict[str, Any],
    *,
    input_label: str = "",
    input_text: str = "",
    sources: list[tuple[str, str]] | None = None,
    focus: str = "",
    existing_ideas: list[str] | None = None,
    existing_todos: list[str] | None = None,
) -> dict[str, Any]:
    """Run one Idea Fabric pattern (see :mod:`navig.projects.patterns`). A PROPOSAL.

    Returns ``{"kind": "ideas", "ideas": [...]}``, ``{"kind": "todos", "todos": [...]}``
    or ``{"kind": "doc"|"plan", "markdown": "..."}``.
    """
    out_kind = pattern["output"]
    system = pattern["body"] + _OUTPUT_CONTRACT[out_kind]
    parts = [f"CONTEXT PACK:\n{pack.get('markdown', '')[:_PACK_CAP // 2]}"]
    if existing_ideas is not None:
        listed = "\n".join(f"- {t}" for t in existing_ideas[:200]) or "(none)"
        parts.append(f"EXISTING IDEAS:\n{listed}")
    if existing_todos:
        parts.append("EXISTING TODOS:\n" + "\n".join(f"- {t}" for t in existing_todos[:150]))
    if focus.strip():
        parts.append(f"FOCUS: {focus.strip()[:500]}")
    if input_text:
        parts.append(f"{input_label or 'INPUT'}:\n-----\n{input_text[:_DOC_CAP]}\n-----")
    if sources:
        budget = _SOURCES_CAP // max(1, len(sources))
        parts.append("SOURCES:\n" + "\n\n".join(f"### SOURCE: {lbl}\n{txt[:budget]}" for lbl, txt in sources))
    raw = _call(system, "\n\n".join(parts), max_tokens=2400 if out_kind in ("doc", "plan") else 1600,
                temperature=0.7 if pattern["id"] == "brainstorm" else 0.3)
    if out_kind in ("doc", "plan"):
        return {"kind": out_kind, "markdown": raw.strip() + "\n"}
    data = _parse_json(raw)
    if out_kind == "todos":
        todos = []
        for t in data.get("todos") or []:
            if isinstance(t, dict) and str(t.get("text") or "").strip():
                todos.append({"text": " ".join(str(t["text"]).split()), "heading": str(t.get("heading") or "").strip()})
        return {"kind": "todos", "todos": todos[:20]}
    have = {_norm_text(t) for t in (existing_ideas or []) + (existing_todos or [])}
    ideas = []
    for item in data.get("ideas") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text or _norm_text(text) in have:
            continue
        have.add(_norm_text(text))
        tags = [re.sub(r"[^a-z0-9-]", "", str(x).lower())[:24] for x in (item.get("tags") or [])
                if isinstance(x, str)]
        ideas.append({"text": text[:300], "why": str(item.get("why") or "").strip()[:400],
                      "tags": [x for x in tags if x][:3], "source": str(item.get("source") or "").strip()[:200]})
    return {"kind": "ideas", "ideas": ideas[:15]}


def _norm_text(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def extract_todos(sources: list[tuple[str, str]], existing: list[str]) -> dict[str, Any]:
    """Proposed todos from *sources* ``[(path, text)]`` not already in *existing*."""
    if not sources:
        return {"todos": []}
    budget = _SOURCES_CAP // max(1, len(sources))
    src = "\n\n".join(f"### SOURCE: {p}\n{t[:budget]}" for p, t in sources)
    tracked = "\n".join(f"- {t}" for t in existing[:300]) or "(none)"
    user = f"EXISTING TODOS:\n{tracked}\n\nSOURCES:\n{src}"
    data = _parse_json(_call(_prompt("project/analyst_extract_todos"), user, max_tokens=1500))
    have = {_norm_text(t) for t in existing}
    out: list[dict[str, str]] = []
    for item in data.get("todos") or []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text or _norm_text(text) in have:
            continue
        have.add(_norm_text(text))
        out.append({"text": text, "heading": str(item.get("heading") or "").strip(),
                    "source": str(item.get("source") or "").strip()})
    return {"todos": out[:15]}
