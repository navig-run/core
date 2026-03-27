"""
ContextBuilder -- Assembles relevant context before requests reach the LLM router.

Pipeline position:
    Caller -> ContextBuilder -> ModeRouter -> ModelRouter -> Provider

Produces a single JSON-serializable ``context: dict`` consumed read-only by
all downstream components.  ModeRouter and ModelRouter must NEVER fetch
memory directly -- they only inspect the dict passed to them.

Sections assembled:
    conversation_history  -- last N messages from ConversationStore
    key_facts             -- ranked persistent user facts (preferences, decisions, identity)
    workspace_notes       -- content from .navig/workspace/ and .navig/plans/
    kb_snippets           -- top K semantic search results via RAGPipeline / KnowledgeBase
    metadata              -- user profile, active host/app info
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from navig.workspace_ownership import USER_WORKSPACE_DIR

logger = logging.getLogger("navig.memory.context_builder")


# ---------------------------------------------------------------------------
# Default configuration constants
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "conversation_history_limit": 10,
    "include_key_facts": True,
    "key_facts_max_tokens": 600,
    "kb_snippets_top_k": 3,
    "kb_min_input_length": 20,
    "include_workspace_notes": False,
    "include_memory_logs": False,
    "include_api_snapshots": True,
    "include_project_index": True,
    "project_index_top_k": 10,
    "project_index_max_chars": 12_000,
    "api_snapshot_max_age_minutes": 60,
    "api_snapshot_max_entries": 5,
    "max_context_chars": 32_000,
}


class _EmptyContextDict(dict):
    """Compatibility view for legacy `key_facts` access on empty contexts."""

    def __contains__(self, key: object) -> bool:  # type: ignore[override]
        return key == "key_facts" or super().__contains__(key)

    def __getitem__(self, key: str) -> Any:  # type: ignore[override]
        if key == "key_facts":
            return ""
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        if key == "key_facts":
            return ""
        return super().get(key, default)


# AUDIT self-check: Correct implementation? yes - preserves both old/new empty-context contracts.
# AUDIT self-check: Break callers? no - key visibility is additive for legacy checks.
# AUDIT self-check: Simpler alternative? yes - a tiny compatibility dict avoids broader refactors.
EMPTY_CONTEXT: dict[str, Any] = _EmptyContextDict(
    {
        "conversation_history": [],
        "workspace_notes": [],
        "kb_snippets": [],
        "project_files": [],
        "api_snapshots": [],
        "stale_sources": [],
        "metadata": {},
    }
)


# ---------------------------------------------------------------------------
# Adapter helpers -- thin wrappers over existing memory APIs
# ---------------------------------------------------------------------------


def _retrieve_key_facts(user_input: str, max_tokens: int = 600) -> str:
    """
    Adapter: Retrieve ranked key facts formatted for LLM context injection.

    Uses FactRetriever to find relevant persistent memories (preferences,
    decisions, identity, technical context) and format them as a text block.

    Returns empty string if no facts or on any failure.
    """
    try:
        from navig.memory.fact_retriever import FactRetriever
        from navig.memory.key_facts import get_key_fact_store

        store = get_key_fact_store()
        retriever = FactRetriever(store)
        result = retriever.retrieve(user_input, max_tokens=max_tokens)
        return result.formatted
    except ImportError:
        logger.debug("Key facts module not available")
        return ""
    except Exception as exc:
        logger.debug("Key facts retrieval failed: %s", exc)
        return ""


def _get_recent_messages(session_id: str, limit: int) -> list[dict[str, Any]]:
    """
    Adapter: ConversationStore.get_history(session_key, limit) -> list[dict].

    The existing ``ConversationStore.get_history`` returns ``list[Message]``
    with ``.to_dict()`` serialization.  We normalise into plain dicts here
    so the context dict stays JSON-serializable and decoupled from dataclass
    internals.

    If ConversationStore is unavailable (no DB, import error), returns [].
    """
    try:
        from navig.memory.conversation import ConversationStore
        from navig.memory.manager import _get_memory_dir

        db_path = _get_memory_dir() / "memory.db"
        if not db_path.exists():
            return []

        store = ConversationStore(db_path)
        messages = store.get_history(session_id, limit=limit)
        return [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp.isoformat(),
            }
            for m in messages
        ]
    except Exception as exc:
        logger.debug("_get_recent_messages failed: %s", exc)
        return []


def _search_knowledge(query: str, top_k: int) -> list[dict[str, Any]]:
    """
    Adapter: KnowledgeBase.search / RAGPipeline.retrieve -> list[dict].

    Tries RAGPipeline first (richer context), then falls back to
    KnowledgeBase.search.  Returns a list of dicts with at least
    ``content``, ``source``, and ``score`` keys.

    If neither is available, returns [].
    """
    # Try RAGPipeline
    try:
        from navig.memory.rag import RAGConfig, RAGPipeline

        config = RAGConfig(
            max_knowledge_entries=top_k,
            include_history=False,
            include_files=False,
            include_knowledge=True,
        )
        rag = RAGPipeline(config=config)

        # RAGPipeline needs a KnowledgeBase instance
        try:
            from navig.memory.knowledge_base import KnowledgeBase
            from navig.memory.manager import _get_memory_dir

            kb_path = _get_memory_dir() / "knowledge.db"
            if kb_path.exists():
                kb = KnowledgeBase(db_path=kb_path, embedding_provider=None)
                rag.knowledge_base = kb

                result = rag.retrieve(query=query)
                if result.knowledge_entries > 0:
                    # Parse markdown-formatted knowledge back into structured dicts
                    return _parse_rag_knowledge(result.context, top_k)
        except Exception as exc:
            logger.debug("RAGPipeline KB search failed: %s", exc)

    except ImportError:
        pass  # optional dependency not installed; feature disabled

    # Fallback: direct KnowledgeBase.search (text-only, no embeddings)
    try:
        from navig.memory.knowledge_base import KnowledgeBase
        from navig.memory.manager import _get_memory_dir

        kb_path = _get_memory_dir() / "knowledge.db"
        if not kb_path.exists():
            return []

        kb = KnowledgeBase(db_path=kb_path, embedding_provider=None)
        results = kb.search(query, limit=top_k)
        return [
            {
                "content": entry.content[:500],
                "source": entry.source,
                "key": entry.key,
                "tags": entry.tags,
                "score": round(score, 3),
            }
            for entry, score in results
        ]
    except Exception as exc:
        logger.debug("KnowledgeBase.search failed: %s", exc)
        return []


def _load_api_snapshots(
    max_age_minutes: int = 60,
    max_entries: int = 5,
    workspace: str = "default",
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Adapter: Load recent API snapshots and detect stale data sources.

    Returns:
        (snapshot_dicts, stale_tool_names)
        snapshot_dicts: LLM-safe dicts with normalized + tool + fetched_at
        stale_tool_names: Tools whose latest snapshot exceeds max_age_minutes
    """
    try:
        from navig.memory.snapshot import load_snapshot_policies, load_snapshots

        policies = load_snapshot_policies()
        stored_tools = [t for t, p in policies.items() if p.store]

        snap_dicts: list[dict[str, Any]] = []
        stale: list[str] = []

        for tool_name in stored_tools:
            entries = load_snapshots(
                workspace=workspace,
                tool=tool_name,
                max_age_minutes=max_age_minutes,
                limit=1,
            )
            if entries:
                e = entries[0]
                snap_dicts.append(
                    {
                        "tool": e.tool,
                        "data": e.normalized,
                        "fetched_at": e.timestamp,
                    }
                )
            else:
                stale.append(tool_name)

        return snap_dicts[:max_entries], stale

    except Exception as exc:
        logger.debug("_load_api_snapshots failed: %s", exc)
        return [], []


