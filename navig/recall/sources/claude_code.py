"""Claude Code transcripts (``<claude home>/projects/<slug>/<sessionId>.jsonl``).

Selective by design (``POLICY`` below, measured on 693 real transcripts):

* typed user text — with injected blocks split out and re-attributed: a
  ``<task-notification>`` is an *agent report*, ``<pasted_content>`` is *pasted* material,
  ``<ide_selection>`` is an editor selection; harness wrappers (``<system-reminder>``,
  ``<command-name>`` …) are dropped. 38.8 % of user-role text was injected content.
* assistant visible text.
* structured decision evidence: ``AskUserQuestion`` answers (``user_answer``),
  ``ExitPlanMode`` plans + the operator's verdict, and ``Agent`` results (``agent_report``).
* everything else is skipped: Bash/Read/Grep output, attachments, thinking, file history,
  ``api-request*`` copies, compaction summaries (duplicates of the history kept in-file).
"""

from __future__ import annotations

import os
import re
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from navig.recall.model import Turn
from navig.recall.session_links import EDIT_TOOLS, LINKS_VERSION, commit_shas, is_commit_command
from navig.recall.sources.base import ParseResult, SourceRef, iter_jsonl, real_key

ADAPTER = "claude_code"

# Tool calls whose inputs/results are kept, and what they become.
POLICY: dict[str, str] = {
    "AskUserQuestion": "user_answer",
    "ExitPlanMode": "plan",
    "Agent": "agent_report",
    "Task": "agent_report",
}
_MAX_PENDING = 400
_MAX_TURN_CHARS = 60_000

_TAGGED = re.compile(
    r"<(?P<tag>task-notification|pasted_content|ide_selection|ide_opened_file|system-reminder|"
    r"command-name|command-message|command-args|local-command-stdout|local-command-stderr|"
    r"local-command-caveat|user-prompt-submit-hook|bash-input|bash-stdout|bash-stderr)"
    r"(?:\s[^>]*)?>(?P<body>.*?)</(?P=tag)(?:\s[^>]*)?>",
    re.S,
)
_TAG_SPEAKER = {
    "task-notification": "agent_report",
    "pasted_content": "user_pasted",
    "ide_selection": "ide_selection",
    "command-args": "user_typed",
}
_AGENT_TRAILER = re.compile(r"\n\s*agentId:\s.*\Z", re.S)
_USAGE = re.compile(r"<usage>.*?</usage>", re.S)
_ASYNC_STUB = "The agent is working in the background. You will be notified automatically"
_NOTIF_RESULT = re.compile(r"<result>(.*?)</result>", re.S)
_NOTIF_SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.S)
_XML_TAG = re.compile(r"</?[a-z][a-z0-9_-]*>")
_NOISE_LINES = re.compile(
    r"^\s*(\[Request interrupted by user[^\]]*\]|Caveat: The messages below were generated.*)\s*$", re.M
)


def claude_homes() -> list[Path]:
    """Every Claude config dir with a ``projects/`` folder: ``$CLAUDE_CONFIG_DIR``,
    ``~/.claude`` and ``~/.claude-*`` (same discovery ``navig processes`` uses)."""
    cands: list[Path] = []
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env:
        cands.append(Path(env))
    home = Path.home()
    cands.append(home / ".claude")
    try:
        cands.extend(sorted(p for p in home.glob(".claude-*") if p.is_dir()))
    except OSError:
        pass
    seen, out = set(), []
    for c in cands:
        k = real_key(c)
        if k not in seen and (c / "projects").is_dir():
            seen.add(k)
            out.append(c)
    return out


def discover(homes: list[Path] | None = None) -> Iterator[SourceRef]:
    """Top-level session transcripts, each yielded once even when reachable through a
    junctioned project folder. Subagent transcripts (``<session>/subagents``) are skipped:
    their final reports already reach the parent as ``Agent`` results."""
    seen: set[str] = set()
    for home in homes if homes is not None else claude_homes():
        try:
            files = sorted((home / "projects").glob("*/*.jsonl"))
        except OSError:
            continue
        for f in files:
            sid = f.stem
            k = real_key(f)
            if k in seen or sid in seen:
                continue
            seen.update((k, sid))
            yield SourceRef(source_id=f"cc:{sid}", adapter=ADAPTER, path=str(f),
                            meta={"session_id": sid, "project_dir": f.parent.name})


