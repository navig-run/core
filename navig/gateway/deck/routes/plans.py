"""Plans engine routes for the Deck API — the project command center.

Exposes the ``navig.plans`` engine (CURRENT_PHASE.md + milestones + plan docs)
over HTTP so the OS desktop app and the deck can render and edit a space's
command center — the capability formerly locked inside the archived
navig-spaces VS Code extension (ledger: docs/forge-consolidation.md).

Routes (registered in navig/gateway/deck/__init__.py, prefix /api/deck/plans):
    GET  /state               → phase + milestone progress + plan-doc listing
    GET  /doc?path=<rel>      → one plan doc (confined to .navig/plans)
    POST /doc                 → save/create a plan doc (atomic write)
    POST /toggle-task         → toggle a markdown checkbox by 0-based line
    POST /phase/advance       → install the next phase file as CURRENT_PHASE.md
    POST /phase/block         → block the current phase with a reason
    POST /phase/unblock       → unblock the current phase
    POST /phase/complete      → mark the current phase completed
    POST /milestone/commit    → git-commit .navig/plans as a milestone checkpoint
    POST /milestone/rollback  → HARD reset to the last milestone commit (confirm-gated)
    POST /generate            → scaffold the standard plan-doc suite (skip existing);
                                mode:"ai" drafts the suite from VISION.md via the
                                agent layer (navig/agent/plan_drafter.py)

Every route takes an optional ``space`` param (query for GET, body for POST):
a space id from the spaces registry, resolved to its root path the same way
the inbox/catalog handlers do. When omitted, the gateway's project root is
used (``.navig/`` walk-up from the working directory).

Doc paths are always relative to the space's ``.navig/plans`` directory and
are resolved + prefix-verified — traversal outside the plans dir is a 400.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

if TYPE_CHECKING:  # heavy engine types only for annotations — runtime imports stay lazy
    from navig.plans.current_phase_manager import PhaseState

logger = logging.getLogger(__name__)

_MILESTONE_SUBJECT_PREFIX = "[navig] Milestone:"
_PLANS_REL = ".navig/plans"
_GIT_TIMEOUT = 30  # seconds — local plumbing only, never network
_CHECKBOX_RE = re.compile(r"^(\s*[-*]\s*\[)([ xX])(\])")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ok(data: Any, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _json(request: "web.Request") -> dict | None:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


class _PlanOpError(Exception):
    """A user-reportable plan-operation failure carrying an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _find_project_root() -> Path:
    try:
        from navig.commands.plans import _find_project_root as _fpr

        return _fpr()
    except Exception:
        return Path.cwd()


def _space_root(space_id: str) -> Path | None:
    """Resolve a spaces-registry id to its root path (same seam as inbox routes)."""
    try:
        from navig.spaces.resolver import discover_space_paths

        cfg = (discover_space_paths() or {}).get(space_id)
        if cfg is not None:
            p = Path(str(getattr(cfg, "path", "")))
            return p if str(p) else None
    except Exception:
        logger.debug("space resolve failed for %s", space_id, exc_info=True)
    return None


def _resolve_root(space: str | None) -> Path:
    """Space id → space root; no space → the active project root."""
    if space:
        root = _space_root(space)
        if root is None:
            raise _PlanOpError(f"unknown space: {space}", 404)
        return root
    return _find_project_root()


def _space_param(request: "web.Request", body: dict | None = None) -> str | None:
    space = (body or {}).get("space") or request.rel_url.query.get("space")
    space = str(space).strip() if space else ""
    return space or None


def _plans_dir(root: Path) -> Path:
    return root / ".navig" / "plans"


def _confined_doc_path(plans_dir: Path, rel: str) -> Path | None:
    """Resolve *rel* inside *plans_dir*; ``None`` when it escapes (traversal)."""
    from navig.gateway.deck.routes._utils import confine_under  # noqa: PLC0415

    return confine_under(plans_dir, rel)


# ── Payload serializers (sync — called from thread workers) ──────────────────

def _phase_payload(state: "PhaseState") -> dict[str, Any]:
    return {
        "phase": state.phase,
        "title": state.title,
        "started": state.started,
        "milestone": state.milestone,
        "status": state.status,
        "blocked_by": state.blocked_by,
        "active_tasks": list(state.active_tasks),
        "source_path": str(state.source_path),
    }