def _parse_rag_knowledge(context: str, top_k: int) -> list[dict[str, Any]]:
    """Extract structured snippets from RAGPipeline markdown context."""
    snippets: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    for line in context.splitlines():
        if line.startswith("### "):
            if current.get("content"):
                snippets.append(current)
                if len(snippets) >= top_k:
                    break
            current = {
                "key": line[4:].strip(),
                "content": "",
                "source": "rag",
                "score": 1.0,
            }
        elif current:
            current["content"] += line + "\n"

    if current.get("content") and len(snippets) < top_k:
        current["content"] = current["content"].strip()
        snippets.append(current)

    return snippets


def _search_project_index(
    query: str,
    project_root: Path,
    top_k: int = 10,
    max_chars: int = 12_000,
) -> list[dict[str, Any]]:
    """
    Adapter: ProjectIndexer.search -> list[dict].

    Searches the project source code index and returns ranked code/doc
    chunks.  If the index doesn't exist or ProjectIndexer is unavailable,
    returns [].
    """
    try:
        from navig.memory.project_indexer import ProjectIndexConfig, ProjectIndexer

        db_path = project_root / ".navig" / ProjectIndexer.DB_NAME
        if not db_path.exists():
            return []

        config = ProjectIndexConfig(max_results=top_k, max_chars=max_chars)
        indexer = ProjectIndexer(project_root, config=config)
        try:
            results = indexer.search(query, top_k=top_k, max_chars=max_chars)
            return [
                {
                    "file": r.file_path,
                    "lines": f"{r.start_line}-{r.end_line}",
                    "type": r.content_type,
                    "section": r.section_title,
                    "content": r.content,
                    "score": r.score,
                }
                for r in results
            ]
        finally:
            indexer.close()

    except Exception as exc:
        logger.debug("_search_project_index failed: %s", exc)
        return []


