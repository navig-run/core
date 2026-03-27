"""
RAG (Retrieval-Augmented Generation) pipeline.

Combines conversation history, knowledge base, and file content
to provide relevant context for AI model prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING


def _debug_log(message: str) -> None:
    """Simple debug logging wrapper."""
    try:
        from navig.debug_logger import DebugLogger

        logger = DebugLogger()
        logger.log_operation("memory", {"message": message})
    except Exception:
        pass  # Fail silently if logging unavailable


if TYPE_CHECKING:
    from navig.memory.conversation import ConversationStore, Message
    from navig.memory.embeddings import EmbeddingProvider
    from navig.memory.knowledge_base import KnowledgeBase


@dataclass
class RAGConfig:
    """RAG pipeline configuration."""

    # Context limits
    max_context_tokens: int = 4000
    max_history_messages: int = 20
    max_knowledge_entries: int = 5
    max_file_content_chars: int = 10000

    # Search settings
    min_similarity: float = 0.5
    recency_weight: float = 0.2  # Weight for recent messages

    # What to include
    include_history: bool = True
    include_knowledge: bool = True
    include_files: bool = True

    # Formatting
    history_format: str = "chat"  # chat, markdown, json
    knowledge_format: str = "markdown"


@dataclass
class RetrievalResult:
    """Result from RAG retrieval."""

    context: str
    sources: list[str] = field(default_factory=list)
    history_messages: int = 0
    knowledge_entries: int = 0
    file_snippets: int = 0
    token_estimate: int = 0

    def to_dict(self) -> dict:
        return {
            "context": self.context,
            "sources": self.sources,
            "history_messages": self.history_messages,
            "knowledge_entries": self.knowledge_entries,
            "file_snippets": self.file_snippets,
            "token_estimate": self.token_estimate,
        }


class RAGPipeline:
    """
    Retrieval-Augmented Generation pipeline.

    Builds rich context by combining:
    1. Recent conversation history
    2. Semantically relevant knowledge
    3. Referenced file content

    Usage:
        rag = RAGPipeline(
            conversation_store=store,
            knowledge_base=kb,
            embedding_provider=embeddings,
        )

        result = rag.retrieve(
            query="How do I configure SSH?",
            session_key="task-123",
        )

        prompt = f"Context:\\n{result.context}\\n\\nQuestion: {query}"
    """

    def __init__(
        self,
        conversation_store: ConversationStore | None = None,
        knowledge_base: KnowledgeBase | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        config: RAGConfig | None = None,
    ):
        self.conversation_store = conversation_store
        self.knowledge_base = knowledge_base
        self.embedding_provider = embedding_provider
        self.config = config or RAGConfig()

    def retrieve(
        self,
        query: str,
        session_key: str | None = None,
        include_files: list[Path] | None = None,
        tags: list[str] | None = None,
    ) -> RetrievalResult:
        """
        Retrieve relevant context for a query.

        Args:
            query: The user's query or current task
            session_key: Session for conversation history
            include_files: Additional files to include
            tags: Knowledge base tag filter

        Returns:
            RetrievalResult with combined context
        """
        parts = []
        sources = []
        result = RetrievalResult(context="")

        # 1. Conversation history
        if self.config.include_history and session_key and self.conversation_store:
            history_context = self._get_history_context(session_key)
            if history_context:
                parts.append(history_context)
                sources.append(f"conversation:{session_key}")
                result.history_messages = history_context.count("\n")

        # 2. Knowledge base search
        if self.config.include_knowledge and self.knowledge_base:
            kb_context = self._get_knowledge_context(query, tags)
            if kb_context:
                parts.append(kb_context)
                result.knowledge_entries = kb_context.count("### ")

        # 3. File content
        if self.config.include_files and include_files:
            file_context = self._get_file_context(include_files)
            if file_context:
                parts.append(file_context)
                sources.extend([f"file:{f}" for f in include_files])
                result.file_snippets = len(include_files)

        # Combine all parts
        result.context = "\n\n---\n\n".join(parts)
        result.sources = sources
        result.token_estimate = self._estimate_tokens(result.context)

        _debug_log(
            f"RAG retrieved: {result.history_messages} history, "
            f"{result.knowledge_entries} knowledge, {result.file_snippets} files"
        )

        return result

    def _get_history_context(self, session_key: str) -> str:
        """Format conversation history."""
        if not self.conversation_store:
            return ""

        messages = self.conversation_store.get_history(
            session_key,
            limit=self.config.max_history_messages,
        )

        if not messages:
            return ""

        if self.config.history_format == "chat":
            return self._format_history_chat(messages)
        elif self.config.history_format == "markdown":
            return self._format_history_markdown(messages)
        else:
            return self._format_history_json(messages)

    def _format_history_chat(self, messages: list[Message]) -> str:
        """Format history as chat transcript."""
        lines = ["## Conversation History"]

        for msg in messages:
            role = msg.role.capitalize()
            content = self._truncate(msg.content, 500)
            lines.append(f"{role}: {content}")

        return "\n".join(lines)

    def _format_history_markdown(self, messages: list[Message]) -> str:
        """Format history as markdown."""
        lines = ["## Conversation History"]

        for msg in messages:
            role = msg.role.capitalize()
            content = self._truncate(msg.content, 500)
            timestamp = msg.timestamp.strftime("%H:%M")
            lines.append(f"\n### {role} ({timestamp})\n{content}")

        return "\n".join(lines)

    def _format_history_json(self, messages: list[Message]) -> str:
        """Format history as JSON."""
        import json

        data = [
            {
                "role": msg.role,
                "content": self._truncate(msg.content, 500),
                "timestamp": msg.timestamp.isoformat(),
            }
            for msg in messages
        ]

        return f"## Conversation History\n```json\n{json.dumps(data, indent=2)}\n```"

    def _get_knowledge_context(
        self,
        query: str,
        tags: list[str] | None = None,
    ) -> str:
        """Get relevant knowledge entries."""
        if not self.knowledge_base:
            return ""

        results = self.knowledge_base.search(
            query,
            limit=self.config.max_knowledge_entries,
            min_similarity=self.config.min_similarity,
            tags=tags,
        )

        if not results:
            return ""

        lines = ["## Relevant Knowledge"]

        for entry, similarity in results:
            content = entry.summary or self._truncate(entry.content, 500)
            tags_str = ", ".join(entry.tags) if entry.tags else "none"

            if self.config.knowledge_format == "markdown":
                lines.append(f"\n### {entry.key}")
                lines.append(f"*Tags: {tags_str} | Relevance: {similarity:.2f}*")
                lines.append(content)
            else:
                lines.append(f"- **{entry.key}** ({tags_str}): {content}")

        return "\n".join(lines)

    def _get_file_context(self, files: list[Path]) -> str:
        """Read and format file content."""
        lines = ["## Referenced Files"]
        char_budget = self.config.max_file_content_chars

        for file_path in files:
            if char_budget <= 0:
                break

            try:
                if not file_path.exists():
                    continue

                content = file_path.read_text(encoding="utf-8", errors="replace")
                content = self._truncate(content, min(char_budget, 2000))
                char_budget -= len(content)

                # Detect language for code block
                lang = self._detect_language(file_path)

                lines.append(f"\n### {file_path.name}")
                lines.append(f"```{lang}")
                lines.append(content)
                lines.append("```")

            except Exception as e:
                _debug_log(f"Failed to read file {file_path}: {e}")

        return "\n".join(lines) if len(lines) > 1 else ""

    def _detect_language(self, path: Path) -> str:
        """Detect language from file extension."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".md": "markdown",
            ".sh": "bash",
            ".ps1": "powershell",
            ".sql": "sql",
            ".html": "html",
            ".css": "css",
            ".xml": "xml",
        }
        return ext_map.get(path.suffix.lower(), "")

    def _truncate(self, text: str, max_chars: int) -> str:
        """Truncate text to max characters."""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (rough approximation)."""
        # Rough estimate: ~4 chars per token for English
        return len(text) // 4

    def build_prompt(
        self,
        query: str,
        session_key: str | None = None,
        system_prompt: str | None = None,
        include_files: list[Path] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """
        Build a complete prompt with context.

        Args:
            query: User's query
            session_key: Session for history
            system_prompt: Optional system prompt
            include_files: Files to include
            tags: Knowledge tag filter

        Returns:
            Complete prompt string
        """
        result = self.retrieve(
            query=query,
            session_key=session_key,
            include_files=include_files,
            tags=tags,
        )

        parts = []

        if system_prompt:
            parts.append(system_prompt)

        if result.context:
            parts.append("# Context\n" + result.context)

        parts.append("# Current Request\n" + query)

        return "\n\n".join(parts)

    def extract_file_references(self, text: str) -> list[Path]:
        """
        Extract file paths mentioned in text.

        Looks for patterns like:
        - `path/to/file.py`
        - "path/to/file.py"
        - path/to/file.py (if it exists)
        """
        paths = []

        # Match backtick paths
        backtick_pattern = r"`([^`]+\.\w+)`"
        for match in re.finditer(backtick_pattern, text):
            path = Path(match.group(1))
            if path.exists():
                paths.append(path)

        # Match quoted paths
        quote_pattern = r'"([^"]+\.\w+)"'
        for match in re.finditer(quote_pattern, text):
            path = Path(match.group(1))
            if path.exists():
                paths.append(path)

        return list(set(paths))  # Remove duplicates

    def summarize_session(
        self,
        session_key: str,
        max_length: int = 500,
    ) -> str:
        """
        Generate a summary of a conversation session.

        Args:
            session_key: Session to summarize
            max_length: Maximum summary length

        Returns:
            Summary string
        """
        if not self.conversation_store:
            return ""

        messages = self.conversation_store.get_history(
            session_key,
            limit=50,
        )

        if not messages:
            return "No conversation history."

        # Extract key points (simple extraction)
        user_messages = [m for m in messages if m.role == "user"]
        assistant_messages = [m for m in messages if m.role == "assistant"]

        topics = set()
        for msg in user_messages[:10]:
            # Extract first sentence as topic indicator
            first_line = msg.content.split("\n")[0][:100]
            topics.add(first_line)

        summary_parts = [
            f"Session: {session_key}",
            f"Messages: {len(messages)} ({len(user_messages)} user, {len(assistant_messages)} assistant)",
            "Topics discussed:",
        ]

        for topic in list(topics)[:5]:
            summary_parts.append(f"  - {topic}")

        summary = "\n".join(summary_parts)
        return self._truncate(summary, max_length)


class ContextWindow:
    """
    Manages context window budget for token-limited models.

    Helps balance between history, knowledge, and current content.
    """

    def __init__(
        self,
        max_tokens: int = 8000,
        reserved_for_response: int = 2000,
    ):
        self.max_tokens = max_tokens
        self.reserved = reserved_for_response
        self.available = max_tokens - reserved_for_response

    def allocate(
        self,
        history_priority: float = 0.3,
        knowledge_priority: float = 0.3,
        files_priority: float = 0.4,
    ) -> dict[str, int]:
        """
        Allocate token budget across content types.

        Args:
            history_priority: Weight for conversation history
            knowledge_priority: Weight for knowledge base
            files_priority: Weight for file content

        Returns:
            Token budget per content type
        """
        total = history_priority + knowledge_priority + files_priority

        return {
            "history": int(self.available * (history_priority / total)),
            "knowledge": int(self.available * (knowledge_priority / total)),
            "files": int(self.available * (files_priority / total)),
        }

    def fits(self, content: str) -> bool:
        """Check if content fits in available budget."""
        estimated = len(content) // 4
        return estimated <= self.available

    def remaining_after(self, content: str) -> int:
        """Calculate remaining tokens after content."""
        estimated = len(content) // 4
        return max(0, self.available - estimated)