def _progress_payload(root: Path) -> dict[str, Any]:
    from navig.plans.milestone_progress import MilestoneProgressEngine

    engine = MilestoneProgressEngine(root)
    milestones: list[dict[str, Any]] = []
    done = total = 0
    for ms in engine.list_milestones():
        milestones.append({
            "name": ms.name,
            "title": ms.title,
            "status": ms.status,
            "target_date": ms.target_date,
            "done": ms.done_count,
            "total": ms.total_count,
            "pct": ms.progress_pct,
            "strip": engine.render_strip(ms),
        })
        done += ms.done_count
        total += ms.total_count
    pct = round(done / total * 100, 1) if total else 0.0
    return {"milestones": milestones, "done": done, "total": total, "pct": pct}


def _docs_payload(plans_dir: Path) -> list[dict[str, Any]]:
    from navig.plans.frontmatter import first_h1, parse_frontmatter

    if not plans_dir.is_dir():
        return []
    docs: list[dict[str, Any]] = []
    for path in sorted(plans_dir.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            st = path.stat()
            with path.open(encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)  # title only — never load whole docs for a listing
        except OSError:
            continue
        title = first_h1(head) or parse_frontmatter(head).get("title", "") or path.stem
        docs.append({
            "name": path.name,
            "path": path.relative_to(plans_dir).as_posix(),
            "title": title,
            "size": st.st_size,
            "mtime": st.st_mtime,
        })
    return docs


# ── Git plumbing (sync — called from thread workers) ─────────────────────────

def _git(root: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GIT_TIMEOUT,
    )


def _require_git_repo(root: Path) -> None:
    """The space root must BE a repo's toplevel — not merely sit inside one.

    A space nested in an unrelated outer repo would otherwise checkpoint (and,
    far worse, ``reset --hard``) the ENCLOSING repository.
    """
    try:
        top = _git(root, "rev-parse", "--show-toplevel")
    except FileNotFoundError:
        raise _PlanOpError("git is not available on this machine", 500) from None
    if top.returncode != 0:
        raise _PlanOpError("space is not a git repository", 400)
    toplevel = Path(top.stdout.strip())
    try:
        same = toplevel.resolve() == root.resolve()
    except OSError:
        same = False
    if not same:
        raise _PlanOpError(
            f"space is not a git repository (it sits inside the repo at {toplevel}) — "
            "milestone checkpoints need the space root to be the repo root",
            400,
        )


def _porcelain_paths(line: str) -> list[str]:
    """Path(s) from one ``git status --porcelain`` line (handles renames)."""
    body = line[3:] if len(line) > 3 else ""
    parts = body.split(" -> ") if " -> " in body else [body]
    return [p.strip().strip('"') for p in parts if p.strip()]


def _under_plans(path: str) -> bool:
    p = path.replace("\\", "/").rstrip("/")
    return p == _PLANS_REL or p.startswith(_PLANS_REL + "/")


def _commit_milestone_sync(root: Path, name: str) -> dict[str, Any]:
    """Append a changelog line, then path-scoped commit of ``.navig/plans``."""
    from navig.core.yaml_io import atomic_write_text

    _require_git_repo(root)

    plans_dir = _plans_dir(root)
    plans_dir.mkdir(parents=True, exist_ok=True)

    # Always have something to commit: a dated changelog line per milestone.
    changelog = plans_dir / "CHANGELOG.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"- {stamp} — Milestone: {name}\n"
    try:
        existing = changelog.read_text(encoding="utf-8") if changelog.is_file() else ""
    except OSError:
        existing = ""
    if not existing:
        existing = "# Plans Changelog\n\n"
    elif not existing.endswith("\n"):
        existing += "\n"
    atomic_write_text(changelog, existing + entry)

    # Ignored plans dir (or no effective change) → clean 400, never a git error.
    status = _git(root, "status", "--porcelain", "--", _PLANS_REL)
    if status.returncode != 0:
        raise _PlanOpError(f"git status failed: {status.stderr.strip()}", 500)
    if not status.stdout.strip():
        raise _PlanOpError("nothing to commit", 400)

    add = _git(root, "add", "--", _PLANS_REL)
    if add.returncode != 0:
        raise _PlanOpError(f"git add failed: {add.stderr.strip()}", 500)
    staged = _git(root, "diff", "--cached", "--quiet", "--", _PLANS_REL)
    if staged.returncode == 0:
        raise _PlanOpError("nothing to commit", 400)

    message = f"{_MILESTONE_SUBJECT_PREFIX} {name}"
    # Pathspec-limited commit: only .navig/plans lands, even if the user had
    # other files staged — never sweep foreign work into a milestone commit.
    commit = _git(root, "commit", "-m", message, "--", _PLANS_REL)
    if commit.returncode != 0:
        detail = (commit.stderr or commit.stdout).strip()
        raise _PlanOpError(f"git commit failed: {detail}", 500)

    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    return {"sha": sha, "message": message, "changelog_entry": entry.strip()}


def _rollback_milestone_sync(root: Path) -> dict[str, Any]:
    """``git reset --hard`` to the last milestone commit (caller confirm-gates)."""
    _require_git_repo(root)

    # Protect user work: refuse when anything OUTSIDE .navig/plans is dirty.
    status = _git(root, "status", "--porcelain")
    if status.returncode != 0:
        raise _PlanOpError(f"git status failed: {status.stderr.strip()}", 500)
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        for p in _porcelain_paths(line):
            if not _under_plans(p):
                raise _PlanOpError(
                    f"working tree has uncommitted changes outside {_PLANS_REL} "
                    f"({p}) — commit or stash them first",
                    400,
                )

    log = _git(root, "log", "--pretty=%H%x09%s")
    if log.returncode != 0:
        raise _PlanOpError(f"git log failed: {log.stderr.strip()}", 500)
    target_sha = subject = None
    for line in log.stdout.splitlines():
        sha, _, subj = line.partition("\t")
        if subj.startswith(_MILESTONE_SUBJECT_PREFIX):
            target_sha, subject = sha.strip(), subj.strip()
            break
    if not target_sha:
        raise _PlanOpError("no milestone commit found", 404)

    reset = _git(root, "reset", "--hard", target_sha)
    if reset.returncode != 0:
        raise _PlanOpError(f"git reset failed: {reset.stderr.strip()}", 500)
    return {"resetTo": target_sha, "subject": subject}


# ── State ────────────────────────────────────────────────────────────────────

async def handle_plans_state(request: "web.Request") -> "web.Response":
    """Command-center snapshot: current phase + milestone progress + plan docs."""
    try:
        root = _resolve_root(_space_param(request))
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)

    def _read() -> dict[str, Any]:
        from navig.plans.current_phase_manager import CurrentPhaseManager

        state = CurrentPhaseManager(root).get_current_phase()
        return {
            "phase": _phase_payload(state) if state is not None else None,
            "progress": _progress_payload(root),
            "docs": _docs_payload(_plans_dir(root)),
        }

    try:
        return _ok(await asyncio.to_thread(_read))
    except Exception as exc:
        logger.exception("plans state failed")
        return _err(str(exc), 500)