def _read_workspace_notes(
    project_root: Path | None = None,
    include_memory_logs: bool = False,
    max_chars: int = 2000,
) -> list[str]:
    """
    Read .navig/plans/CURRENT_PHASE.md and optionally .navig/memory/*.md.

    Returns a list of strings, each representing one file's content,
    truncated so total does not exceed ``max_chars``.
    """
    if project_root is None:
        project_root = Path.cwd()

    navig_dir = project_root / ".navig"
    if not navig_dir.is_dir():
        return []

    notes: list[str] = []
    remaining = max_chars

    # 1. CURRENT_PHASE.md (always if include_workspace_notes is on)
    phase_file = navig_dir / "plans" / "CURRENT_PHASE.md"
    if phase_file.is_file() and remaining > 0:
        try:
            text = phase_file.read_text(encoding="utf-8", errors="replace")
            chunk = text[:remaining]
            notes.append("[CURRENT_PHASE.md]\n" + chunk)
            remaining -= len(chunk)
        except Exception as exc:
            logger.debug("Failed to read %s: %s", phase_file, exc)

    # 2. .navig/memory/*.md -- most recent 2 by mtime
    if include_memory_logs and remaining > 0:
        mem_dir = navig_dir / "memory"
        if mem_dir.is_dir():
            md_files = sorted(
                mem_dir.glob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for md_file in md_files[:2]:
                if remaining <= 0:
                    break
                try:
                    text = md_file.read_text(encoding="utf-8", errors="replace")
                    chunk = text[:remaining]
                    notes.append("[" + md_file.name + "]\n" + chunk)
                    remaining -= len(chunk)
                except Exception as exc:
                    logger.debug("Failed to read %s: %s", md_file, exc)

    return notes


def _collect_metadata(project_root: Path | None = None) -> dict[str, Any]:
    """
    Assemble metadata dict: user profile excerpt, active host/app.

    Sources:
        - .navig/workspace/USER.md (first 500 chars)
        - config_manager.get_active_host()
        - config_manager.get_active_app()
    """
    meta: dict[str, Any] = {}

    # User profile snippet
    if project_root is None:
        project_root = Path.cwd()

    user_md_candidates = [
        USER_WORKSPACE_DIR / "USER.md",
        project_root / ".navig" / "workspace" / "USER.md",
    ]
    for user_md in user_md_candidates:
        if user_md.is_file():
            try:
                text = user_md.read_text(encoding="utf-8", errors="replace")
                meta["user_profile_snippet"] = text[:500]
                break
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    # Active host / app from ConfigManager
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        meta["active_host"] = cm.get_active_host() or None
        meta["active_app"] = cm.get_active_app() or None
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    return meta


# ---------------------------------------------------------------------------
# ContextBuilder
# ---------------------------------------------------------------------------


class ContextBuilder:
    """
    Assembles relevant context (conversation history, workspace notes,
    knowledge-base snippets, metadata) into a single JSON-serializable dict.

    The returned dict is consumed **read-only** by ModeRouter and ModelRouter.

    Constructor accepts a ``config`` dict (matching the ``context_builder:``
    section of config.yaml) with fallback defaults for every key.

    Usage::

        builder = ContextBuilder()
        ctx = builder.build_context("How do I deploy?", {"enable_kb": True}, "session-123")
        # ctx keys: conversation_history, workspace_notes, kb_snippets, metadata
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        project_root: Path | None = None,
    ):
        """
        Args:
            config: Dict matching ``context_builder:`` YAML keys.  Missing keys
                    fall back to ``_DEFAULTS``.  If *None*, attempts to read
                    from ``config.yaml`` automatically.
            project_root: Root directory containing ``.navig/`` (defaults to cwd).
        """
        if config is None:
            config = self._load_from_config_yaml()

        self._cfg: dict[str, Any] = {**_DEFAULTS, **(config or {})}
        self.project_root: Path = project_root or Path.cwd()

    # -- public API ---------------------------------------------------------

    def build_context(
        self,
        user_input: str,
        caller_info: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Build the full context dict for the LLM pipeline.

        Args:
            user_input:  The raw user prompt / question.
            caller_info: Caller-provided flags.  Recognised keys:
                         ``enable_kb`` (bool, default True),
                         ``include_workspace`` (bool, default from config).
            session_id:  Conversation session identifier (optional).

        Returns:
            JSON-serializable dict with keys:
                conversation_history, workspace_notes, kb_snippets, metadata
        """
        if not self._cfg.get("enabled", True):
            return dict(EMPTY_CONTEXT)

        caller_info = caller_info or {}
        ctx: dict[str, Any] = {
            "conversation_history": [],
            "key_facts": "",
            "workspace_notes": [],
            "kb_snippets": [],
            "project_files": [],
            "api_snapshots": [],
            "stale_sources": [],
            "metadata": {},
        }
        budget = int(self._cfg["max_context_chars"])

        # -- 1. conversation_history -----------------------------------------
        if session_id:
            limit = int(self._cfg["conversation_history_limit"])
            messages = _get_recent_messages(session_id, limit)
            ctx["conversation_history"] = messages
            budget -= self._estimate_chars(messages)

        # -- 2. metadata (always) -------------------------------------------
        ctx["metadata"] = _collect_metadata(self.project_root)
        budget -= self._estimate_chars(ctx["metadata"])

        # -- 2b. key_facts (persistent user memory) -------------------------
        include_kf = caller_info.get(
            "include_key_facts",
            self._cfg.get("include_key_facts", True),
        )
        if include_kf and budget > 0:
            kf_max_tokens = int(self._cfg.get("key_facts_max_tokens", 600))
            kf_text = _retrieve_key_facts(user_input, kf_max_tokens)
            if kf_text:
                ctx["key_facts"] = kf_text
                budget -= len(kf_text)

        # -- 3. kb_snippets -------------------------------------------------
        enable_kb = caller_info.get("enable_kb", True)
        min_len = int(self._cfg["kb_min_input_length"])
        if enable_kb and len(user_input) >= min_len and budget > 0:
            top_k = int(self._cfg["kb_snippets_top_k"])
            snippets = _search_knowledge(user_input, top_k)
            ctx["kb_snippets"] = snippets
            budget -= self._estimate_chars(snippets)

        # -- 3b. project_files (source code index) -------------------------
        include_project = caller_info.get(
            "include_project_index",
            self._cfg.get("include_project_index", True),
        )
        if include_project and len(user_input) >= min_len and budget > 0:
            pi_top_k = int(self._cfg.get("project_index_top_k", 10))
            pi_max_chars = min(
                int(self._cfg.get("project_index_max_chars", 12_000)),
                budget,
            )
            project_chunks = _search_project_index(
                user_input,
                self.project_root,
                top_k=pi_top_k,
                max_chars=pi_max_chars,
            )
            ctx["project_files"] = project_chunks
            budget -= self._estimate_chars(project_chunks)

        # -- 4. workspace_notes ---------------------------------------------
        include_ws = caller_info.get(
            "include_workspace",
            self._cfg.get("include_workspace_notes", False),
        )
        if include_ws and budget > 0:
            notes = _read_workspace_notes(
                project_root=self.project_root,
                include_memory_logs=self._cfg.get("include_memory_logs", False),
                max_chars=min(2000, budget),
            )
            ctx["workspace_notes"] = notes
            budget -= self._estimate_chars(notes)

        # -- 5. api_snapshots ------------------------------------------------
        include_snap = caller_info.get(
            "include_api_snapshots",
            self._cfg.get("include_api_snapshots", True),
        )
        if include_snap and budget > 0:
            max_age = int(self._cfg.get("api_snapshot_max_age_minutes", 60))
            max_entries = int(self._cfg.get("api_snapshot_max_entries", 5))
            snap_data, stale = _load_api_snapshots(
                max_age_minutes=max_age,
                max_entries=max_entries,
            )
            ctx["api_snapshots"] = snap_data
            ctx["stale_sources"] = stale
            budget -= self._estimate_chars(snap_data)

        # -- hard cap -------------------------------------------------------
        ctx = self._enforce_cap(ctx, int(self._cfg["max_context_chars"]))

        return ctx

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _estimate_chars(obj: Any) -> int:
        """Rough char-count for budget tracking."""
        if isinstance(obj, str):
            return len(obj)
        try:
            return len(json.dumps(obj, default=str))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _enforce_cap(ctx: dict[str, Any], max_chars: int) -> dict[str, Any]:
        """Truncate sections if total serialized size exceeds cap."""
        total = 0
        try:
            total = len(json.dumps(ctx, default=str))
        except (TypeError, ValueError):
            return ctx

        if total <= max_chars:
            return ctx

        # Trim workspace_notes first, then key_facts, then project_files, then kb_snippets, then history
        for key in (
            "workspace_notes",
            "key_facts",
            "project_files",
            "kb_snippets",
            "conversation_history",
        ):
            if total <= max_chars:
                break
            section = ctx.get(key)
            if not section:
                continue

            if isinstance(section, str):
                # key_facts is a formatted string — truncate or clear
                removed_len = len(section)
                ctx[key] = ""
                total -= removed_len
            elif isinstance(section, list):
                while section and total > max_chars:
                    removed = section.pop()
                    try:
                        total -= len(json.dumps(removed, default=str))
                    except (TypeError, ValueError):
                        pass

        return ctx

    @staticmethod
    def _load_from_config_yaml() -> dict[str, Any]:
        """Attempt to read ``context_builder`` section from NAVIG config."""
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            raw = cm.global_config or {}
            return raw.get("context_builder", {})
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_builder_instance: ContextBuilder | None = None


def get_context_builder(
    config: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> ContextBuilder:
    """Get or create the module-level ContextBuilder singleton."""
    global _builder_instance
    if _builder_instance is None:
        _builder_instance = ContextBuilder(config=config, project_root=project_root)
    return _builder_instance


def build_context(
    user_input: str,
    caller_info: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """
    One-call convenience that mirrors ``ContextBuilder.build_context``.

    Safe to call even when nothing is configured -- returns EMPTY_CONTEXT
    on any failure.
    """
    try:
        builder = get_context_builder()
        return builder.build_context(user_input, caller_info, session_id)
    except Exception as exc:
        logger.warning("build_context failed, returning empty: %s", exc)
        return dict(EMPTY_CONTEXT)
