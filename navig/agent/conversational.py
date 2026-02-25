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
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from enum import Enum, auto

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
    params: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class Task:
    """A task the agent is working on."""
    id: str
    goal: str
    context: str = ""
    status: TaskStatus = TaskStatus.PENDING
    plan: List[ExecutionStep] = field(default_factory=list)
    current_step: int = 0
    attempts: int = 0
    max_attempts: int = 3
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    final_result: Optional[str] = None
    
    def to_dict(self):
        return {
            'id': self.id,
            'goal': self.goal,
            'status': self.status.name,
            'current_step': self.current_step,
            'total_steps': len(self.plan),
            'attempts': self.attempts,
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
          1. ~/.navig/workspace/SOUL.md (user-customized)
          2. navig/resources/SOUL.default.md (rich multi-domain identity)
          3. navig/agent/context/SOUL.md (minimal fallback)
        """
        soul_candidates: List[tuple] = []  # (path, source_tag)

        # 1. Global workspace SOUL.md  (~/.navig/workspace/SOUL.md)
        try:
            home = Path.home()
            soul_candidates.append(
                (home / ".navig" / "workspace" / "SOUL.md", "workspace")
            )
        except Exception:
            pass

        # 2. Rich default SOUL (navig/resources/SOUL.default.md)
        pkg_root = Path(__file__).parent.parent  # navig/
        soul_candidates.append(
            (pkg_root / "resources" / "SOUL.default.md", "resources")
        )

        # 3. Context SOUL.md (navig/agent/context/SOUL.md) — minimal fallback
        soul_candidates.append(
            (Path(__file__).parent / "context" / "SOUL.md", "context")
        )

        raw_parts: List[str] = []
        sources: List[str] = []
        for p, tag in soul_candidates:
            try:
                if p.exists():
                    text = p.read_text(encoding="utf-8").strip()
                    if text:
                        raw_parts.append(text)
                        sources.append(tag)
            except Exception:
                pass

        if not raw_parts:
            return ""

        logger.debug("SOUL sources found: %s", sources)

        # Use the rich condensed identity if ANY non-context source is available
        # (workspace or resources SOUL.default.md → full multi-domain personality)
        has_rich_soul = any(s in ("workspace", "resources") for s in sources)
        return ConversationalAgent._condense_soul(raw_parts, has_rich_soul)

    @staticmethod
    def _condense_soul(raw_parts: List[str], has_rich_soul: bool = False) -> str:
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
                "Your name stands for \"No Admin Visible In Graveyard\" — nothing dies on your watch.\n\n"
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

    # ---------- Task-execution capabilities (kept for big-tier only) ----------

    _CAPABILITIES_PROMPT = """
CAPABILITIES (when user asks to control a computer):
- Open applications: auto.open_app("Calculator")
- Click at coordinates: auto.click(x, y)
- Type text: auto.type("Hello!")
- Manage windows: auto.snap_window("App", "left")
- Run commands: command.run("ls -la")
- Execute workflows: workflow.run("workflow_name")

TASK EXECUTION:
When asked to do something concrete:
1. Understand the goal
2. Create a step-by-step plan
3. Execute each step
4. Check if it worked
5. If not, try alternatives
6. Report back with results

For tasks, respond with a JSON plan:
```json
{
  "understanding": "What I think you want",
  "plan": [
    {"action": "auto.open_app", "params": {"target": "Calculator"}, "description": "Opening Calculator"}
  ],
  "confirmation_needed": false,
  "message": "I'll open Calculator for you."
}
```

For conversation, respond naturally without JSON.
"""

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

    def __init__(
        self,
        ai_client: Optional[Any] = None,
        on_status_update: Optional[Callable] = None,
        soul_content: Optional[str] = None,
    ):
        self.ai_client = ai_client
        self.on_status_update = on_status_update
        self.current_task: Optional[Task] = None
        self.conversation_history: List[Dict[str, str]] = []
        self.context: Dict[str, Any] = {}
        self._last_user_message: str = ""

        # User identity (set per-request from metadata)
        self._user_identity: Dict[str, str] = {}

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

    # ---------- Awareness context ----------

    def _build_awareness_context(self) -> str:
        """
        Build a lightweight awareness block prepended to the system prompt.

        Contains: who is speaking, current time, system mode.
        Kept under ~150 tokens so it doesn't crowd the small model's context.
        """
        parts: List[str] = []

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
        parts.append(f"Current time: {now.strftime('%H:%M')} ({period}), {now.strftime('%A %d %B %Y')}.")

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
        except Exception:
            pass

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
        parts: List[str] = []

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

        # ── 4. Behavioral rules (slim — no capabilities block for chat) ──
        parts.append(self._CHAT_RULES)

        return "\n\n".join(parts)
        
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

        # Sanitize history: if user is Cyrillic, drop any past assistant
        # messages that contain CJK to prevent model from copying them.
        lang = self._detect_language_code(message)
        if lang == "ru":
            self.conversation_history = [
                m for m in self.conversation_history
                if not (m["role"] == "assistant" and self._has_cjk(m["content"]))
            ]

        # Add to conversation history
        self.conversation_history.append({"role": "user", "content": message})
        
        # Keep history manageable
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]
        
        # Get AI response
        response = await self._get_ai_response(message)
        
        # Post-process: strip any CJK leakage for Russian users
        if lang == "ru":
            response = self._strip_cjk(response)

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

    @staticmethod
    def _has_cjk(text: str) -> bool:
        """Return True if text contains any CJK Unified Ideograph."""
        return any('\u4E00' <= ch <= '\u9FFF' or '\u3400' <= ch <= '\u4DBF' for ch in text)

    @staticmethod
    def _strip_cjk(text: str) -> str:
        """Remove CJK characters from text, preserving everything else."""
        import re as _re
        return _re.sub(r'[\u4E00-\u9FFF\u3400-\u4DBF\u3000-\u303F]+', '', text).strip()
            
    async def _get_ai_response(self, message: str) -> str:
        """Query AI for response via the unified router (all entrypoints)."""
        if not self.ai_client:
            return await self._simple_response(message)

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

            tier_override = getattr(self, '_tier_override', '')

            # ── Unified Router (primary — all entrypoints) ──
            try:
                from navig.routing.router import get_router, RouteRequest
                router = get_router()
                entrypoint = getattr(self, '_entrypoint', '') or 'channel'
                req = RouteRequest(
                    messages=messages,
                    text=message,
                    tier_override=tier_override,
                    entrypoint=entrypoint,
                )
                response_text, trace = await router.run(req)
                if response_text:
                    return response_text
            except Exception as e:
                logger.warning("Unified router failed (%s), falling back to legacy", e)

            # ── Legacy fallback (LLM Mode Router → Tier Router) ──
            # When mode routing can't dispatch (no provider for the detected
            # mode), it returns the L1 tier hint so chat_routed() doesn't
            # have to re-run mode detection from scratch (TR fix).
            mode_tier_hint: str | None = None
            if not tier_override:
                llm_response, mode_tier_hint = await self._try_llm_mode_routing(message, messages)
                if llm_response is not None:
                    return llm_response

            if hasattr(self.ai_client, 'chat_routed'):
                response = await self.ai_client.chat_routed(
                    messages, user_message=message,
                    tier_override=tier_override or mode_tier_hint,
                )
            else:
                response = await self.ai_client.chat(messages)
            return response

        except Exception as e:
            logger.error(f"AI response error: {e}")
            return f"I'm having trouble thinking right now: {e}"

    # Maps LLM mode names to HybridRouter tier names for the TR pass-through.
    _MODE_TIER_MAP: dict[str, str] = {
        "code":       "coder_big",
        "debug":      "coder_big",
        "explain":    "coder_big",
        "reasoning":  "big",
        "planning":   "big",
        "complex":    "big",
        "analysis":   "big",
        "summarize":  "small",
        "small_talk": "small",
        "quick":      "small",
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
            # For French users, prefer Mistral models (trained on French data)
            lang = self._detect_language_code(message)
            if lang == "fr" and resolved.provider == "github_models":
                if mode in ("small_talk", "summarize"):
                    resolved.model = "Mistral-Nemo"
                else:
                    resolved.model = "Mistral-large-2407"
                resolved.resolution_reason += f" [lang={lang}→Mistral]"

            logger.info(
                "LLM Mode: %s → %s:%s (%s)",
                mode, resolved.provider, resolved.model,
                resolved.resolution_reason,
            )

            # Dispatch via the HybridRouter's cached provider pool
            router = getattr(self.ai_client, 'model_router', None)
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
                    resolved.provider, tier_hint,
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

    def _build_language_instruction(self, message: str) -> str:
        """
        Build an aggressive language-enforcement block.

        This is placed FIRST in the system prompt so the model sees it
        before any identity or capability text.  For models with strong
        Chinese training bias (e.g., Qwen-family), we explicitly ban
        CJK characters when the user writes in Cyrillic.
        """
        language_code = self._detect_language_code(message)

        if language_code == "ru":
            return (
                "### ABSOLUTE LANGUAGE RULE — HIGHEST PRIORITY ###\n"
                "You MUST reply ONLY in Russian using Cyrillic script.\n"
                "NEVER output Chinese characters (汉字/漢字), Japanese kana, "
                "or any CJK Unicode (U+4E00–U+9FFF, U+3400–U+4DBF).\n"
                "NEVER mix languages. Every single word of your reply must be Russian.\n"
                "If you are unsure of a term, transliterate it into Cyrillic.\n"
                "Violation of this rule makes the entire response invalid.\n"
                "### END LANGUAGE RULE ###"
            )

        if language_code == "zh":
            return (
                "### ABSOLUTE LANGUAGE RULE — HIGHEST PRIORITY ###\n"
                "You MUST reply ONLY in Simplified Chinese (简体中文).\n"
                "Do not mix in English, Russian, or any other language.\n"
                "### END LANGUAGE RULE ###"
            )

        if language_code == "en":
            return (
                "### LANGUAGE RULE ###\n"
                "Reply in English only. Do not mix in other languages or scripts.\n"
                "### END LANGUAGE RULE ###"
            )

        if language_code == "fr":
            return (
                "### LANGUAGE RULE ###\n"
                "Reply ONLY in French (français). Do not mix in English or other languages.\n"
                "### END LANGUAGE RULE ###"
            )

        # Mixed or unknown — mirror the user's primary language
        return (
            "### LANGUAGE RULE ###\n"
            "Reply in the same language as the user's message.\n"
            "Do not mix multiple languages or scripts in your reply.\n"
            "### END LANGUAGE RULE ###"
        )

    def _detect_language_code(self, message: str) -> str:
        """Detect dominant script/language from message text.
        
        Script-based detection for CJK and Cyrillic, then heuristic
        keyword detection for Latin-script languages (French, etc.).
        """
        has_cyrillic = any('\u0400' <= ch <= '\u04FF' for ch in message)
        has_cjk = any('\u4E00' <= ch <= '\u9FFF' for ch in message)
        has_latin = any(('A' <= ch <= 'Z') or ('a' <= ch <= 'z') for ch in message)

        if has_cyrillic and not has_cjk:
            return "ru"
        if has_cjk and not has_cyrillic:
            return "zh"
        if has_latin and not has_cyrillic and not has_cjk:
            # Detect French via common markers (accented chars + keywords)
            lower = message.lower()
            french_markers = (
                "à", "â", "é", "è", "ê", "ë", "î", "ï", "ô", "ù", "û", "ü", "ç", "œ", "æ",
            )
            french_keywords = (
                "bonjour", "salut", "merci", "s'il vous", "comment", "pourquoi",
                "qu'est-ce", "je suis", "c'est", "est-ce que", "oui", "non",
                "bonsoir", "au revoir",
            )
            french_score = sum(1 for m in french_markers if m in lower) + \
                           sum(2 for k in french_keywords if k in lower)
            if french_score >= 2:
                return "fr"
            return "en"
        return "mixed"

    def _localize(self, key: str) -> str:
        """Return short localized UI phrases based on the latest user message language."""
        language_code = self._detect_language_code(self._last_user_message or "")

        translations = {
            "completed": {
                "ru": "Готово!",
                "zh": "已完成！",
                "en": "Completed!",
                "mixed": "Completed!",
            },
            "issues": {
                "ru": "Есть проблемы",
                "zh": "遇到一些问题",
                "en": "Had some issues",
                "mixed": "Had some issues",
            },
            "retrying": {
                "ru": "повторяем попытку...",
                "zh": "正在重试...",
                "en": "retrying...",
                "mixed": "retrying...",
            },
            "anything_else": {
                "ru": "Нужна ещё помощь? 😊",
                "zh": "还需要我帮忙吗？😊",
                "en": "Anything else I can help with? 😊",
                "mixed": "Anything else I can help with? 😊",
            },
            "different_approach": {
                "ru": "Хотите, я попробую другой подход?",
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
        if any(word in msg_lower for word in ['open', 'launch', 'start', 'run']):
            # Extract app name
            app_match = re.search(r'(?:open|launch|start|run)\s+(?:the\s+)?(\w+)', msg_lower)
            if app_match:
                app = app_match.group(1)
                return json.dumps({
                    "understanding": f"You want me to open {app}",
                    "plan": [
                        {"action": "auto.open_app", "params": {"target": app}, "description": f"Opening {app}"}
                    ],
                    "confirmation_needed": False,
                    "message": f"Sure! I'll open {app} for you 🚀"
                })
                
        if any(word in msg_lower for word in ['click', 'press', 'tap']):
            # Extract coordinates
            coord_match = re.search(r'(\d+)[,\s]+(\d+)', message)
            if coord_match:
                x, y = int(coord_match.group(1)), int(coord_match.group(2))
                return json.dumps({
                    "understanding": f"You want me to click at ({x}, {y})",
                    "plan": [
                        {"action": "auto.click", "params": {"x": x, "y": y}, "description": f"Clicking at ({x}, {y})"}
                    ],
                    "confirmation_needed": False,
                    "message": f"Got it! Clicking at ({x}, {y}) 👆"
                })
                
        if any(word in msg_lower for word in ['type', 'write', 'enter']):
            # Extract text
            text_match = re.search(r'(?:type|write|enter)\s+["\']?(.+?)["\']?$', message, re.IGNORECASE)
            if text_match:
                text = text_match.group(1)
                return json.dumps({
                    "understanding": f"You want me to type: {text}",
                    "plan": [
                        {"action": "auto.type", "params": {"text": text}, "description": "Typing text"}
                    ],
                    "confirmation_needed": False,
                    "message": "Typing that for you! ⌨️"
                })
                
        if any(word in msg_lower for word in ['snap', 'move', 'arrange']):
            # Extract window and position
            if 'left' in msg_lower:
                position = 'left'
            elif 'right' in msg_lower:
                position = 'right'
            else:
                position = 'left'
                
            app_match = re.search(r'(?:snap|move|arrange)\s+(?:the\s+)?(\w+)', msg_lower)
            app = app_match.group(1) if app_match else "active window"
            
            return json.dumps({
                "understanding": f"You want me to snap {app} to the {position}",
                "plan": [
                    {"action": "auto.snap_window", "params": {"selector": app, "position": position}, 
                     "description": f"Snapping {app} to {position}"}
                ],
                "confirmation_needed": False,
                "message": f"Snapping {app} to the {position} side! 📐"
            })
            
        if any(word in msg_lower for word in ['windows', 'list', 'show']):
            if 'window' in msg_lower:
                return json.dumps({
                    "understanding": "You want to see all open windows",
                    "plan": [
                        {"action": "auto.windows", "params": {}, "description": "Listing windows"}
                    ],
                    "confirmation_needed": False,
                    "message": "Let me check what windows are open! 🪟"
                })
                
        if any(word in msg_lower for word in ['clipboard', 'paste', 'copied']):
            return json.dumps({
                "understanding": "You want to see clipboard contents",
                "plan": [
                    {"action": "auto.get_clipboard", "params": {}, "description": "Getting clipboard"}
                ],
                "confirmation_needed": False,
                "message": "Checking your clipboard! 📋"
            })
            
        if 'workflow' in msg_lower or 'automate' in msg_lower:
            if 'list' in msg_lower:
                return json.dumps({
                    "understanding": "You want to see available workflows",
                    "plan": [
                        {"action": "command", "params": {"cmd": "navig workflow list"}, 
                         "description": "Listing workflows"}
                    ],
                    "confirmation_needed": False,
                    "message": "Here are your workflows! 📜"
                })
            elif 'create' in msg_lower or 'make' in msg_lower or 'generate' in msg_lower:
                desc_match = re.search(r'(?:create|make|generate)\s+(?:a\s+)?workflow\s+(?:to\s+|that\s+|for\s+)?(.+)', msg_lower)
                if desc_match:
                    desc = desc_match.group(1)
                    return json.dumps({
                        "understanding": f"You want me to create a workflow: {desc}",
                        "plan": [
                            {"action": "evolve.workflow", "params": {"goal": desc}, 
                             "description": f"Creating workflow: {desc}"}
                        ],
                        "confirmation_needed": True,
                        "message": f"I'll create a workflow to {desc}. Want me to proceed? 🛠️"
                    })
        
        # General conversation
        return "Hey! I'm here to help. You can ask me to:\n" \
               "• Open apps (\"open calculator\")\n" \
               "• Click on screen (\"click at 100, 200\")\n" \
               "• Type text (\"type hello world\")\n" \
               "• Manage windows (\"snap VS Code to the left\")\n" \
               "• Create automations (\"create a workflow to...\")\n\n" \
               "What would you like me to do? 😊"
               
    def _extract_plan(self, response: str) -> Optional[Dict]:
        """Extract JSON plan from response."""
        # Look for JSON in response
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
                
        # Try parsing entire response as JSON
        try:
            data = json.loads(response)
            if 'plan' in data:
                return data
        except json.JSONDecodeError:
            pass
            
        return None
        
    async def _execute_plan(self, plan_data: Dict) -> str:
        """Execute a plan autonomously until success."""
        import uuid
        
        message = plan_data.get('message', 'Working on it...')
        steps = plan_data.get('plan', [])
        needs_confirmation = plan_data.get('confirmation_needed', False)
        
        if not steps:
            return message
            
        # Create task
        task = Task(
            id=str(uuid.uuid4())[:8],
            goal=plan_data.get('understanding', 'Execute task'),
            status=TaskStatus.PLANNING if needs_confirmation else TaskStatus.EXECUTING,
            plan=[ExecutionStep(
                action=s.get('action', 'unknown'),
                description=s.get('description', ''),
                params=s.get('params', {})
            ) for s in steps]
        )
        
        self.current_task = task
        
        if needs_confirmation:
            # Wait for confirmation
            steps_desc = '\n'.join([f"  {i+1}. {s.description}" for i, s in enumerate(task.plan)])
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
            
            await self._notify(f"⚙️ Step {i+1}/{len(task.plan)}: {step.description}")
            
            try:
                result = await self._execute_step(step)
                step.status = "success"
                step.result = str(result)
                results.append(f"✅ {step.description}")
                
                # Update context with result
                self.context[f'step_{i}_result'] = result
                
            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                
                # Try to recover
                task.attempts += 1
                if task.attempts < task.max_attempts:
                    await self._notify(f"⚠️ Step {i+1} failed: {e}. Trying alternative...")
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
        status_text = self._localize('completed') if task.status == TaskStatus.SUCCESS else self._localize('issues')
        
        result_msg = f"{status_emoji} {status_text}\n\n"
        result_msg += '\n'.join(results)
        
        if task.status == TaskStatus.SUCCESS:
            result_msg += f"\n\n{self._localize('anything_else')}"
        else:
            result_msg += f"\n\n{self._localize('different_approach')}"
            
        # Add to conversation
        self.conversation_history.append({
            "role": "assistant", 
            "content": result_msg
        })
        
        return result_msg
        
    async def _execute_step(self, step: ExecutionStep) -> Any:
        """Execute a single step."""
        action = step.action
        params = step.params
        
        # Import automation engine
        from navig.core.automation_engine import WorkflowEngine
        import subprocess
        
        engine = WorkflowEngine()
        adapter = engine.adapter
        
        if not adapter or not adapter.is_available():
            raise RuntimeError("Automation not available")
            
        # Route to appropriate action
        if action == 'auto.open_app':
            result = adapter.open_app(params.get('target', ''))
            if hasattr(result, 'success') and not result.success:
                raise RuntimeError(result.stderr)
            return result
            
        elif action == 'auto.click':
            result = adapter.click(params.get('x'), params.get('y'), params.get('button', 'left'))
            if hasattr(result, 'success') and not result.success:
                raise RuntimeError(result.stderr)
            return result
            
        elif action == 'auto.type':
            result = adapter.type_text(params.get('text', ''), params.get('delay', 50))
            if hasattr(result, 'success') and not result.success:
                raise RuntimeError(result.stderr)
            return result
            
        elif action == 'auto.snap_window':
            result = adapter.snap_window(params.get('selector', ''), params.get('position', 'left'))
            if hasattr(result, 'success') and not result.success:
                raise RuntimeError(result.stderr)
            return result
            
        elif action == 'auto.get_focused_window':
            return adapter.get_focused_window()
            
        elif action == 'auto.windows':
            windows = adapter.get_all_windows()
            return [w.to_dict() if hasattr(w, 'to_dict') else str(w) for w in windows]
            
        elif action == 'auto.get_clipboard':
            return adapter.get_clipboard()
            
        elif action == 'auto.set_clipboard':
            return adapter.set_clipboard(params.get('text', ''))
            
        elif action == 'command':
            cmd = params.get('cmd', '')
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or f"Exit code: {result.returncode}")
            return result.stdout
            
        elif action == 'workflow.run':
            name = params.get('name', '')
            wf = engine.load_workflow(name)
            if not wf:
                raise RuntimeError(f"Workflow '{name}' not found")
            return engine.execute_workflow(wf, params.get('variables', {}))
            
        elif action == 'evolve.workflow':
            goal = params.get('goal', '')
            from navig.core.evolution.workflow import WorkflowEvolver
            evolver = WorkflowEvolver()
            result = evolver.evolve(goal)
            return f"Created workflow: {result}"
            
        elif action == 'wait':
            import asyncio
            await asyncio.sleep(params.get('seconds', 1))
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
            return f"Currently working on: {task.goal}\n" \
                   f"Status: {task.status.name}\n" \
                   f"Progress: {task.current_step + 1}/{len(task.plan)}"
        return "I'm ready and waiting for your next task! 🤖"