# ── Docs ─────────────────────────────────────────────────────────────────────

async def handle_plans_doc_get(request: "web.Request") -> "web.Response":
    """Read one plan doc (path relative to the space's ``.navig/plans``)."""
    try:
        root = _resolve_root(_space_param(request))
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)
    plans_dir = _plans_dir(root)
    rel = request.rel_url.query.get("path", "")
    target = _confined_doc_path(plans_dir, rel)
    if target is None:
        return _err("path must stay inside .navig/plans", 400)
    if not target.is_file():
        return _err("plan doc not found", 404)

    def _read() -> dict[str, Any]:
        return {
            "path": target.relative_to(plans_dir.resolve()).as_posix(),
            "content": target.read_text(encoding="utf-8", errors="replace"),
            "mtime": target.stat().st_mtime,
        }

    try:
        return _ok(await asyncio.to_thread(_read))
    except Exception as exc:
        logger.exception("plans doc read failed")
        return _err(str(exc), 500)


async def handle_plans_doc_save(request: "web.Request") -> "web.Response":
    """Save (or create) a plan doc — atomic write, confined to ``.navig/plans``."""
    body = await _json(request) or {}
    try:
        root = _resolve_root(_space_param(request, body))
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)
    plans_dir = _plans_dir(root)
    rel = str(body.get("path") or "")
    content = body.get("content")
    if not isinstance(content, str):
        return _err("content must be a string")
    target = _confined_doc_path(plans_dir, rel)
    if target is None:
        return _err("path must stay inside .navig/plans", 400)
    if target.suffix.lower() != ".md":
        return _err("only .md plan docs can be saved")

    def _write() -> dict[str, Any]:
        from navig.core.yaml_io import atomic_write_text

        atomic_write_text(target, content)
        return {
            "path": target.relative_to(plans_dir.resolve()).as_posix(),
            "mtime": target.stat().st_mtime,
        }

    try:
        return _ok(await asyncio.to_thread(_write))
    except Exception as exc:
        logger.exception("plans doc save failed")
        return _err(str(exc), 500)


