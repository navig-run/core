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

from navig.platform import paths

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?", re.MULTILINE)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _safe_read(path: Path) -> str:
    """Read a file as UTF-8, returning empty string on failure."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Return a dict of key→value from ``---``-delimited frontmatter."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        values[key.strip()] = val.strip()
    return values


def _first_h1(text: str) -> str:
    """Extract the first ``# Heading`` from Markdown text."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


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
        wiki = self._safe_source_call(
            "wiki",
            self._search_wiki,
            errors,
            phase_title,
            space_name,
        )
        docs = self._safe_source_call("docs", self._find_docs, errors, space_path)
        inbox_unread = self._safe_source_call(
            "inbox_unread",
            self._count_inbox_unread,
            errors,
            space_path,
        )

        mcp_resources: list[dict[str, str]] | None
        if self.mcp_enabled:
            mcp_resources = self._safe_source_call(
                "mcp_resources",
                self._gather_mcp_resources,
                errors,
                space_name,
            )
        else:
            mcp_resources = None

        return {
            "current_phase": current_phase,
            "dev_plan": dev_plan,
            "wiki": wiki,
            "docs": docs,
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
