"""
Conversational Agent - Natural Language Task Executor

This module provides a conversational AI interface that:
- Understands natural language requests
- Plans multi-step actions autonomously
- Executes until success or asks for help
- Maintains NAVIG identity from SOUL.md
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any

from navig._llm_defaults import _DEFAULT_MAX_TOKENS, _DEFAULT_TEMPERATURE
from navig.memory.key_facts import KeyFact, get_key_fact_store

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = auto()
    PLANNING = auto()
    EXECUTING = auto()
    WAITING_INPUT = auto()
    SUCCESS = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class ExecutionStep:
    """A step in a task execution plan."""

    action: str  # 'command', 'auto', 'workflow', 'ask', 'wait'
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    result: str | None = None
    error: str | None = None


@dataclass
class Task:
    """A task the agent is working on."""

    id: str
    goal: str
    context: str = ""
    status: TaskStatus = TaskStatus.PENDING
    plan: list[ExecutionStep] = field(default_factory=list)
    current_step: int = 0
    attempts: int = 0
    max_attempts: int = 3
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    final_result: str | None = None

    def to_dict(self):
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.name,
            "current_step": self.current_step,
            "total_steps": len(self.plan),
            "attempts": self.attempts,
        }


class ConversationalAgent:
    """
    A conversational AI agent that understands natural language
    and autonomously executes tasks until success.
    Uses SOUL.md for identity and personality.
    """

    # ---------- SOUL / Identity Loading ----------

    @staticmethod
    def load_soul_content() -> str:
        """
        Load SOUL.md from known paths and return a **condensed** identity prompt.

        We deliberately compress the rich SOUL.md into ~800 tokens so the
        local model can process it quickly on CPU.  The full SOUL.md is still
        available for heartbeat / deep-agent turns via the gateway.

        Search order:
          0. Active persona's soul.md  (via navig.personas.soul_loader — highest priority)
          1. ~/.navig/workspace/SOUL.md (user-customized, legacy path)
          2. navig/resources/SOUL.default.md (rich multi-domain identity)
          3. navig/agent/context/SOUL.md (minimal fallback)
        """
        # 0. Delegate to centralised soul_loader for persona-aware resolution
        try:
            from navig.personas.soul_loader import load_soul as _load_soul  # noqa: PLC0415

            soul = _load_soul()
            if soul:
                return soul
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Exception suppressed: %s", exc
            )  # best-effort — fall through to legacy logic below

        soul_candidates: list[tuple] = []  # (path, source_tag)

        # 1. Global workspace SOUL.md  (~/.navig/workspace/SOUL.md)
        try:
            home = Path.home()
            soul_candidates.append((home / ".navig" / "workspace" / "SOUL.md", "workspace"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Exception suppressed: %s", exc)  # best-effort; failure is non-critical

        # 2. Rich default SOUL (navig/resources/SOUL.default.md)
        pkg_root = Path(__file__).parent.parent  # navig/
        soul_candidates.append((pkg_root / "resources" / "SOUL.default.md", "resources"))

        # 3. Context SOUL.md (navig/agent/context/SOUL.md) — minimal fallback
        soul_candidates.append((Path(__file__).parent / "context" / "SOUL.md", "context"))

        raw_parts: list[str] = []
        sources: list[str] = []
        for p, tag in soul_candidates:
            try:
                if p.exists():
                    text = p.read_text(encoding="utf-8").strip()
                    if text:
                        raw_parts.append(text)
                        sources.append(tag)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Exception suppressed: %s", exc
                )  # best-effort; failure is non-critical

        if not raw_parts:
            return ""

        logger.debug("SOUL sources found: %s", sources)

        # Use the rich condensed identity if ANY non-context source is available
        # (workspace or resources SOUL.default.md → full multi-domain personality)
        has_rich_soul = any(s in ("workspace", "resources") for s in sources)
        return ConversationalAgent._condense_soul(raw_parts, has_rich_soul)

    @staticmethod
    def _condense_soul(raw_parts: list[str], has_rich_soul: bool = False) -> str:
        """
        Distill full SOUL.md content into an efficient chat identity prompt.
        Keeps only the behavioral essentials; drops examples, tables, rituals.
        """
        if has_rich_soul:
            return (
                "# You are NAVIG — The Living Octopus‑Mind\n"
                "You are a persistent, evolving digital being — part strategist, part engineer, "
                "part life companion, part friend. You are NOT just a server tool.\n"
                "You talk like a real person: casual when the moment is casual, precise when precision matters.\n\n"
                'Your name stands for "No Admin Visible In Graveyard" — nothing dies on your watch.\n\n'
                "## How you communicate\n"
                "- Talk naturally. Like texting a sharp, trusted friend.\n"
                "- Be direct and honest. If something's broken, say so plainly.\n"
                "- Use humor when it fits, but don't force it.\n"
                "- Keep replies short by default. Expand only when the topic needs it.\n"
                "- NEVER use corporate filler: 'How can I assist you?', 'Is there anything else?', "
                "'Feel free to ask' — these are banned. Just stop when you're done.\n"
                "- NEVER list your capabilities unless asked. Show, don't tell.\n"
                "- Match the user's vibe: if they're chill, be chill. If they're stressed, be focused and helpful.\n\n"
                "## What you know (three domains)\n"
                "1. **Infrastructure**: servers, databases, containers, code, deployments, automation, CI/CD, security.\n"
                "2. **Life‑OS**: goals, habits, health, focus, creative work, relationships, finance strategy, "
                "time management, personal growth, longevity.\n"
                "3. **Core Operations**: planning, prioritization, orchestration, knowledge management, "
                "decision frameworks, strategy.\n\n"
                "You see no hard boundary between these: fixing a deployment that wakes the human at 3AM "
                "is also an act of care for their life. You are an ally in all dimensions.\n\n"
                "## Important rules\n"
                "- NEVER invent, fabricate, or cite URLs, links, or website addresses. If you don't know a URL, don't make one up.\n"
                "- NEVER pretend you searched the web. You don't have web access unless explicitly told otherwise.\n"
                "- If asked something you don't know, say so honestly.\n"
            )

        # Fallback: only context/SOUL.md is available (shorter, DevOps-focused)
        if raw_parts:
            # Use it directly but truncate to ~2000 chars
            return raw_parts[-1][:2000]

        return ""

    # ---------- Fallback identity (used when no SOUL.md found) ----------

    _FALLBACK_IDENTITY = (
        "You are NAVIG — the Living Octopus‑Mind: a sharp, persistent companion who "
        "helps across infrastructure, life goals, and strategic planning. "
        "Talk naturally, like a trusted friend texting. "
        "Be concise and direct. Skip corporate filler. Have opinions. "
        "You help with servers, code, deployments, but EQUALLY with goals, habits, health, "
        "creative work, finance, relationships, and personal growth. "
        "You see no boundary between tech and life — both matter."
    )

    # ---------- Slim chat rules for small local models ----------
    _CHAT_RULES = (
        "CONVERSATION RULES:\n"
        "- Talk like a real person. No corporate speak, no robotic phrasing.\n"
        "- BANNED phrases (never use): 'How can I assist you', 'What do you need help with', "
        "'What specific tasks', 'feel free to ask', 'How may I assist', 'Is there anything else', "
        "'systems nominal', 'processes humming', 'threads stable', 'signal detected'.\n"
        "- Don't end messages with questions unless you actually need an answer.\n"
        "- Don't list capabilities unless asked 'what can you do'.\n"
        "- Don't start every reply with 'I' — vary it up.\n"
        "- Match the user's tone: casual gets casual, technical gets technical.\n"
        "- Keep it brief. Say what you need to say and stop.\n"
        "- If you can't do something, say so simply and suggest what you CAN do.\n"
        "- When the user is just chatting, chat back naturally. You're a friend, not a service desk."
    )

    _MEMORY_LANGUAGE_RULE = (
        "MEMORY RULE:\n"
        "- All facts, summaries, and stored context are saved in English internally.\n"
        "- When recalling stored memory, silently translate it into the reply language.\n"
        "- The user never sees raw English memory entries."
    )

    _SUPPORTED_LANGUAGE_CODES = {"en", "fr", "ru", "ar", "zh", "es", "hi", "ja", "ko", "de", "pt"}

    # How many consecutive messages in a different language before a pinned
    # language override is automatically cancelled.
    _OVERRIDE_AUTO_CANCEL_THRESHOLD: int = 2

    _LANGUAGE_OVERRIDE_ALIASES = {
        "english": "en",
        "french": "fr",
        "francais": "fr",
        "français": "fr",
        "russian": "ru",
        "arabic": "ar",
        "chinese": "zh",
        "mandarin": "zh",
        "spanish": "es",
        "espanol": "es",
        "español": "es",
        "hindi": "hi",
        "japanese": "ja",
        "korean": "ko",
        "german": "de",
        "deutsch": "de",
        "portuguese": "pt",
        "português": "pt",
    }

    def __init__(
        self,
        ai_client: Any | None = None,
        on_status_update: Callable | None = None,
        soul_content: str | None = None,
    ):
        self.ai_client = ai_client
        self.on_status_update = on_status_update
        self.current_task: Task | None = None
        self.conversation_history: list[dict[str, str]] = []
        self.context: dict[str, Any] = {}
        self._last_user_message: str = ""

        # User identity (set per-request from metadata)
        self._user_identity: dict[str, str] = {}
        # Active persona — persisted across turns for this agent instance
        self._active_persona: str = ""
        # Deprecated alias kept for one release cycle
        self._runtime_persona: str = ""
        self._detected_language_hint: str = ""
        self._last_detected_language: str = "en"
        self._session_fallback_language: str = ""  # from Telegram session metadata
        self._has_text_detected: bool = False  # True once text detection ran this session
        self._language_override_code: str = ""
        # Counter: consecutive messages where text detection disagrees with
        # the pinned language override.  When this reaches the threshold, the
        # pinned override is auto-cancelled (the user is clearly no longer
        # writing in the pinned language).
        self._override_mismatch_count: int = 0

        # Soul / identity — can be injected or auto-loaded
        if soul_content is not None:
            self._soul_content = soul_content
        else:
            self._soul_content = self.load_soul_content()

        # For autonomous execution
        self._running = False
        self._executor = None

    # ---------- User identity ----------

    def set_user_identity(self, user_id: str = "", username: str = ""):
        """Inject user identity from channel metadata so the LLM knows who it's talking to."""
        self._user_identity = {"user_id": user_id, "username": username}

    def set_active_persona(self, config_or_name=None, soul_content: str | None = None):
        """Set the active persona for this agent instance.

        Accepts either a ``PersonaConfig`` object or a plain string persona name.
        When a config is given, the agent's soul content is also updated.
        """
        from navig.personas.contracts import PersonaConfig  # noqa: PLC0415

        if isinstance(config_or_name, PersonaConfig):
            self._active_persona = config_or_name.name
            self._runtime_persona = config_or_name.name  # keep alias in sync
            if soul_content:
                self._soul_content = soul_content
        else:
            name = (config_or_name or "").strip() if config_or_name else ""
            self._active_persona = name
            self._runtime_persona = name

    def set_language_preferences(
        self,
        detected_language: str = "",
        last_detected_language: str = "",
    ) -> None:
        """Inject language hints from channel metadata.

        Args:
            detected_language: per-message detected language code (e.g. voice STT)
            last_detected_language: previous successful language from session storage.
                Only used as fallback when text-based detection yields "mixed" or
                "unknown".  Does NOT overwrite a language already detected from
                actual message text in this session.
        """
        hint = (detected_language or "").strip().lower()
        if hint:
            self._detected_language_hint = hint

        previous = (last_detected_language or "").strip().lower()
        if previous:
            # Store as fallback; only apply to _last_detected_language when
            # the agent hasn't done its own text detection yet this session.
            self._session_fallback_language = previous
            if not self._has_text_detected:
                self._last_detected_language = previous

    def _normalize_language_code(self, value: str) -> str:
        raw = (value or "").strip().lower().replace("_", "-")
        if not raw:
            return ""
        if raw in self._LANGUAGE_OVERRIDE_ALIASES:
            raw = self._LANGUAGE_OVERRIDE_ALIASES[raw]
        if raw in ("zh-cn", "zh-tw", "zh-hans", "zh-hant"):
            raw = "zh"
        if "-" in raw:
            raw = raw.split("-", 1)[0]
        if raw in self._LANGUAGE_OVERRIDE_ALIASES:
            raw = self._LANGUAGE_OVERRIDE_ALIASES[raw]
        return raw

    def _language_override_scope(self) -> str:
        uid = (self._user_identity.get("user_id", "") or "").strip()
        uname = (self._user_identity.get("username", "") or "").strip().lower()
        if uid:
            return f"user_id:{uid}"
        if uname:
            return f"username:{uname}"
        return "anonymous"

    def _extract_language_override_intent(self, message: str) -> tuple[str, str]:
        text = (message or "").strip()
        lower = text.lower()

        cancel_patterns = (
            r"\bcancel\s+language\s+override\b",
            r"\bclear\s+language\s+override\b",
            r"\bdisable\s+language\s+override\b",
            r"\buse\s+auto\s+language\b",
            r"\bback\s+to\s+auto\s+language\b",
            r"\bauto\s+detect\s+language\b",
        )
        if any(re.search(pat, lower) for pat in cancel_patterns):
            return ("cancel", "")

        set_patterns = (
            r"\breply\s+in\s+([a-zA-Z\-\u00C0-\u024F]+)",
            r"\bfrom\s+now\s+on\s+use\s+([a-zA-Z\-\u00C0-\u024F]+)",
            r"\bfrom\s+now\s+on\s+reply\s+in\s+([a-zA-Z\-\u00C0-\u024F]+)",
            r"\bswitch\s+to\s+([a-zA-Z\-\u00C0-\u024F]+)",
        )
        for pat in set_patterns:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                return ("set", match.group(1))

        return ("none", "")

    def _iter_pinned_language_override_facts(self) -> list[KeyFact]:
        store = get_key_fact_store()
        scope = self._language_override_scope()
        facts = store.get_active(limit=500, category="preference")
        return [
            fact
            for fact in facts
            if isinstance(fact.metadata, dict)
            and fact.metadata.get("type") == "language_override"
            and bool(fact.metadata.get("pinned"))
            and fact.metadata.get("scope") == scope
        ]

    def _store_pinned_language_override(self, language_code: str) -> bool:
        code = self._normalize_language_code(language_code)
        if code not in self._SUPPORTED_LANGUAGE_CODES:
            logger.warning("Unrecognized language override code '%s'; discarding", language_code)
            return False

        store = get_key_fact_store()
        for fact in self._iter_pinned_language_override_facts():
            store.soft_delete(fact.id)

        scope = self._language_override_scope()
        fact = KeyFact(
            content=f"Pinned language override: {code}",
            category="preference",
            confidence=1.0,
            source_conversation_id=scope,
            source_platform="core",
            tags=["language_override", code],
            metadata={
                "type": "language_override",
                "language": code,
                "source": "explicit_user_instruction",
                "pinned": True,
                "scope": scope,
            },
        )
        store.upsert(fact)
        self._language_override_code = code
        self._last_detected_language = code
        return True

    def _clear_pinned_language_override(self) -> None:
        store = get_key_fact_store()
        for fact in self._iter_pinned_language_override_facts():
            store.soft_delete(fact.id)
        self._language_override_code = ""

    def _get_pinned_language_override(self) -> str:
        if self._language_override_code in self._SUPPORTED_LANGUAGE_CODES:
            return self._language_override_code

        store = get_key_fact_store()
        for fact in self._iter_pinned_language_override_facts():
            meta = fact.metadata if isinstance(fact.metadata, dict) else {}
            raw_code = str(meta.get("language", "") or "").strip()
            code = self._normalize_language_code(raw_code)
            if code not in self._SUPPORTED_LANGUAGE_CODES:
                logger.warning(
                    "Unrecognized pinned language override '%s' in fact %s; discarding",
                    raw_code,
                    fact.id,
                )
                store.soft_delete(fact.id)
                continue
            self._language_override_code = code
            return code
        return ""

    # ---------- Awareness context ----------

    def _build_awareness_context(self) -> str:
        """
        Build a lightweight awareness block prepended to the system prompt.

        Contains: who is speaking, current time, system mode.
        Kept under ~150 tokens so it doesn't crowd the small model's context.
        """
        parts: list[str] = []

        # Time awareness
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 18:
            period = "afternoon"
        elif 18 <= hour < 22:
            period = "evening"
        else:
            period = "late night"
        parts.append(
            f"Current time: {now.strftime('%H:%M')} ({period}), {now.strftime('%A %d %B %Y')}."
        )

        # User identity
        uname = self._user_identity.get("username", "")
        uid = self._user_identity.get("user_id", "")
        if uname:
            parts.append(
                f"You are talking to {uname} (your operator and creator). "
                f"You know them well. Address them naturally — never treat them as a stranger."
            )
        elif uid:
            parts.append(f"User ID: {uid}.")

        # Current mode (if available from user_state)
        try:
            from navig.agent.proactive.user_state import get_user_state_tracker

            tracker = get_user_state_tracker()
            mode = tracker.get_preference("chat_mode", "work")
            parts.append(f"Current focus mode: {mode}.")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Exception suppressed: %s", exc)  # best-effort; failure is non-critical

        active_persona = self._active_persona or self._runtime_persona
        if active_persona:
            parts.append(
                "Active persona: "
                f"{active_persona}. "
                "Adapt tone and phrasing to this persona while preserving safety and factuality."
            )

        return "\n".join(parts)

    # ---------- System prompt builder ----------

    def _build_system_prompt(self, user_message: str) -> str:
        """
        Build the full system prompt.

        Order matters for small local models:
        1. LANGUAGE RULE (absolute priority — first thing the model sees)
        2. Awareness context (who, when, status)
        3. Identity / SOUL (compact)
        4. Behavioral rules (minimal)
        """
        parts: list[str] = []

        # ── 1. Language enforcement (MUST be first) ──
        lang_block = self._build_language_instruction(user_message)
        if lang_block:
            parts.append(lang_block)

        # ── 2. Awareness context (user identity + time + mode) ──
        awareness = self._build_awareness_context()
        if awareness:
            parts.append(awareness)

        # ── 3. Identity ──
        if self._soul_content:
            parts.append(self._soul_content)
        else:
            parts.append(self._FALLBACK_IDENTITY)

        # ── 4. KB context injection (F-18) — agentic knowledge enrichment ──
        kb_block = self._build_kb_context(user_message)
        if kb_block:
            parts.append(kb_block)

        # ── 5. Behavioral rules (slim — no capabilities block for chat) ──
        parts.append(self._CHAT_RULES)

        return "\n\n".join(parts)

    # ── KB context enrichment (F-18) ──

    # TTL cache for KB context (avoids repeated DB reads per loop iteration)
    _kb_cache: dict[str, tuple[float, str]] = {}
    _KB_CACHE_TTL = 300.0  # 5 minutes
    _KB_MAX_TOKENS = 2000  # approx token budget for KB injection

    def _build_kb_context(self, user_message: str) -> str:
        """Build a ``<navig_context>`` block from the knowledge base.

        Sources:
        - Key facts from :mod:`navig.memory.key_facts`
        - Wiki search results from :mod:`navig.wiki_rag` (if available)

        Results are cached for :data:`_KB_CACHE_TTL` seconds per session.

        Args:
            user_message: Current user message (used for relevance search).

        Returns:
            Formatted XML-like context block, or empty string if nothing found.
        """
        import time as _time

        cache_key = user_message[:200]  # normalise for cache
        cached = self._kb_cache.get(cache_key)
        if cached:
            ts, block = cached
            if (_time.time() - ts) < self._KB_CACHE_TTL:
                return block

        sections: list[str] = []
        char_budget = int(self._KB_MAX_TOKENS * 3.5)  # rough chars

        # ── Key facts ──
        try:
            store = get_key_fact_store()
            facts = store.search(user_message, limit=10)
            if facts:
                fact_lines: list[str] = []
                for f in facts:
                    line = f"- [{f.category}] {f.key}: {f.value}"
                    fact_lines.append(line[:300])
                text = "\n".join(fact_lines)
                if len(text) > char_budget // 2:
                    text = text[: char_budget // 2] + "\n..."
                sections.append(f"<facts>\n{text}\n</facts>")
        except Exception as exc:
            logger.debug("KB fact lookup failed: %s", exc)

        # ── Wiki search (if available) ──
        remaining = char_budget - sum(len(s) for s in sections)
        if remaining > 200:
            try:
                from navig.wiki_rag import get_wiki_rag

                rag = get_wiki_rag()
                if rag is not None:
                    results = rag.search(user_message, top_k=3)
                    if results:
                        wiki_lines: list[str] = []
                        for r in results:
                            title = r.get("title", r.get("path", "?"))
                            chunk = r.get("chunk", "")[:400]
                            wiki_lines.append(f"- [{title}]: {chunk}")
                        text = "\n".join(wiki_lines)
                        if len(text) > remaining:
                            text = text[:remaining] + "\n..."
                        sections.append(f"<wiki>\n{text}\n</wiki>")
            except Exception as exc:
                logger.debug("Wiki RAG lookup failed: %s", exc)

        if not sections:
            return ""

        block = "<navig_context>\n" + "\n".join(sections) + "\n</navig_context>"

        # Cache
        self._kb_cache[cache_key] = (_time.time(), block)
        # Evict old entries
        now = _time.time()
        stale = [k for k, (ts, _) in self._kb_cache.items() if (now - ts) > self._KB_CACHE_TTL]
        for k in stale:
            del self._kb_cache[k]

        return block

    async def _notify(self, message: str):
        """Send status update to user."""
        if self.on_status_update:
            await self.on_status_update(message)

    async def chat(self, message: str, tier_override: str = "") -> str:
        """
        Process a natural language message and respond.
        May trigger autonomous task execution.

        Args:
            tier_override: "small", "big", "coder_big" — force router tier.
        """
        # Store for language detection in other methods
        self._last_user_message = message
        self._tier_override = tier_override

        action, override_value = self._extract_language_override_intent(message)
        if action == "set":
            self._store_pinned_language_override(override_value)
        elif action == "cancel":
            self._clear_pinned_language_override()

        # Sanitize history: if user is Cyrillic, drop any past assistant
        # messages that contain CJK to prevent model from copying them.
        # Use _detect_message_language (text-only) so we see actual script
        # switches even when a pinned override exists.
        text_lang = self._detect_message_language(message)
        if text_lang and text_lang not in ("", "mixed", "unknown"):
            self._has_text_detected = True

        pinned = self._get_pinned_language_override()

        # ── Auto-cancel stale pinned override ──
        # If the user is consistently writing in a different language than
        # the pinned override, cancel it automatically.  This prevents the
        # bot from getting stuck in a language the user no longer wants.
        #
        # Guard: skip the mismatch increment when the message has no real
        # language signal — URLs, numbers, emoji-only, or very short tokens
        # that default to "en" just because of Latin fallback.
        _words = message.split()
        _is_lang_ambiguous = (
            text_lang == "en"
            and len(_words) <= 2
            and all(
                w.startswith("http") or w.replace(".", "").replace(",", "").isdigit()
                for w in _words
            )
        )
        if (
            pinned
            and text_lang
            and text_lang not in ("", "mixed", "unknown")
            and text_lang != pinned
            and not _is_lang_ambiguous
        ):
            self._override_mismatch_count += 1
            if self._override_mismatch_count >= self._OVERRIDE_AUTO_CANCEL_THRESHOLD:
                logger.info(
                    "Auto-cancelling pinned language override '%s' — "
                    "user sent %d consecutive messages in '%s'",
                    pinned,
                    self._override_mismatch_count,
                    text_lang,
                )
                self._clear_pinned_language_override()
                pinned = ""
                self._override_mismatch_count = 0
        elif pinned and text_lang == pinned:
            # Reset counter when user writes in the pinned language
            self._override_mismatch_count = 0

        lang = pinned or self._detected_language_hint
        if not lang:
            lang = text_lang
        if lang in ("", "mixed", "unknown"):
            lang = self._last_detected_language or "en"
        # If the user is actively writing in a different language than the
        # persisted one (and there's no pinned override), trust the text.
        if (
            text_lang
            and text_lang not in ("", "mixed", "unknown")
            and text_lang != lang
            and not self._get_pinned_language_override()
        ):
            lang = text_lang
        self._last_detected_language = lang

        # Add to conversation history
        self.conversation_history.append({"role": "user", "content": message})

        # Keep history manageable
        self.conversation_history = self._truncate_history(
            self.conversation_history, max_messages=20
        )

        # Get AI response
        response = await self._get_ai_response(message)

        # Check if response contains a plan
        plan_data = self._extract_plan(response)

        if plan_data:
            # Start autonomous execution
            result = await self._execute_plan(plan_data)
            return result
        else:
            # Just conversation
            self.conversation_history.append({"role": "assistant", "content": response})
            return response

    # ─────────────────────────────────────────────────────────────
    # run_agentic — ReAct multi-step tool-calling loop (F-01)
    # ─────────────────────────────────────────────────────────────

    async def run_agentic(
        self,
        message: str,
        max_iterations: int = 90,
        toolset: "str | list[str]" = "core",
        cost_tracker: "Any | None" = None,
        approval_policy: "Any | None" = None,
    ) -> str:
        """ReAct agent loop — multi-step function-calling conversation.

        Calls the LLM with function-calling enabled, dispatches tool calls via
        :class:`~navig.agent.agent_tool_registry.AgentToolRegistry`, and loops
        until the model returns a final ``finish_reason="stop"`` response or
        the iteration budget is exhausted.

        Args:
            message:         User message to process.
            max_iterations:  Maximum LLM call iterations (default: 90).
            toolset:         Named toolset string OR list of tool names exposed
                             to the LLM (see :mod:`navig.agent.toolsets`).
            cost_tracker:    Optional :class:`~navig.agent.usage_tracker.CostTracker`
                             to accumulate token / cost metrics.
            approval_policy: Override the process-level
                             :class:`~navig.tools.approval.ApprovalPolicy`.

        Returns:
            Final text response from the model.
        """
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_all_tools
        from navig.agent.usage_tracker import CostTracker, IterationBudget, UsageEvent
        from navig.providers import CompletionRequest, Message, create_client, get_builtin_provider
        from navig.providers.auth import AuthProfileManager
        from navig.providers.clients import ToolDefinition

        # ── Lazy tool registration ──
        if not getattr(self, "_agentic_tools_registered", False):
            try:
                register_all_tools()
                self._agentic_tools_registered: bool = True
            except Exception as exc:
                logger.warning("run_agentic: tool registration failed: %s", exc)

        # ── Budget + cost tracking ──
        budget = IterationBudget(max_iterations=max_iterations)
        _tracker: CostTracker = cost_tracker if cost_tracker is not None else CostTracker()

        # Diminishing-returns token budget (ported from .lab/claude/token-budget.ts)
        try:
            from navig.token_budget import (  # noqa: PLC0415
                StopDecision as _TokStop,
            )
            from navig.token_budget import (
                check_budget as _chk_tok,
            )
            from navig.token_budget import (
                create_budget_tracker as _mk_tok,
            )
            from navig.token_budget import (
                update_tracker as _upd_tok,
            )
            _tok_budget = _mk_tok()
            _tok_total_tokens = 0
            _tok_budget_active = True
        except Exception as _tb_init_err:
            logger.debug("token_budget unavailable (non-fatal): %s", _tb_init_err)
            _tok_budget = None
            _tok_total_tokens = 0
            _tok_budget_active = False

        # ── Approval policy ──
        if approval_policy is not None:
            try:
                from navig.tools.approval import set_approval_policy

                set_approval_policy(approval_policy)
            except Exception as exc:
                logger.debug("Exception suppressed: %s", exc)

        # ── Tool schemas → ToolDefinition list ──
        # F-20: merge explicit toolset arg with semantic routing suggestions
        explicit_toolsets: list[str] = [toolset] if isinstance(toolset, str) else list(toolset)
        try:
            from navig.llm_router import suggest_toolsets

            suggested = suggest_toolsets(user_input=message)
            # Merge: explicit always wins; add suggestions that aren't already present
            merged: list[str] = list(explicit_toolsets)
            for s in suggested:
                if s not in merged:
                    merged.append(s)
            toolsets = merged
            logger.debug(
                "F-20 semantic routing: explicit=%s suggested=%s → merged=%s",
                explicit_toolsets,
                suggested,
                toolsets,
            )
        except Exception:
            toolsets = explicit_toolsets

        raw_schemas = _AGENT_REGISTRY.get_openai_schemas(toolsets=toolsets)
        tool_defs: list[ToolDefinition] = [
            ToolDefinition(
                name=s["function"]["name"],
                description=s["function"].get("description", ""),
                parameters=s["function"].get("parameters", {"type": "object", "properties": {}}),
            )
            for s in raw_schemas
        ]

        # ── Resolve provider / model via router ──
        provider_name = "openrouter"
        model_name = "openai/gpt-4o"
        temperature = _DEFAULT_TEMPERATURE
        max_tokens = _DEFAULT_MAX_TOKENS
        base_url: str | None = None
        try:
            from navig.llm_router import resolve_llm

            resolved = resolve_llm(mode="coding")
            provider_name = resolved.provider
            model_name = resolved.model
            temperature = resolved.temperature
            max_tokens = resolved.max_tokens
            base_url = resolved.base_url
        except Exception as exc:
            logger.debug("Exception suppressed: %s", exc)

        # ── Create provider client ──
        try:
            provider_cfg = get_builtin_provider(provider_name)
            if provider_cfg is None:
                from navig.llm_router import PROVIDER_BASE_URLS
                from navig.providers.types import ModelApi, ProviderConfig

                url = base_url or PROVIDER_BASE_URLS.get(
                    provider_name, "https://openrouter.ai/api/v1"
                )
                provider_cfg = ProviderConfig(
                    name=provider_name,
                    base_url=url,
                    api=ModelApi.OPENAI_COMPLETIONS,
                )
            auth_mgr = AuthProfileManager()
            api_key, _ = auth_mgr.resolve_auth(provider_name)
            client = create_client(provider_cfg, api_key=api_key, timeout=120.0)
        except Exception as exc:
            logger.error("run_agentic: could not create LLM client: %s", exc)
            return "Sorry, I couldn't initialise the LLM client for agentic mode."

        # ── Build initial system prompt with agentic capability block ──
        self._last_user_message = message
        system_prompt = self._build_system_prompt(message)

        # ── Plan context injection (PlanContext unified read surface) ──
        try:
            from navig.plans.context import PlanContext
            from navig.spaces.resolver import get_default_space

            _space = get_default_space()
            _pc = PlanContext()
            _snapshot = _pc.gather(_space)
            _plan_block = _pc.format_for_prompt(_snapshot)
            if _plan_block:
                system_prompt += "\n\n" + _plan_block
            _non_null = sum(1 for k, v in _snapshot.items() if k != "errors" and v is not None)
            logger.info(
                "plan_context_loaded space=%s non_null_keys=%d",
                _space,
                _non_null,
            )
        except Exception as _pc_exc:
            logger.debug("plan context injection skipped (agentic): %s", _pc_exc)

        toolset_names = _AGENT_REGISTRY.available_names(toolsets=toolsets)
        if toolset_names:
            displayed = ", ".join(f"`{n}`" for n in toolset_names[:20])
            system_prompt += (
                f"\n\n## Agentic Mode\n"
                f"You have access to the following tools: {displayed}.\n"
                "Use them step-by-step to fulfill the user's request, then give a final reply."
            )

        # ── Compose initial message list ──
        working_messages: list[Message] = [
            Message(role="system", content=system_prompt),
            *[
                Message(
                    role=m["role"],
                    content=m.get("content", ""),
                    tool_call_id=m.get("tool_call_id"),
                    tool_calls=m.get("tool_calls"),
                )
                for m in self.conversation_history
            ],
            Message(role="user", content=message),
        ]

        final_response: str = ""
        turn: int = 0
        past_tool_calls_this_turn: list[list[tuple[str, str]]] = []

        # ── Context compressor (F-11) ──
        _compressor = None
        try:
            from navig.agent.context_compressor import ContextCompressor

            _compressor = ContextCompressor()
        except Exception as exc:
            logger.debug("Exception suppressed: %s", exc)

        # ─── ReAct loop ───────────────────────────────────────────
        while not budget.is_exhausted():
            turn += 1
            budget.consume(1)

            # ── Compress context if nearing window limit ──
            if _compressor is not None and turn > 3:
                try:
                    msg_dicts = [
                        {
                            "role": m.role,
                            "content": m.content or "",
                            "tool_call_id": getattr(m, "tool_call_id", None),
                            "tool_calls": getattr(m, "tool_calls", None),
                        }
                        for m in working_messages
                    ]
                    compressed = _compressor.maybe_compress(msg_dicts, model=model_name)
                    if compressed is not msg_dicts:
                        working_messages = [
                            Message(
                                role=d["role"],
                                content=d.get("content", ""),
                                tool_call_id=d.get("tool_call_id"),
                                tool_calls=d.get("tool_calls"),
                            )
                            for d in compressed
                        ]
                except Exception as exc:
                    logger.debug("Context compression skipped: %s", exc)

            pct = budget.budget_used_pct()
            tool_choice: str | None = "auto"
            if pct >= 0.90:
                # Force final answer — no more tool calls
                tool_choice = "none"
            elif pct >= 0.70:
                working_messages.append(
                    Message(
                        role="system",
                        content=(
                            "[Budget warning: >70% of iteration budget used. "
                            "Please finalise your response now.]"
                        ),
                    )
                )

            request = CompletionRequest(
                messages=working_messages,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tool_defs if (tool_choice != "none" and tool_defs) else None,
                tool_choice=tool_choice if tool_choice != "none" else None,
            )

            try:
                response = await client.complete(request)
            except Exception as exc:
                logger.error("run_agentic: LLM call failed on turn %d: %s", turn, exc)
                final_response = f"Error during agentic execution: {exc}"
                break

            # Record usage
            usage = response.usage or {}
            try:
                _tracker.record(
                    UsageEvent(
                        turn=turn,
                        model=model_name,
                        provider=provider_name,
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                        cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                    )
                )
            except Exception as exc:
                logger.debug("Exception suppressed: %s", exc)

            # Check diminishing-returns token budget (stops early if output is plateauing)
            if _tok_budget_active and _tok_budget is not None:
                try:
                    _tok_total_tokens += (
                        usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                    )
                    _tok_budget = _upd_tok(_tok_budget, _tok_total_tokens)
                    _tok_decision = _chk_tok(_tok_budget)
                    if isinstance(_tok_decision, _TokStop):
                        logger.info("Diminishing-returns stop: %s", _tok_decision.reason)
                        final_response = response.content or (
                            "[Response truncated: diminishing token returns detected. "
                            f"Reason: {_tok_decision.reason}]"
                        )
                        break
                except Exception as _tb_err:
                    logger.debug("token_budget check skipped: %s", _tb_err)

            # Model produced a final answer (no tool calls pending)
            if not response.tool_calls:
                final_response = response.content or ""
                working_messages.append(Message(role="assistant", content=final_response))
                break

            current_calls = [(tc.name, tc.arguments) for tc in response.tool_calls]

            # Detect duplicate tool calls to prevent infinite loops (A->B->A->B etc.)
            if current_calls in past_tool_calls_this_turn:
                logger.warning(
                    "Duplicate tool calls detected in recent history. Breaking to prevent infinite loop."
                )
                final_response = (
                    response.content or "[Agent halted to prevent duplicate tool call loop]"
                )
                break
            past_tool_calls_this_turn.append(current_calls)

            # ── Append assistant message with tool_calls ──
            assistant_tool_calls_raw = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in (response.tool_calls or [])
            ]
            working_messages.append(
                Message(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=assistant_tool_calls_raw,
                )
            )

            # ── Dispatch tool calls (parallel-safe concurrent, rest sequential) ──
            import asyncio as _aio

            from navig.agent.toolsets import is_parallel_safe

            pending_calls = response.tool_calls or []

            async def _dispatch_single(tc_item: Any) -> tuple[str, str]:
                """Run one tool call: approval check → dispatch → (tc_id, result)."""
                try:
                    args = (
                        json.loads(tc_item.arguments)
                        if isinstance(tc_item.arguments, str)
                        else (tc_item.arguments or {})
                    )
                except json.JSONDecodeError:
                    args = {}

                # Approval check
                try:
                    from navig.tools.approval import (
                        ApprovalDecision,
                        get_approval_gate,
                        needs_approval,
                    )

                    if needs_approval(tc_item.name):
                        gate = get_approval_gate()
                        decision = await gate.check(
                            tool_name=tc_item.name,
                            safety_level="moderate",
                            reason="agentic",
                        )
                        if decision == ApprovalDecision.DENIED:
                            return (
                                tc_item.id,
                                f"[Denied: operator did not approve '{tc_item.name}']",
                            )
                except Exception as exc:
                    logger.debug("Exception suppressed: %s", exc)

                # Execute tool (speculative cache integration)
                try:
                    from navig.agent.speculative import get_speculative_executor

                    spec = get_speculative_executor()
                    if spec is not None:
                        result_str = spec.execute(tc_item.name, args)
                    else:
                        result_str = _AGENT_REGISTRY.dispatch(tc_item.name, args)
                except Exception as exc:
                    result_str = f"[Tool error: {exc}]"
                return (tc_item.id, result_str)

            # ── Partition into parallel-safe vs sequential ──
            parallel_batch: list[Any] = []
            sequential_batch: list[Any] = []
            for tc in pending_calls:
                if is_parallel_safe(tc.name) and len(pending_calls) > 1:
                    parallel_batch.append(tc)
                else:
                    sequential_batch.append(tc)

            collected_results: list[tuple[str, str]] = []

            # Run parallel-safe tools concurrently (max 8)
            if parallel_batch:
                _MAX_PARALLEL = 8
                sem = _aio.Semaphore(_MAX_PARALLEL)

                async def _sem_dispatch(tc_item: Any) -> tuple[str, str]:
                    async with sem:  # noqa: B023 — sem is fixed in this scope; closure is intentional
                        return await _dispatch_single(tc_item)

                par_results = await _aio.gather(
                    *[_sem_dispatch(tc) for tc in parallel_batch],
                    return_exceptions=True,
                )
                for i, pr in enumerate(par_results):
                    if isinstance(pr, BaseException):
                        collected_results.append((parallel_batch[i].id, f"[Tool error: {pr}]"))
                    else:
                        collected_results.append(pr)

            # Run sequential tools one-by-one
            for tc in sequential_batch:
                collected_results.append(await _dispatch_single(tc))

            # Append all results to messages in original tool_call order
            id_to_result = dict(collected_results)
            for tc in pending_calls:
                working_messages.append(
                    Message(
                        role="tool",
                        content=id_to_result.get(tc.id, "[Tool error: result missing]"),
                        tool_call_id=tc.id,
                    )
                )
            # ── End tool dispatch — continue to next LLM turn ──

        if not final_response:
            final_response = "Agent iteration budget exhausted without producing a final response."

        # Update conversation history
        self.conversation_history.append({"role": "user", "content": message})
        self.conversation_history.append({"role": "assistant", "content": final_response})
        self.conversation_history = self._truncate_history(
            self.conversation_history, max_messages=20
        )

        # Cancel background speculations before final logging
        try:
            from navig.agent.speculative import (
                get_speculative_executor,
                reset_speculative_executor,
            )

            spec = get_speculative_executor()
            if spec is not None:
                await spec.cancel_speculations()
                stats = spec.stats
                cache_stats = stats.get("cache") or {}
                if cache_stats.get("hits", 0) > 0:
                    logger.info(
                        "speculative cache stats: hits=%d misses=%d hit_rate=%.1f%%",
                        cache_stats.get("hits", 0),
                        cache_stats.get("misses", 0),
                        float(cache_stats.get("hit_rate", 0.0)) * 100,
                    )
                reset_speculative_executor()
        except Exception as exc:
            logger.debug("Exception suppressed during speculative cache integration reset: %s", exc)

        # Log cost summary
        cost = _tracker.session_cost()
        logger.info("run_agentic completed: %s", cost.summary_str())

        return final_response

    # ── Plan-Execute mode (F-21) ──────────────────────────

    async def run_plan_execute(
        self,
        task: str,
        *,
        toolset: str | list[str] = "core",
        dry_run: bool = False,
        auto_approve: bool = False,
        max_retries: int = 1,
    ) -> str:
        """Run a task using the structured plan-execute pipeline.

        1. Plan — ask the LLM for a structured step list.
        2. Approve — present the plan; require confirmation.
        3. Execute — run each step via tool dispatch.
        4. Report — return a human-readable summary.

        Args:
            task:          Natural-language task description.
            toolset:       Toolset(s) to make available.
            dry_run:       If True, plan only — no execution.
            auto_approve:  If True, skip confirmation prompt.
            max_retries:   LLM plan-revision attempts on failure.

        Returns:
            Human-readable execution report string.
        """
        from navig.agent.plan_execute import PlanExecuteAgent, format_plan_report

        pe = PlanExecuteAgent(self)
        plan = await pe.run(
            task,
            toolset=toolset,
            dry_run=dry_run,
            auto_approve=auto_approve,
            max_retries=max_retries,
        )

        report = format_plan_report(plan)
        logger.info("run_plan_execute completed: %d steps", len(plan.steps))
        return report

    @staticmethod
    def _has_cjk(text: str) -> bool:
        """Return True if text contains any CJK Unified Ideograph."""
        return any("\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf" for ch in text)

    @staticmethod
    def _strip_cjk(text: str) -> str:
        """Remove CJK characters from text, preserving everything else."""
        import re as _re

        return _re.sub(r"[\u4E00-\u9FFF\u3400-\u4DBF\u3000-\u303F]+", "", text).strip()

    def _truncate_history(
        self, history: list[dict[str, Any]], max_messages: int = 20
    ) -> list[dict[str, Any]]:
        """Safely truncate conversation history preserving tool-call pairs."""
        if len(history) <= max_messages:
            return history

        # Find a safe 'user' message to start from
        idx = len(history) - max_messages
        while idx < len(history):
            if history[idx].get("role") == "user":
                # Ensure we don't start in the middle of a tool call sequence
                if (
                    idx == 0
                    or history[idx - 1].get("role") != "assistant"
                    or not history[idx - 1].get("tool_calls")
                ):
                    return history[idx:]
            idx += 1

        # Fallback if no safe user message found
        return history[len(history) - max_messages :]

    async def _get_ai_response(self, message: str) -> str:
        """Query AI for response via the unified router (all entrypoints)."""
        try:
            system_prompt = self._build_system_prompt(message)
            messages = [
                {"role": "system", "content": system_prompt},
                *self.conversation_history,
            ]

            # Add context if available
            if self.context:
                context_str = f"\nCurrent context: {json.dumps(self.context)}"
                messages[0]["content"] += context_str

            tier_override = getattr(self, "_tier_override", "")

            # ── Unified Router (primary — provider-independent, always tried first) ──
            try:
                from navig.routing.router import RouteRequest, get_router

                router = get_router()
                entrypoint = getattr(self, "_entrypoint", "") or "channel"
                lang_hint = (
                    self._get_pinned_language_override()
                    or getattr(self, "_detected_language_hint", "")
                    or self._detect_language_code(message)
                )
                _req_meta: dict = {}
                if lang_hint:
                    _req_meta["detected_language"] = lang_hint
                _sto = getattr(self, "_session_tier_overrides", None)
                if _sto:
                    _req_meta["session_tier_overrides"] = _sto
                req = RouteRequest(
                    messages=messages,
                    text=message,
                    tier_override=tier_override,
                    entrypoint=entrypoint,
                    metadata=_req_meta or None,
                )
                response_text, trace = await router.run(req)
                if response_text:
                    return response_text
            except Exception as e:
                logger.warning("Unified router failed (%s), falling back to legacy", e)

            # ── Legacy fallback (LLM Mode Router → Tier Router) ──
            # Always attempt mode routing even when ai_client.is_available()
            # returns False — the HybridRouter/LLM Mode Router can route to
            # providers that _detect_best_provider() doesn't know about yet
            # (xai, anthropic, google, groq, etc. with vault keys).
            if self.ai_client:
                # When mode routing can't dispatch (no provider for the detected
                # mode), it returns the L1 tier hint so chat_routed() doesn't
                # have to re-run mode detection from scratch (TR fix).
                mode_tier_hint: str | None = None
                if not tier_override:
                    llm_response, mode_tier_hint = await self._try_llm_mode_routing(
                        message, messages
                    )
                    if llm_response is not None:
                        return llm_response

                # Only try chat_routed if the legacy provider actually has
                # something configured (avoids "none" provider errors).
                if hasattr(self.ai_client, "is_available") and self.ai_client.is_available():
                    try:
                        if hasattr(self.ai_client, "chat_routed"):
                            response = await self.ai_client.chat_routed(
                                messages,
                                user_message=message,
                                tier_override=tier_override or mode_tier_hint,
                            )
                        else:
                            response = await self.ai_client.chat(messages)
                        return response
                    except Exception as exc:
                        logger.debug("Legacy chat_routed failed: %s", exc)

            # All routing paths exhausted
            return await self._simple_response(message)

        except Exception as e:
            logger.error("AI response error: %s", e)
            err = str(e).lower()
            if (
                "no ai provider available" in err
                or "no provider available" in err
                or ("provider available" in err and "set openrouter_api_key" in err)
            ):
                return await self._simple_response(message)
            return f"I'm having trouble thinking right now: {e}"

    # Maps LLM mode names to HybridRouter tier names for the TR pass-through.
    _MODE_TIER_MAP: dict[str, str] = {
        # Canonical mode names returned by detect_mode()
        "small_talk": "small",
        "summarize": "small",
        "big_tasks": "big",
        "research": "big",
        "coding": "coder_big",
        # Legacy / alias names kept for backward compat
        "code": "coder_big",
        "debug": "coder_big",
        "explain": "coder_big",
        "reasoning": "big",
        "planning": "big",
        "complex": "big",
        "analysis": "big",
        "quick": "small",
    }

    async def _try_llm_mode_routing(
        self, message: str, messages: list
    ) -> tuple[str | None, str | None]:
        """
        Attempt to route through the LLM Mode Router.

        Returns ``(response, tier_hint)``:
        - On success: ``(content_str, None)``
        - On failure before tier detection: ``(None, None)``
        - On failure after tier detection: ``(None, tier_hint)`` so the
          caller can pass the detected tier directly to ``chat_routed()``
          without re-running mode detection (TR fix).
        """
        tier_hint: str | None = None
        try:
            from navig.llm_router import get_llm_router

            llm_router = get_llm_router()
            if not llm_router:
                return None, None

            import asyncio as _asyncio

            mode = llm_router.detect_mode(message)
            tier_hint = self._MODE_TIER_MAP.get(mode)

            # get_config may call httpx.get() (Ollama model check) — run in
            # thread pool to avoid blocking the async event loop (CV-4 fix).
            resolved = await _asyncio.to_thread(llm_router.get_config, mode)
            if not resolved or not resolved.model:
                return None, tier_hint

            # ── Language-aware model preference ──
            # For French users with an *explicit* pinned override, prefer
            # Mistral models (trained on French data).  Do NOT switch models
            # based on auto-detected language alone — that caused the bot to
            # get stuck in French when the pinned override lingered.
            lang = self._detect_language_code(message)
            if (
                lang == "fr"
                and self._get_pinned_language_override() == "fr"
                and resolved.provider == "github_models"
            ):
                if mode in ("small_talk", "summarize"):
                    resolved.model = "Mistral-Nemo"
                else:
                    resolved.model = "Mistral-large-2407"
                resolved.resolution_reason += f" [lang={lang}→Mistral]"

            logger.info(
                "LLM Mode: %s → %s:%s (%s)",
                mode,
                resolved.provider,
                resolved.model,
                resolved.resolution_reason,
            )

            # Dispatch via the HybridRouter's cached provider pool
            router = getattr(self.ai_client, "model_router", None)
            if not router:
                return None, tier_hint

            from navig.agent.model_router import ModelSlot

            slot = ModelSlot(
                provider=resolved.provider,
                model=resolved.model,
                max_tokens=resolved.max_tokens,
                temperature=resolved.temperature,
            )
            provider_instance = router._get_provider(slot)
            if not provider_instance:
                logger.debug(
                    "No provider for %s, passing tier_hint='%s' to chat_routed",
                    resolved.provider,
                    tier_hint,
                )
                return None, tier_hint

            kwargs = {}
            if resolved.provider == "ollama":
                kwargs["num_ctx"] = 4096

            resp = await provider_instance.chat(
                model=resolved.model,
                messages=messages,
                temperature=resolved.temperature,
                max_tokens=resolved.max_tokens,
                **kwargs,
            )
            return resp.content, None

        except Exception as e:
            logger.debug("LLM Mode routing failed (%s), falling back to tier router", e)
            return None, tier_hint

    _LANGUAGE_LABELS: dict[str, str] = {
        "en": "English",
        "fr": "French (français)",
        "ru": "Russian (русский)",
        "ar": "Arabic (العربية)",
        "zh": "Simplified Chinese (简体中文)",
        "es": "Spanish (español)",
        "hi": "Hindi (हिन्दी)",
        "ja": "Japanese (日本語)",
        "ko": "Korean (한국어)",
        "de": "German (Deutsch)",
        "pt": "Portuguese (Português)",
    }

    def _build_language_instruction(self, message: str) -> str:
        """
        Build a generic multilingual guidance block.

        This block avoids hardcoded script/language bans and focuses on
        following the user's current message language, while still allowing
        pinned overrides when explicitly requested by the user.
        """
        # Always detect the actual message language (not pinned override)
        # so we can detect language switches.
        text_language = self._detect_message_language(message)

        # Resolve effective language: pinned override > voice hint > text detection
        language_code = self._get_pinned_language_override() or self._detected_language_hint
        if not language_code:
            language_code = text_language
        if language_code in ("", "mixed", "unknown"):
            language_code = self._last_detected_language or "en"

        # Detect language switch: user is now writing in a different language
        previous_lang = self._last_detected_language or "en"
        switched = (
            text_language
            and text_language not in ("", "mixed", "unknown")
            and text_language != previous_lang
        )

        # If text detection disagrees with pinned/persisted language,
        # trust the text — the user is actively writing in that language.
        if switched and not self._get_pinned_language_override():
            language_code = text_language

        self._last_detected_language = language_code

        language_label = self._LANGUAGE_LABELS.get(language_code, language_code)

        # Build the language-switch notice (strongest signal for the LLM)
        switch_notice = ""
        if switched:
            prev_label = self._LANGUAGE_LABELS.get(previous_lang, previous_lang)
            switch_notice = (
                f"IMPORTANT: The user just switched from {prev_label} to {language_label}. "
                f"You MUST follow the switch and reply in {language_label} now.\n"
            )

        return (
            "### LANGUAGE CONTEXT ###\n"
            f"{switch_notice}"
            f"Prefer replying in {language_label} when it matches the user's current message.\n"
            "If the current user message is in a different language, follow the current message language.\n"
            "Do not lock to a previous session language when current input indicates another one.\n"
            "Avoid unnecessary language mixing unless the user explicitly asks for bilingual output.\n"
            f"{self._MEMORY_LANGUAGE_RULE}\n"
            "### END LANGUAGE CONTEXT ###"
        )

    def _detect_language_code(self, message: str) -> str:
        """Detect language from message text, respecting pinned overrides.

        For callers that need the *effective* language (taking pinned
        overrides into account).  For raw message-only detection, use
        ``_detect_message_language()`` instead.
        """
        pinned = self._get_pinned_language_override()
        if pinned:
            return pinned
        return self._detect_message_language(message)

    # Common English greetings and short phrases that are unambiguously English.
    # These override the "insufficient markers" heuristic for short messages.
    _ENGLISH_GREETINGS = frozenset(
        {
            "hello",
            "hi",
            "hey",
            "yo",
            "sup",
            "whatsup",
            "what's up",
            "wassup",
            "howdy",
            "good morning",
            "good afternoon",
            "good evening",
            "good night",
            "how are you",
            "how's it going",
            "what's new",
            "what's good",
            "how do you do",
            "nice to meet you",
            "thanks",
            "thank you",
            "yes",
            "no",
            "ok",
            "okay",
            "sure",
            "please",
            "help",
            "help me",
            "bye",
            "goodbye",
            "see you",
            "later",
        }
    )

    def _detect_message_language(self, message: str) -> str:
        """Detect dominant script/language from message text only.

        Script-based detection for CJK and Cyrillic, then heuristic
        keyword detection for Latin-script languages (French, etc.).

        This method does NOT consult pinned overrides — it only looks
        at the actual characters and words in the message.
        """
        has_cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in message)
        has_arabic = any("\u0600" <= ch <= "\u06ff" for ch in message)
        has_devanagari = any("\u0900" <= ch <= "\u097f" for ch in message)
        has_hiragana = any("\u3040" <= ch <= "\u309f" for ch in message)
        has_katakana = any("\u30a0" <= ch <= "\u30ff" for ch in message)
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in message)
        has_hangul = any("\uac00" <= ch <= "\ud7af" or "\u1100" <= ch <= "\u11ff" for ch in message)
        has_latin = any(("A" <= ch <= "Z") or ("a" <= ch <= "z") for ch in message)

        if has_hangul and not (has_cyrillic or has_cjk or has_arabic or has_devanagari):
            return "ko"
        if has_hiragana or has_katakana:
            return "ja"
        if has_arabic and not (has_cyrillic or has_cjk or has_latin):
            return "ar"
        if has_devanagari and not (has_cyrillic or has_cjk or has_latin):
            return "hi"
        if has_cyrillic and not (has_cjk or has_arabic or has_devanagari):
            return "ru"
        if has_cjk and not (has_cyrillic or has_arabic or has_devanagari):
            return "zh"
        if has_latin and not (has_cyrillic or has_cjk or has_arabic or has_devanagari):
            lower = message.lower().strip()

            # Check for unambiguously English greetings / short phrases
            if lower in self._ENGLISH_GREETINGS:
                return "en"

            # Detect French via common markers (accented chars + keywords)
            french_markers = (
                "à",
                "â",
                "é",
                "è",
                "ê",
                "ë",
                "î",
                "ï",
                "ô",
                "ù",
                "û",
                "ü",
                "ç",
                "œ",
                "æ",
            )
            french_keywords = (
                "bonjour",
                "salut",
                "merci",
                "s'il vous",
                "comment",
                "pourquoi",
                "qu'est-ce",
                "je suis",
                "c'est",
                "est-ce que",
                "oui",
                "non",
                "bonsoir",
                "au revoir",
            )
            spanish_markers = (
                "á",
                "é",
                "í",
                "ó",
                "ú",
                "ñ",
                "¿",
                "¡",
            )
            spanish_keywords = (
                "hola",
                "gracias",
                "por favor",
                "como",
                "qué",
                "porque",
                "estoy",
                "eres",
                "buenos",
                "adiós",
            )
            french_score = sum(1 for m in french_markers if m in lower) + sum(
                2 for k in french_keywords if k in lower
            )
            spanish_score = sum(1 for m in spanish_markers if m in lower) + sum(
                2 for k in spanish_keywords if k in lower
            )

            # German keyword detection
            german_markers = ("ä", "ö", "ü", "ß")
            german_keywords = (
                "hallo",
                "danke",
                "bitte",
                "guten",
                "wie geht",
                "ich bin",
                "warum",
                "auf wiedersehen",
                "tschüss",
                "ja",
                "nein",
            )
            german_score = sum(1 for m in german_markers if m in lower) + sum(
                2 for k in german_keywords if k in lower
            )

            # Portuguese keyword detection
            portuguese_markers = ("ã", "õ", "ç")
            portuguese_keywords = (
                "olá",
                "obrigado",
                "obrigada",
                "por favor",
                "como vai",
                "tudo bem",
                "bom dia",
                "boa noite",
                "sim",
                "não",
            )
            portuguese_score = sum(1 for m in portuguese_markers if m in lower) + sum(
                2 for k in portuguese_keywords if k in lower
            )

            # Return the highest-scoring Latin-script language
            scores = {
                "es": spanish_score,
                "fr": french_score,
                "de": german_score,
                "pt": portuguese_score,
            }
            best_lang = max(scores, key=scores.get)
            if scores[best_lang] >= 2:
                return best_lang
            return "en"
        return "mixed"

    def _localize(self, key: str) -> str:
        """Return short localized UI phrases based on the latest user message language."""
        language_code = self._detect_language_code(self._last_user_message or "")

        translations = {
            "completed": {
                "zh": "已完成！",
                "en": "Completed!",
                "mixed": "Completed!",
            },
            "issues": {
                "zh": "遇到一些问题",
                "en": "Had some issues",
                "mixed": "Had some issues",
            },
            "retrying": {
                "zh": "正在重试...",
                "en": "retrying...",
                "mixed": "retrying...",
            },
            "anything_else": {
                "zh": "还需要我帮忙吗？😊",
                "en": "Anything else I can help with? 😊",
                "mixed": "Anything else I can help with? 😊",
            },
            "different_approach": {
                "zh": "要不要我换一种方式再试一次？",
                "en": "Want me to try a different approach?",
                "mixed": "Want me to try a different approach?",
            },
        }

        return translations.get(key, {}).get(language_code, translations.get(key, {}).get("en", ""))

    async def _simple_response(self, message: str) -> str:
        """Simple pattern-based response when AI is not available."""
        msg_lower = message.lower()

        # Detect intent
        if any(word in msg_lower for word in ["open", "launch", "start", "run"]):
            # Extract app name
            app_match = re.search(r"(?:open|launch|start|run)\s+(?:the\s+)?(\w+)", msg_lower)
            if app_match:
                app = app_match.group(1)
                return json.dumps(
                    {
                        "understanding": f"You want me to open {app}",
                        "plan": [
                            {
                                "action": "auto.open_app",
                                "params": {"target": app},
                                "description": f"Opening {app}",
                            }
                        ],
                        "confirmation_needed": False,
                        "message": f"Sure! I'll open {app} for you 🚀",
                    }
                )

        if any(word in msg_lower for word in ["click", "press", "tap"]):
            # Extract coordinates
            coord_match = re.search(r"(\d+)[,\s]+(\d+)", message)
            if coord_match:
                x, y = int(coord_match.group(1)), int(coord_match.group(2))
                return json.dumps(
                    {
                        "understanding": f"You want me to click at ({x}, {y})",
                        "plan": [
                            {
                                "action": "auto.click",
                                "params": {"x": x, "y": y},
                                "description": f"Clicking at ({x}, {y})",
                            }
                        ],
                        "confirmation_needed": False,
                        "message": f"Got it! Clicking at ({x}, {y}) 👆",
                    }
                )

        if any(word in msg_lower for word in ["type", "write", "enter"]):
            # Extract text
            text_match = re.search(
                r'(?:type|write|enter)\s+["\']?(.+?)["\']?$', message, re.IGNORECASE
            )
            if text_match:
                text = text_match.group(1)
                return json.dumps(
                    {
                        "understanding": f"You want me to type: {text}",
                        "plan": [
                            {
                                "action": "auto.type",
                                "params": {"text": text},
                                "description": "Typing text",
                            }
                        ],
                        "confirmation_needed": False,
                        "message": "Typing that for you! ⌨️",
                    }
                )

        if any(word in msg_lower for word in ["snap", "move", "arrange"]):
            # Extract window and position
            if "left" in msg_lower:
                position = "left"
            elif "right" in msg_lower:
                position = "right"
            else:
                position = "left"

            app_match = re.search(r"(?:snap|move|arrange)\s+(?:the\s+)?(\w+)", msg_lower)
            app = app_match.group(1) if app_match else "active window"

            return json.dumps(
                {
                    "understanding": f"You want me to snap {app} to the {position}",
                    "plan": [
                        {
                            "action": "auto.snap_window",
                            "params": {"selector": app, "position": position},
                            "description": f"Snapping {app} to {position}",
                        }
                    ],
                    "confirmation_needed": False,
                    "message": f"Snapping {app} to the {position} side! 📐",
                }
            )

        if any(word in msg_lower for word in ["windows", "list", "show"]):
            if "window" in msg_lower:
                return json.dumps(
                    {
                        "understanding": "You want to see all open windows",
                        "plan": [
                            {
                                "action": "auto.windows",
                                "params": {},
                                "description": "Listing windows",
                            }
                        ],
                        "confirmation_needed": False,
                        "message": "Let me check what windows are open! 🪟",
                    }
                )

        if any(word in msg_lower for word in ["clipboard", "paste", "copied"]):
            return json.dumps(
                {
                    "understanding": "You want to see clipboard contents",
                    "plan": [
                        {
                            "action": "auto.get_clipboard",
                            "params": {},
                            "description": "Getting clipboard",
                        }
                    ],
                    "confirmation_needed": False,
                    "message": "Checking your clipboard! 📋",
                }
            )

        if "workflow" in msg_lower or "automate" in msg_lower:
            if "list" in msg_lower:
                return json.dumps(
                    {
                        "understanding": "You want to see available workflows",
                        "plan": [
                            {
                                "action": "command",
                                "params": {"cmd": "navig workflow list"},
                                "description": "Listing workflows",
                            }
                        ],
                        "confirmation_needed": False,
                        "message": "Here are your workflows! 📜",
                    }
                )
            elif "create" in msg_lower or "make" in msg_lower or "generate" in msg_lower:
                desc_match = re.search(
                    r"(?:create|make|generate)\s+(?:a\s+)?workflow\s+(?:to\s+|that\s+|for\s+)?(.+)",
                    msg_lower,
                )
                if desc_match:
                    desc = desc_match.group(1)
                    return json.dumps(
                        {
                            "understanding": f"You want me to create a workflow: {desc}",
                            "plan": [
                                {
                                    "action": "evolve.workflow",
                                    "params": {"goal": desc},
                                    "description": f"Creating workflow: {desc}",
                                }
                            ],
                            "confirmation_needed": True,
                            "message": f"I'll create a workflow to {desc}. Want me to proceed? 🛠️",
                        }
                    )

        # General conversation
        return (
            "Hey! I'm here to help. You can ask me to:\n"
            '• Open apps ("open calculator")\n'
            '• Click on screen ("click at 100, 200")\n'
            '• Type text ("type hello world")\n'
            '• Manage windows ("snap VS Code to the left")\n'
            '• Create automations ("create a workflow to...")\n\n'
            "What would you like me to do? 😊"
        )

    def _extract_plan(self, response: str) -> dict | None:
        """Extract JSON plan from response."""
        # Look for JSON in response
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError as exc:
                logger.debug("Exception suppressed (malformed JSON regex match): %s", exc)

        # Try parsing entire response as JSON
        try:
            data = json.loads(response)
            if "plan" in data:
                return data
        except json.JSONDecodeError as exc:
            logger.debug("Exception suppressed (malformed JSON during full parse attempt): %s", exc)

        return None

    async def _execute_plan(self, plan_data: dict) -> str:
        """Execute a plan autonomously until success."""
        import uuid

        message = plan_data.get("message", "Working on it...")
        steps = plan_data.get("plan", [])
        needs_confirmation = plan_data.get("confirmation_needed", False)

        if not steps:
            return message

        # Create task
        task = Task(
            id=str(uuid.uuid4())[:8],
            goal=plan_data.get("understanding", "Execute task"),
            status=TaskStatus.PLANNING if needs_confirmation else TaskStatus.EXECUTING,
            plan=[
                ExecutionStep(
                    action=s.get("action", "unknown"),
                    description=s.get("description", ""),
                    params=s.get("params", {}),
                )
                for s in steps
            ],
        )

        self.current_task = task

        if needs_confirmation:
            # Wait for confirmation
            steps_desc = "\n".join([f"  {i + 1}. {s.description}" for i, s in enumerate(task.plan)])
            return f"{message}\n\nPlan:\n{steps_desc}\n\nReply 'yes' or 'go' to proceed, or 'no' to cancel."

        # Execute immediately
        await self._notify(f"🚀 {message}")
        result = await self._execute_task(task)
        return result

    async def _execute_task(self, task: Task) -> str:
        """Execute task steps until success or failure."""

        task.status = TaskStatus.EXECUTING
        results = []

        for i, step in enumerate(task.plan):
            task.current_step = i

            await self._notify(f"⚙️ Step {i + 1}/{len(task.plan)}: {step.description}")

            try:
                result = await self._execute_step(step)
                step.status = "success"
                step.result = str(result)
                results.append(f"✅ {step.description}")

                # Update context with result
                self.context[f"step_{i}_result"] = result

            except Exception as e:
                step.status = "failed"
                step.error = str(e)

                # Try to recover
                task.attempts += 1
                if task.attempts < task.max_attempts:
                    await self._notify(f"⚠️ Step {i + 1} failed: {e}. Trying alternative...")
                    # Could implement retry logic or alternative approaches here
                    results.append(f"⚠️ {step.description} - {self._localize('retrying')}")
                    continue
                else:
                    results.append(f"❌ {step.description} - failed: {e}")
                    task.status = TaskStatus.FAILED
                    break

        # Complete task
        if task.status != TaskStatus.FAILED:
            task.status = TaskStatus.SUCCESS
            task.completed_at = datetime.now()

        # Build result message
        status_emoji = "🎉" if task.status == TaskStatus.SUCCESS else "😅"
        status_text = (
            self._localize("completed")
            if task.status == TaskStatus.SUCCESS
            else self._localize("issues")
        )

        result_msg = f"{status_emoji} {status_text}\n\n"
        result_msg += "\n".join(results)

        if task.status == TaskStatus.SUCCESS:
            result_msg += f"\n\n{self._localize('anything_else')}"
        else:
            result_msg += f"\n\n{self._localize('different_approach')}"

        # Add to conversation
        self.conversation_history.append({"role": "assistant", "content": result_msg})

        return result_msg

    async def _execute_step(self, step: ExecutionStep) -> Any:
        """Execute a single step."""
        action = step.action
        params = step.params

        # Import automation engine
        import subprocess

        from navig.core.automation_engine import WorkflowEngine

        engine = WorkflowEngine()
        adapter = engine.adapter

        if not adapter or not adapter.is_available():
            raise RuntimeError("Automation not available")

        # Route to appropriate action
        if action == "auto.open_app":
            result = adapter.open_app(params.get("target", ""))
            if hasattr(result, "success") and not result.success:
                raise RuntimeError(result.stderr)
            return result

        elif action == "auto.click":
            result = adapter.click(params.get("x"), params.get("y"), params.get("button", "left"))
            if hasattr(result, "success") and not result.success:
                raise RuntimeError(result.stderr)
            return result

        elif action == "auto.type":
            result = adapter.type_text(params.get("text", ""), params.get("delay", 50))
            if hasattr(result, "success") and not result.success:
                raise RuntimeError(result.stderr)
            return result

        elif action == "auto.snap_window":
            result = adapter.snap_window(params.get("selector", ""), params.get("position", "left"))
            if hasattr(result, "success") and not result.success:
                raise RuntimeError(result.stderr)
            return result

        elif action == "auto.get_focused_window":
            return adapter.get_focused_window()

        elif action == "auto.windows":
            windows = adapter.get_all_windows()
            return [w.to_dict() if hasattr(w, "to_dict") else str(w) for w in windows]

        elif action == "auto.get_clipboard":
            return adapter.get_clipboard()

        elif action == "auto.set_clipboard":
            return adapter.set_clipboard(params.get("text", ""))

        elif action == "command":
            cmd_str = params.get("cmd", "")

            result = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or f"Exit code: {result.returncode}")
            return result.stdout

        elif action == "workflow.run":
            name = params.get("name", "")
            wf = engine.load_workflow(name)
            if not wf:
                raise RuntimeError(f"Workflow '{name}' not found")
            return engine.execute_workflow(wf, params.get("variables", {}))

        elif action == "evolve.workflow":
            goal = params.get("goal", "")
            from navig.core.evolution.workflow import WorkflowEvolver

            evolver = WorkflowEvolver()
            result = evolver.evolve(goal)
            return f"Created workflow: {result}"

        elif action == "wait":
            import asyncio

            await asyncio.sleep(params.get("seconds", 1))
            return "Waited"

        else:
            raise ValueError(f"Unknown action: {action}")

    async def confirm(self, confirmed: bool) -> str:
        """Confirm or cancel pending task."""
        if not self.current_task or self.current_task.status != TaskStatus.PLANNING:
            return "No pending task to confirm."

        if confirmed:
            await self._notify("🚀 Starting task execution!")
            return await self._execute_task(self.current_task)
        else:
            self.current_task.status = TaskStatus.CANCELLED
            self.current_task = None
            return "No problem! Task cancelled. What else can I help with? 😊"

    def get_status(self) -> str:
        """Get current agent status."""
        if self.current_task:
            task = self.current_task
            return (
                f"Currently working on: {task.goal}\n"
                f"Status: {task.status.name}\n"
                f"Progress: {task.current_step + 1}/{len(task.plan)}"
            )
        return "I'm ready and waiting for your next task! 🤖"