async def handle_plans_toggle_task(request: "web.Request") -> "web.Response":
    """Toggle a markdown checkbox (``- [ ]``/``- [x]``) on a 0-based line."""
    body = await _json(request) or {}
    try:
        root = _resolve_root(_space_param(request, body))
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)
    plans_dir = _plans_dir(root)
    line_no = body.get("line")
    if not isinstance(line_no, int) or isinstance(line_no, bool) or line_no < 0:
        return _err("line must be a non-negative integer (0-based)")
    target = _confined_doc_path(plans_dir, str(body.get("path") or ""))
    if target is None:
        return _err("path must stay inside .navig/plans", 400)
    if not target.is_file():
        return _err("plan doc not found", 404)

    def _toggle() -> dict[str, Any]:
        from navig.core.yaml_io import atomic_write_text

        content = target.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)
        if line_no >= len(lines):
            raise _PlanOpError(f"line {line_no} is out of range", 400)
        raw = lines[line_no]
        text = raw.rstrip("\r\n")
        ending = raw[len(text):]
        m = _CHECKBOX_RE.match(text)
        if m is None:
            raise _PlanOpError(f"line {line_no} is not a markdown checkbox", 400)
        checked = m.group(2) not in ("x", "X")  # new state after the toggle
        lines[line_no] = text[: m.start(2)] + ("x" if checked else " ") + text[m.end(2):] + ending
        new_content = "".join(lines)
        atomic_write_text(target, new_content)
        return {"checked": checked, "content": new_content, "mtime": target.stat().st_mtime}

    try:
        return _ok(await asyncio.to_thread(_toggle))
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)
    except Exception as exc:
        logger.exception("plans toggle-task failed")
        return _err(str(exc), 500)


# ── Phase lifecycle ──────────────────────────────────────────────────────────

async def _phase_mutation(
    request: "web.Request", body: dict, fn_name: str, *args: Any
) -> "web.Response":
    """Shared thin wrapper over a :class:`CurrentPhaseManager` mutation."""
    try:
        root = _resolve_root(_space_param(request, body))
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)

    def _run() -> dict[str, Any] | None:
        from navig.plans.current_phase_manager import CurrentPhaseManager

        state = getattr(CurrentPhaseManager(root), fn_name)(*args)
        return _phase_payload(state) if state is not None else None

    try:
        phase = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("plans phase %s failed", fn_name)
        return _err(str(exc), 500)
    if phase is None:
        return _err("no CURRENT_PHASE.md found (or the update failed)", 404)
    return _ok({"phase": phase})


async def handle_plans_phase_advance(request: "web.Request") -> "web.Response":
    """Install ``next_phase_file`` (rel to ``.navig/plans``) as the current phase."""
    body = await _json(request) or {}
    rel = str(body.get("next_phase_file") or "")
    if not rel:
        return _err(
            "next_phase_file is required (a path under .navig/plans, e.g. phases/phase_02.md)"
        )
    try:
        root = _resolve_root(_space_param(request, body))
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)
    target = _confined_doc_path(_plans_dir(root), rel)
    if target is None:
        return _err("next_phase_file must stay inside .navig/plans", 400)
    if not target.is_file():
        return _err("next_phase_file not found", 404)
    return await _phase_mutation(request, body, "advance_phase", target)


async def handle_plans_phase_block(request: "web.Request") -> "web.Response":
    body = await _json(request) or {}
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return _err("reason is required")
    return await _phase_mutation(request, body, "block_phase", reason)


async def handle_plans_phase_unblock(request: "web.Request") -> "web.Response":
    body = await _json(request) or {}
    return await _phase_mutation(request, body, "unblock_phase")


async def handle_plans_phase_complete(request: "web.Request") -> "web.Response":
    body = await _json(request) or {}
    return await _phase_mutation(request, body, "complete_phase")


# ── Milestone checkpoints (git) ──────────────────────────────────────────────

async def handle_plans_milestone_commit(request: "web.Request") -> "web.Response":
    """Checkpoint ``.navig/plans`` as a ``[navig] Milestone: <name>`` commit."""
    body = await _json(request) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return _err("name is required")
    try:
        root = _resolve_root(_space_param(request, body))
        result = await asyncio.to_thread(_commit_milestone_sync, root, name)
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)
    except Exception as exc:
        logger.exception("milestone commit failed")
        return _err(str(exc), 500)
    return _ok(result)


async def handle_plans_milestone_rollback(request: "web.Request") -> "web.Response":
    """HARD-DESTRUCTIVE: ``git reset --hard`` to the last milestone commit.

    Requires ``{"confirm": "ROLLBACK"}`` and a working tree with no
    uncommitted changes outside ``.navig/plans`` (user work is protected).
    """
    body = await _json(request) or {}
    if body.get("confirm") != "ROLLBACK":
        return _err('rollback is destructive — pass {"confirm": "ROLLBACK"} to proceed')
    try:
        root = _resolve_root(_space_param(request, body))
        result = await asyncio.to_thread(_rollback_milestone_sync, root)
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)
    except Exception as exc:
        logger.exception("milestone rollback failed")
        return _err(str(exc), 500)
    return _ok(result)