def _text_blocks(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    out: list[str] = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                out.append(b["text"])
            elif isinstance(b, str):
                out.append(b)
    return out


_HANDBACK = re.compile(r"\A\s*(?:\[harness:[^\]]*\]\s*)?\[Subagent hand-back\].*?The report follows:\s*\n", re.S)


def clean_agent_text(text: str) -> str:
    """An agent report without the harness's hand-back frame, resume trailer and usage block."""
    text = _AGENT_TRAILER.sub("", _USAGE.sub("", text))
    framed = _HANDBACK.match(text)
    if framed:
        text = textwrap.dedent(text[framed.end():])
    return text.strip()


def _notification_body(body: str) -> str:
    """A background-agent notification → its summary + result, without the envelope tags."""
    result = _NOTIF_RESULT.search(body)
    summary = _NOTIF_SUMMARY.search(body)
    if result:
        head = (summary.group(1).strip() + "\n\n") if summary else ""
        return clean_agent_text(head + result.group(1).strip())
    return clean_agent_text(_XML_TAG.sub("", body)).strip()


def split_user_text(text: str) -> list[tuple[str, str]]:
    """``[(speaker, text), …]`` — typed text separated from injected blocks."""
    parts: list[tuple[str, str]] = []
    for m in _TAGGED.finditer(text):
        speaker = _TAG_SPEAKER.get(m.group("tag"))
        body = m.group("body").strip()
        if m.group("tag") == "task-notification":
            body = _notification_body(body)
        if speaker and body:
            parts.append((speaker, body))
    typed = _NOISE_LINES.sub("", _TAGGED.sub("", text)).strip()
    if typed:
        parts.insert(0, ("user_typed", typed))
    return parts


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_TURN_CHARS else text[:_MAX_TURN_CHARS] + "\n…[truncated]"


def _answer_text(tur: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    answers = tur.get("answers") or {}
    questions = tur.get("questions") or []
    pairs: list[dict[str, Any]] = []
    if isinstance(answers, dict):
        qmeta = {str(q.get("question")): q for q in questions if isinstance(q, dict)}
        for q, a in answers.items():
            meta = qmeta.get(str(q), {})
            opts = [str(o.get("label")) for o in meta.get("options") or [] if isinstance(o, dict)]
            pairs.append({"question": str(q), "answer": str(a), "header": str(meta.get("header") or ""),
                          "options": opts})
    text = "\n\n".join(f"Q: {p['question']}\nA: {p['answer']}" for p in pairs)
    return text, pairs


def parse(path: Path, start: int = 0, pending: dict[str, Any] | None = None) -> ParseResult:
    """Parse complete lines from byte *start*. *pending* carries tool_use ids whose results
    may arrive after the cursor (persisted by the caller between incremental reads)."""
    pending = dict(pending or {})
    res = ParseResult(turns=[], end_offset=start)
    for _off, nxt, rec in iter_jsonl(path, start):
        res.end_offset = nxt
        if rec is None:
            res.bad_lines += 1
            continue
        rtype = rec.get("type")
        if rtype in ("ai-title", "custom-title"):
            res.title = str(rec.get("aiTitle") or rec.get("customTitle") or rec.get("title") or res.title)
            continue
        if rtype not in ("user", "assistant"):
            continue
        if rec.get("isSidechain") or rec.get("isCompactSummary") or rec.get("isMeta"):
            continue
        ts = str(rec.get("timestamp") or "")
        if ts:
            res.first_ts = res.first_ts or ts
            res.last_ts = ts
        if not res.cwd and rec.get("cwd"):
            res.cwd = str(rec["cwd"])
            res.meta["git_branch"] = str(rec.get("gitBranch") or "")
            res.meta["version"] = str(rec.get("version") or "")
        uuid = str(rec.get("uuid") or f"off{_off}")
        msg = rec.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if rtype == "assistant":
            _assistant(res, uuid, ts, content, pending)
        else:
            _user(res, uuid, ts, content, rec.get("toolUseResult"), pending)
    if len(pending) > _MAX_PENDING:
        pending = dict(list(pending.items())[-_MAX_PENDING:])
    res.meta["pending_tools"] = pending
    res.meta["links_v"] = LINKS_VERSION
    return res


def _meta_list(res: ParseResult, key: str, value: str) -> None:
    items = res.meta.setdefault(key, [])
    if value not in items:
        items.append(value)


def _link_result(res: ParseResult, tool: dict[str, Any], block: dict[str, Any]) -> None:
    """Session-link evidence: paths and commit ids only, never file contents."""
    if block.get("is_error"):
        return
    if tool["name"] == "_touch":
        _meta_list(res, "touched", str(tool.get("path") or ""))
    elif tool["name"] == "_commit":
        out = "\n".join(_text_blocks(block.get("content")))
        for sha in commit_shas(str(tool.get("cmd") or ""), out):
            _meta_list(res, "commits", sha)


def _assistant(res: ParseResult, uuid: str, ts: str, content: Any, pending: dict[str, Any]) -> None:
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return
    texts: list[str] = []
    for i, b in enumerate(content):
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip():
            texts.append(b["text"])
        elif b.get("type") == "tool_use" and b.get("name") in EDIT_TOOLS:
            # Session links: the path is recorded once the edit's result comes back without error.
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            fp = str(inp.get(EDIT_TOOLS[str(b["name"])]) or "")
            if fp:
                pending[str(b.get("id"))] = {"name": "_touch", "path": fp}
        elif b.get("type") == "tool_use" and b.get("name") == "Bash":
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            cmd = str(inp.get("command") or "")
            if is_commit_command(cmd):
                pending[str(b.get("id"))] = {"name": "_commit", "cmd": cmd[:2000]}
        elif b.get("type") == "tool_use" and b.get("name") in POLICY:
            name = str(b["name"])
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            pending[str(b.get("id"))] = {"name": name, "desc": str(inp.get("description") or "")[:200]}
            if name == "ExitPlanMode" and str(inp.get("plan") or "").strip():
                res.turns.append(Turn(msg_id=f"{uuid}:plan{i}", speaker="assistant", kind="plan",
                                      text=_clip(str(inp["plan"])), ts=ts,
                                      meta={"tool_use_id": str(b.get("id"))}))
    if texts:
        res.turns.append(Turn(msg_id=uuid, speaker="assistant", text=_clip("\n\n".join(texts)), ts=ts))


def _user(res: ParseResult, uuid: str, ts: str, content: Any, tur: Any, pending: dict[str, Any]) -> None:
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return
    n = 0
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            for speaker, text in split_user_text(b["text"]):
                # A background agent's report arrives as a notification: it is a finding too.
                kind = "report" if speaker == "agent_report" else "text"
                meta = {"description": text.split("\n", 1)[0][:200]} if kind == "report" else {}
                res.turns.append(Turn(msg_id=f"{uuid}:{n}", speaker=speaker, text=_clip(text), ts=ts,
                                      kind=kind, meta=meta))
                n += 1
        elif b.get("type") == "tool_result":
            tid = str(b.get("tool_use_id") or "")
            tool = pending.pop(tid, None)
            if tool is None:
                continue
            if tool["name"].startswith("_"):
                _link_result(res, tool, b)
                continue
            if tool["name"] == "ExitPlanMode" and isinstance(tur, dict) and tur.get("filePath") and not b.get("is_error"):
                _meta_list(res, "planned", str(tur["filePath"]))
            _tool_result(res, f"{uuid}:{n}", ts, tool, b, tur if isinstance(tur, dict) else {}, tid)
            n += 1


def _tool_result(res: ParseResult, msg_id: str, ts: str, tool: dict[str, Any], block: dict[str, Any],
                 tur: dict[str, Any], tid: str) -> None:
    name = tool["name"]
    is_error = bool(block.get("is_error"))
    result_text = "\n".join(_text_blocks(block.get("content"))).strip()
    if name == "AskUserQuestion":
        text, pairs = _answer_text(tur)
        if not text and result_text and not is_error:
            text, pairs = result_text, []
        if text:
            res.turns.append(Turn(msg_id=msg_id, speaker="user_answer", kind="answer", text=_clip(text), ts=ts,
                                  meta={"pairs": pairs, "tool_use_id": tid}))
    elif name == "ExitPlanMode":
        approved = not is_error
        plan = str(tur.get("plan") or "")
        if plan and not any(t.kind == "plan" and t.meta.get("tool_use_id") == tid for t in res.turns):
            # Newer versions read the plan from its file: the text only arrives in the result.
            res.turns.append(Turn(msg_id=f"{msg_id}:plan", speaker="assistant", kind="plan", text=_clip(plan),
                                  ts=ts, meta={"tool_use_id": tid}))
        verdict = "approved" if approved else "not approved"
        detail = "" if approved else (" — " + " ".join(result_text.split())[:300] if result_text else "")
        res.turns.append(Turn(msg_id=msg_id, speaker="user_plan_verdict", kind="verdict",
                              text=f"Plan {verdict}{detail}", ts=ts,
                              meta={"approved": approved, "tool_use_id": tid}))
    else:  # Agent / Task
        # A background launch returns only a stub; its report arrives later as a
        # <task-notification> in a user turn, which is attributed there.
        if is_error or tur.get("isAsync") or _ASYNC_STUB in result_text:
            return
        result_text = clean_agent_text(result_text)
        if not result_text:
            return
        res.turns.append(Turn(msg_id=msg_id, speaker="agent_report", kind="report", text=_clip(result_text),
                              ts=ts, meta={"description": tool.get("desc", ""), "tool_use_id": tid}))
