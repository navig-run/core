"""
navig.plans.context — PlanContext unified read surface.

Provides the AI agent with complete situational awareness across plans,
wiki, docs, inbox, and optionally MCP resources.  All reads are local,
synchronous, and side-effect-free.

Usage::

    from navig.plans.context import PlanContext

    ctx = PlanContext()
    snapshot = ctx.gather()       # default space
    snapshot = ctx.gather("devops")  # specific space
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.plans.frontmatter import (
    FRONTMATTER_RE as _FRONTMATTER_RE,
)
from navig.plans.frontmatter import (
    _safe_read,
)
from navig.plans.frontmatter import (
    first_h1 as _first_h1,
)
from navig.plans.frontmatter import (
    parse_frontmatter as _parse_frontmatter,
)
from navig.platform import paths

logger = logging.getLogger(__name__)


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _extract_md_section(text: str, heading: str) -> str:
    """Return the body under a ``## Heading`` up to the next ``## `` (exclusive)."""
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.strip() == heading:
            capturing = True
            continue
        if capturing:
            if line.startswith("## "):
                break
            out.append(line)
    return "\n".join(out).strip()


# ─────────────────────────────────────────────────────────────
# PlanContext
# ─────────────────────────────────────────────────────────────

@dataclass
class PlanContext:
    """Unified read surface for the navig AI agent.

    Gathers situational context from plans, wiki, docs, inbox, and
    optionally MCP resources.  All methods are synchronous and safe
    to call from any thread.

    Parameters
    ----------
    cwd:
        Working directory for project-level resolution.  Defaults to
        ``Path.cwd()``.
    """

    space_root: str = field(default_factory=lambda: str(paths.config_dir() / "spaces"))
    mcp_enabled: bool = False
    cwd: Path = field(default_factory=Path.cwd)

    def gather(self, space: str | None = None) -> dict[str, Any]:
        """Build a complete context snapshot for the given space.

        Parameters
        ----------
        space:
            Space name (e.g. ``"devops"``).  When *None*, the active
            space is resolved via ``get_default_space()``.

        Returns
        -------
        dict[str, Any]
            Keys: ``current_phase``, ``dev_plan``, ``wiki``, ``docs``,
            ``inbox_unread``, ``mcp_resources``, ``errors``.
        """
        errors: dict[str, str] = {}
        space_name = self._resolve_space_name(space)
        space_path = self._resolve_space_path(space_name, errors)

        # current_phase + dev_plan are quick local file reads; do them
        # eagerly so we can derive phase_title for the wiki search.
        current_phase = self._safe_source_call(
            "current_phase",
            self._read_current_phase,
            errors,
            space_path,
        )
        dev_plan = self._safe_source_call(
            "dev_plan",
            self._read_dev_plan,
            errors,
            space_path,
        )
        phase_title = self._extract_phase_title(current_phase) or space_name

        # Slow / network-bound sources — wiki search + MCP resources can
        # block up to 2s each; docs + inbox are local but disk-bound. Run
        # them concurrently so total wall time = max(any one source)
        # instead of sum. On the warm path this saves 5+ seconds off the
        # first-message latency that the bot user perceives as "typing
        # forever".
        from concurrent.futures import ThreadPoolExecutor as _TPE

        with _TPE(max_workers=4) as ex:
            f_wiki = ex.submit(
                self._safe_source_call,
                "wiki",
                self._search_wiki,
                errors,
                phase_title,
                space_name,
            )
            f_docs = ex.submit(
                self._safe_source_call,
                "docs",
                self._find_docs,
                errors,
                space_path,
            )
            f_inbox = ex.submit(
                self._safe_source_call,
                "inbox_unread",
                self._count_inbox_unread,
                errors,
                space_path,
            )
            f_project_docs = ex.submit(
                self._safe_source_call,
                "project_docs",
                self._read_project_docs,
                errors,
                space_path,
            )
            if self.mcp_enabled:
                f_mcp = ex.submit(
                    self._safe_source_call,
                    "mcp_resources",
                    self._gather_mcp_resources,
                    errors,
                    space_name,
                )
            else:
                f_mcp = None

            wiki = f_wiki.result()
            docs = f_docs.result()
            inbox_unread = f_inbox.result()
            project_docs = f_project_docs.result()
            mcp_resources = f_mcp.result() if f_mcp is not None else None

        return {
            "current_phase": current_phase,
            "dev_plan": dev_plan,
            "wiki": wiki,
            "docs": docs,
            "project_docs": project_docs,
            "inbox_unread": inbox_unread,
            "mcp_resources": mcp_resources,
            "errors": errors,
        }

    # ─────────────────────────────────────────────────────────
    # Private readers
    # ─────────────────────────────────────────────────────────

    def _read_current_phase(self, space_path: Path) -> str | None:
        """Read ``CURRENT_PHASE.md`` from the space directory.

        Checks the space root first, then falls back to
        ``.navig/plans/phases/CURRENT_PHASE.md``.
        """
        try:
            # Space root (canonical for spaces layout)
            cp = space_path / "CURRENT_PHASE.md"
            if not cp.is_file():
                # Fallback: plans/phases directory
                cp = self.cwd / ".navig" / "plans" / "phases" / "CURRENT_PHASE.md"
            if not cp.is_file():
                # Last resort: .navig root
                cp = self.cwd / ".navig" / "CURRENT_PHASE.md"
            if not cp.is_file():
                return None

            text = _safe_read(cp)
            return text or None
        except Exception:
            raise

    def _read_dev_plan(self, space_path: Path) -> str | None:
        """Read ``DEV_PLAN.md`` from the space root or project plans directory."""
        try:
            candidates = [
                space_path / "DEV_PLAN.md",
                self.cwd / ".navig" / "plans" / "DEV_PLAN.md",
            ]
            for dev_plan in candidates:
                if dev_plan.is_file():
                    text = _safe_read(dev_plan)
                    return text or None
            return None
        except Exception:
            raise

    def _search_wiki(self, query: str, space_name: str) -> list[dict[str, str]]:
        """Search the project wiki for content relevant to the active phase."""
        try:
            from navig.agent.tools import wiki_tools

            results = self._call_with_timeout(
                wiki_tools.search,
                timeout_sec=2.0,
                query=query,
                space=space_name,
                limit=5,
            )
            if not results:
                logger.warning("PlanContext wiki search returned empty for space=%s", space_name)
                return []

            out: list[dict[str, str]] = []
            for result in results[:5]:
                out.append(
                    {
                        "title": str(result.get("title", "")),
                        "excerpt": str(result.get("excerpt", "")),
                        "path": str(result.get("path", "")),
                    }
                )
            return out
        except Exception:
            logger.warning("PlanContext wiki search failed for space=%s", space_name)
            return []

    def _find_docs(self, space_path: Path) -> list[str]:
        """Return matching docs file paths."""
        try:
            results: list[str] = []
            for root_doc in ("README.md", "ROADMAP.md"):
                p = self.cwd / root_doc
                if p.is_file():
                    results.append(str(p.relative_to(self.cwd)))
            for doc in sorted((self.cwd / "docs").rglob("*.md")):
                results.append(str(doc.relative_to(self.cwd)))
            # Include space-level docs if they exist
            space_docs = space_path / "docs"
            if space_docs.is_dir():
                for doc in sorted(space_docs.rglob("*.md")):
                    results.append(str(doc))
            return results[:200]
        except Exception:
            raise

    def _read_project_docs(self, space_path: Path) -> dict[str, str] | None:
        """Read the *bodies* of VISION/ROADMAP + the deferred/after-mvp plan
        sections so context adapts to the current project — and so promoted inbox
        bullets (which land in these files) flow back into the LLM system prompt.

        Each value is capped to keep the prompt lean. Space dir wins; otherwise
        the project root / ``.navig/plans``.
        """
        out: dict[str, str] = {}
        cap = 1500
        for key, fname in (("vision", "VISION.md"), ("roadmap", "ROADMAP.md")):
            for cand in (
                space_path / fname,
                self.cwd / fname,
                self.cwd / ".navig" / "plans" / fname,
            ):
                if cand.is_file():
                    txt = (_safe_read(cand) or "").strip()
                    if txt:
                        out[key] = txt[:cap]
                    break
        for cand in (space_path / "DEV_PLAN.md", self.cwd / ".navig" / "plans" / "DEV_PLAN.md"):
            if cand.is_file():
                dev = _safe_read(cand) or ""
                deferred = _extract_md_section(dev, "## Deferred / Later")
                after = _extract_md_section(dev, "## After MVP")
                if deferred:
                    out["deferred"] = deferred[:cap]
                if after:
                    out["after_mvp"] = after[:cap]
                break
        return out or None

    def _count_inbox_unread(self, space_path: Path) -> int:
        """Count unprocessed ``.md`` files in ``.navig/inbox/``."""
        try:
            inbox_dir = self.cwd / ".navig" / "inbox"
            if not inbox_dir.is_dir():
                inbox_dir = space_path / ".navig" / "inbox"
            if not inbox_dir.is_dir():
                return 0
            return sum(
                1
                for f in inbox_dir.iterdir()
                if f.is_file() and f.suffix == ".md"
            )
        except Exception:
            raise

    def _gather_mcp_resources(self, space_name: str) -> list[dict[str, str]]:
        """Attempt to list MCP resources from connected servers.

        This is best-effort — returns empty list if no MCP servers
        are available or the operation times out.
        """
        try:
            from navig.agent.mcp_client import get_mcp_pool

            pool = get_mcp_pool()
            resources = self._call_with_timeout(
                pool.list_resources_sync,
                timeout_sec=2.0,
                timeout=2.0,
            )
            out: list[dict[str, str]] = []
            for res in resources:
                uri = str(res.get("uri", ""))
                if space_name.lower() in uri.lower():
                    out.append(
                        {
                            "uri": uri,
                            "name": str(res.get("name", "")),
                            "description": str(res.get("description", "")),
                        }
                    )
            return out[:10]
        except Exception:
            logger.warning("PlanContext MCP resources failed for space=%s", space_name)
            return []

    def _resolve_space_name(self, space: str | None) -> str:
        if space:
            return space
        try:
            from navig.spaces.resolver import get_default_space

            return get_default_space()
        except Exception:
            return "default"

    def _resolve_space_path(self, space_name: str, errors: dict[str, str]) -> Path:
        base = Path(self.space_root)
        candidate = base / space_name
        if candidate.is_dir():
            return candidate
        try:
            from navig.spaces.resolver import resolve_space

            resolved = resolve_space(space_name, cwd=self.cwd)
            return resolved.path
        except Exception as exc:
            errors["space"] = str(exc)
            return candidate

    def _safe_source_call(
        self,
        source: str,
        fn: Any,
        errors: dict[str, str],
        *args: Any,
    ) -> Any:
        try:
            return fn(*args)
        except Exception as exc:
            logger.warning("PlanContext source '%s' failed: %s", source, exc)
            errors[source] = str(exc)
            return None

    @staticmethod
    def _call_with_timeout(fn: Any, timeout_sec: float, *args: Any, **kwargs: Any) -> Any:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn, *args, **kwargs)
            try:
                return future.result(timeout=timeout_sec)
            except FutureTimeoutError as exc:
                raise TimeoutError(f"Timed out after {timeout_sec}s") from exc

    @staticmethod
    def _extract_phase_title(current_phase_text: str | None) -> str:
        if not current_phase_text:
            return ""
        fm = _parse_frontmatter(current_phase_text)
        if fm.get("title"):
            return fm["title"]
        # First non-frontmatter line
        body = current_phase_text
        match = _FRONTMATTER_RE.match(current_phase_text)
        if match:
            body = current_phase_text[match.end():]
        for line in body.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped.lstrip("# ").strip()
        return ""

    # ─────────────────────────────────────────────────────────
    # Formatting
    # ─────────────────────────────────────────────────────────

    def format_for_prompt(self, snapshot: dict[str, Any]) -> str:
        """Format a context snapshot as a compact string for system prompts.

        Parameters
        ----------
        snapshot:
            Output of :meth:`gather`.

        Returns
        -------
        str
            Human-readable context block suitable for LLM system prompts.
        """
        parts: list[str] = []

        # Current phase
        phase_text = snapshot.get("current_phase")
        if phase_text:
            phase = _parse_frontmatter(phase_text)
            title = self._extract_phase_title(phase_text) or "Untitled"
            completion_raw = phase.get("completion_pct", "0")
            try:
                completion = float(completion_raw)
            except (TypeError, ValueError):
                completion = 0.0
            parts.append(
                f"## Current Phase\n"
                f"**{title}** "
                f"(Phase {phase.get('phase', '?')}) — "
                f"{phase.get('status', 'active')}, "
                f"{completion:.0f}% complete"
            )
            if phase.get("milestone"):
                parts[-1] += f"\nMilestone: {phase['milestone']}"

        # Dev plan summary
        dev_text = snapshot.get("dev_plan")
        if dev_text:
            open_count = len(re.findall(r"^\s*-\s*\[ \]", dev_text, re.MULTILINE))
            done_count = len(re.findall(r"^\s*-\s*\[[xX]\]", dev_text, re.MULTILINE))
            total_count = open_count + done_count
            completion = (done_count / total_count * 100) if total_count else 0.0
            parts.append(
                f"## Dev Plan\n"
                f"{open_count} open / "
                f"{done_count} done / "
                f"{total_count} total tasks "
                f"({completion:.0f}% complete)"
            )

        # Vision & roadmap (incl. promoted inbox items) — adapts to current project
        pdocs = snapshot.get("project_docs") or {}
        if pdocs:
            vr: list[str] = ["## Vision & Roadmap"]
            if pdocs.get("vision"):
                vr.append(f"**Vision**\n{_truncate(pdocs['vision'], 600)}")
            if pdocs.get("roadmap"):
                vr.append(f"**Roadmap**\n{_truncate(pdocs['roadmap'], 800)}")
            if pdocs.get("after_mvp"):
                vr.append(f"**After MVP**\n{_truncate(pdocs['after_mvp'], 400)}")
            if pdocs.get("deferred"):
                vr.append(f"**Deferred / Later**\n{_truncate(pdocs['deferred'], 400)}")
            if len(vr) > 1:
                parts.append("\n\n".join(vr))

        # Wiki hits
        wiki = snapshot.get("wiki", [])
        if wiki:
            lines = ["## Related Wiki Pages"]
            for w in wiki[:3]:
                lines.append(f"- {w.get('title', 'Untitled')}: {w.get('excerpt', '')}")
            parts.append("\n".join(lines))

        # Inbox
        inbox = snapshot.get("inbox_unread", 0)
        if inbox:
            parts.append(f"## Inbox\n{inbox} unread item(s) in inbox")

        # Errors (non-empty only)
        errs = snapshot.get("errors", {})
        if errs:
            parts.append(
                "## Context Warnings\n"
                + "\n".join(f"- {k}: {v}" for k, v in errs.items())
            )

        if not parts:
            return ""

        return "## Plan Context\n\n" + "\n\n".join(parts)