# ── Generate ─────────────────────────────────────────────────────────────────

_VISION_REL = "plans/VISION.md"  # the seed doc — the AI mode never writes it


def _generate_ai_sync(root: Path, vision_override: str, force: bool) -> dict[str, Any]:
    """Draft the plan suite from VISION.md via the agent layer (sync worker).

    Skips docs that already have content (unless *force*), never touches
    VISION.md itself, and keeps going when a single doc fails — one bad draft
    must not abort the rest of the suite.
    """
    from navig.agent.plan_drafter import PlanDraftUnavailableError, draft_plan_doc
    from navig.core.yaml_io import atomic_write_text
    from navig.plans.scaffold import suite_templates

    navig_dir = root / ".navig"
    suite = suite_templates()

    vision = (vision_override or "").strip()
    if not vision:
        vision_path = navig_dir / _VISION_REL
        try:
            if vision_path.is_file():
                vision = vision_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            vision = ""
    if not vision:
        raise _PlanOpError(
            "write a VISION.md first (.navig/plans/VISION.md) — or pass a "
            '"vision" string in the body — so the AI has a seed to plan from',
            400,
        )

    def _has_content(p: Path) -> bool:
        try:
            return p.is_file() and bool(p.read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            return False

    existing = {rel for rel in suite if (navig_dir / rel).is_file()}
    created: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    for rel, template in suite.items():
        if rel == _VISION_REL:
            continue  # the vision is the input, never an output
        target = navig_dir / rel
        if _has_content(target) and not force:
            skipped.append(rel)
            continue
        doc_name = rel.removeprefix("plans/")
        try:
            text = draft_plan_doc(
                doc_name=doc_name,
                template=template,
                vision=vision,
                existing_docs=sorted(
                    e.removeprefix("plans/") for e in existing if e != rel
                ),
                project_name=root.name,
            )
        except PlanDraftUnavailableError as exc:
            if not created:
                raise _PlanOpError(str(exc), 503) from exc
            # Backend went away mid-run — every remaining doc would fail the
            # same way; record it once and return the partial result.
            errors.append({"doc": rel, "error": str(exc)})
            break
        except Exception as exc:  # one doc failing must not abort the rest
            logger.warning("ai plan draft failed for %s: %s", rel, exc)
            errors.append({"doc": rel, "error": str(exc)})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, text if text.endswith("\n") else text + "\n")
        created.append(rel)
        existing.add(rel)
    return {"mode": "ai", "created": created, "skipped": skipped, "errors": errors}


async def handle_plans_generate(request: "web.Request") -> "web.Response":
    """Generate the standard plan-doc suite.

    mode:"scaffold" (default) writes the templates; mode:"ai" reads the
    space's VISION.md (or a ``vision`` string in the body) and drafts real
    starter content for each missing doc via the agent layer. Existing
    non-empty docs are skipped (``force:true`` redrafts them); VISION.md
    itself is never written by the AI mode.
    """
    body = await _json(request) or {}
    mode = str(body.get("mode") or "scaffold").strip().lower()
    if mode not in ("scaffold", "ai"):
        return _err(f"unsupported mode: {mode} (use 'scaffold' or 'ai')")
    try:
        root = _resolve_root(_space_param(request, body))
    except _PlanOpError as exc:
        return _err(str(exc), exc.status)

    if mode == "ai":
        vision_override = str(body.get("vision") or "")
        force = bool(body.get("force"))
        try:
            return _ok(
                await asyncio.to_thread(_generate_ai_sync, root, vision_override, force)
            )
        except _PlanOpError as exc:
            return _err(str(exc), exc.status)
        except Exception as exc:
            logger.exception("plans ai-generate failed")
            return _err(str(exc), 500)

    def _run() -> dict[str, Any]:
        from navig.plans.scaffold import scaffold_plans_structure, template_paths

        navig_dir = root / ".navig"
        skipped = [
            p.relative_to(navig_dir).as_posix() for p in template_paths(root) if p.exists()
        ]
        created = [
            p.relative_to(navig_dir).as_posix() for p in scaffold_plans_structure(root)
        ]
        return {"created": created, "skipped": skipped}

    try:
        return _ok(await asyncio.to_thread(_run))
    except Exception as exc:
        logger.exception("plans generate failed")
        return _err(str(exc), 500)
