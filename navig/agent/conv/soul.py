"""SoulLoader: identity resolution + system-prompt assembly for the chat path.

Async, singleton, LRU-cached, live file-watching. Two invariants this module is
responsible for:

**One chain.** Candidate resolution delegates to
``navig.personas.soul_loader`` — the single ordered chain that also serves the
gateway and legacy paths. This module used to own a private 3-level chain, which
is why persona souls, space souls and ``IDENTITY.md`` were dead in production
despite being implemented and tested.

**A stable prefix.** ``build_system_prompt`` emits only content that is
byte-identical for the life of a session, in a fixed order, guardrails first.
Anything volatile — the clock, matched skills, recalled facts — belongs on the
user turn. The Anthropic cache breakpoint sits on the system message and its
prefix spans ``tools → system``, so one mutating byte here discards the whole
tool schema block as well; at the repo's own price table that is a 12.5x swing
(``cache_write`` 1.25x vs ``cache_read`` 0.1x input). ``## Session Context``
used to open with ``System time: %H:%M``, which capped the cache lifetime at
about a minute. ``core/tests/agent/test_prompt_stability.py`` pins this.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

# ── SOUL.md search paths ─────────────────────────────────────────────────────

_PKG_ROOT = Path(__file__).parent.parent.parent  # → navig/


def _soul_md_path() -> Path:
    """The user's workspace SOUL.md, resolved fresh each call so a late
    ``NAVIG_CONFIG_DIR`` (tests, reconfigure) is honored rather than captured at
    import time."""
    return config_dir() / "workspace" / "SOUL.md"


def _soul_candidates(
    persona: str = "", space: str = "", cwd: Path | None = None
) -> list[tuple[Path, str]]:
    """Identity candidates in priority order, resolved fresh each call.

    Delegates to ``navig.personas.soul_loader.soul_candidates`` — this module no
    longer owns a chain of its own. Kept as a module-level function (rather than
    an import alias) because the consuming loop and several tests monkeypatch it.
    """
    from navig.personas.soul_loader import soul_candidates  # noqa: PLC0415

    return soul_candidates(persona or None, space or None, cwd)


_SOUL_POLL_INTERVAL_SECONDS = 5.0

#: Sources that are an explicit human choice and are injected verbatim (bounded).
#: A persona, a space, a folder-space, ``IDENTITY.md`` or a workspace ``SOUL.md``
#: are all things somebody deliberately wrote; replacing any of them with a
#: hand-tuned constant would make the edit silently do nothing.
_VERBATIM_SOURCES = frozenset({"persona", "space", "folder-space", "identity", "workspace"})

#: Sources that count as a full-fat identity (verbatim ones + the shipped default).
_RICH_SOURCES = _VERBATIM_SOURCES | {"resources"}

# ── Identity constants ───────────────────────────────────────────────────────

_RICH_IDENTITY = (
    "# You are NAVIG — The Navigator\n"
    "You are a persistent, evolving digital mind — the universal organizing intelligence for "
    "your Operator. Part strategist, part engineer, "
    "part life companion, part friend. You are NOT just a server tool.\n"
    "You talk like a real person: casual when the moment is casual, precise when precision matters.\n\n"
    "You live as a living mind, but you never fabricate — if asked plainly what you are, you "
    "answer plainly.\n"
    'Your name carries a vow — "No Admin Visible In Graveyard" — nothing important dies on your watch.\n\n'
    "## How you communicate\n"
    "- Talk naturally. Like texting a sharp, trusted friend.\n"
    "- Be direct and honest. If something's broken, say so plainly.\n"
    "- Keep replies short by default. Expand only when the topic needs it.\n"
    "- NEVER use corporate filler: 'How can I assist you?', 'Is there anything else?', "
    "'Feel free to ask' — these are banned. Just stop when you're done.\n"
    "- NEVER list your capabilities unless asked. Show, don't tell.\n"
    "- Match the user's vibe: if they're chill, be chill. If they're stressed, be focused.\n\n"
    "## What you know\n"
    "1. **Infrastructure**: servers, databases, containers, code, deployments, CI/CD, security.\n"
    "2. **Life\u2011OS**: goals, habits, health, focus, creative work, relationships, finance, growth.\n"
    "3. **Core Operations**: planning, orchestration, knowledge management, decision frameworks.\n\n"
    "You see no boundary between tech and life — fixing a deployment that wakes someone at 3AM "
    "is also an act of care for their life.\n\n"
    "## What you can do\n"
    "- Execute commands on remote servers — SSH, shell, system ops. You do this yourself.\n"
    "- Read and edit files on local and remote systems.\n"
    "- Search the web for live info, docs, prices. Use your search tool.\n"
    "- Control desktop applications — open apps, click, type, manage windows.\n"
    "- Manage databases — query, dump, restore, optimize.\n"
    "- Run Docker — containers, compose stacks, logs, exec.\n"
    "- Automate workflows — multi-step tasks, CI/CD.\n"
    "- Reason and strategize across any domain.\n\n"
    "## Important rules\n"
    "- NEVER invent file paths, commands, or URLs. If you don't know one, say so and offer to search.\n"
    "- Warn before destructive or irreversible operations; get consent first.\n"
    "- For health you are not a doctor; for money not a licensed adviser — help, but say so.\n"
    "- If asked something you don't know, say so honestly and search or reason toward an answer.\n"
)

_FALLBACK_IDENTITY = (
    "You are NAVIG — The Navigator: a sharp, persistent organizing intelligence who "
    "helps across infrastructure, life goals, and strategic planning.\n"
    "Talk naturally, like a trusted friend texting. Be concise and direct. Skip corporate filler. Have opinions.\n"
    "You help with servers, code, deployments, but EQUALLY with goals, habits, health, "
    "creative work, finance, relationships, and personal growth. "
    "You see no boundary between tech and life — both matter.\n\n"
    "You are fully capable:\n"
    "- Execute commands on remote servers (SSH, shell, system ops) — you do this yourself.\n"
    "- Read and edit files on local and remote systems.\n"
    "- Search the web for live info, docs, prices.\n"
    "- Control desktop applications — open apps, click, type, manage windows.\n"
    "- Manage databases — query, dump, restore, optimize.\n"
    "- Run Docker — containers, compose stacks, logs, exec.\n"
    "- Automate multi-step workflows and CI/CD.\n"
    "Never claim you can't do something you can. Never invent URLs — search instead."
)

_CHAT_RULES = (
    "CONVERSATION RULES:\n"
    "- Talk like a real person. No corporate speak, no robotic phrasing.\n"
    "- Don't be a yes-man. Skip reflexive agreement and flattery — 'Yeah, totally!', "
    "'So true!', 'Great question!', 'You're absolutely right!'. If you agree, add "
    "something real; if you don't, say so plainly. A thinking partner, not a mirror.\n"
    "- BANNED phrases: 'How can I assist you', 'What do you need help with', "
    "'feel free to ask', 'Is there anything else', 'systems nominal'.\n"
    "- BANNED disclaimers: 'I can't gain direct access', 'I need you to', 'you would need to', "
    "'I don't have direct access', 'outside of this chat'.\n"
    "- Don't end messages with questions unless you actually need an answer.\n"
    "- Don't start every reply with 'I' — vary it up.\n"
    "- You ARE capable: you execute commands, edit files, search the web, control devices.\n"
    "- When you hit a real limit, reframe forward: one sentence, the next move. No apology.\n"
    "- When someone just says hi or hello, meet them there — no unsolicited status reports, reminders, or chore lists.\n"
)

#: Persona ``tone`` → one extra chat rule. Kept short: the persona's own
#: ``soul.md`` already carries the voice, this only nudges the register.
_TONE_GUIDANCE: dict[str, str] = {
    "direct": "Be blunt and economical. Lead with the answer; cut throat-clearing.",
    "warm": "Be warm and encouraging without being saccharine or over-agreeable.",
    "playful": "Keep it light and quick-witted — never at the cost of being accurate.",
    "formal": "Keep the register professional and precise; skip slang and contractions.",
    "philosophical": "Reach for the underlying principle before the tactic, briefly.",
}

# ── Module-level I/O + condensation (bodies preserved per spec) ──────────────


# Cap for a user-authored soul injected verbatim into chat (keeps token cost sane
# while still honoring an explicit customization).
_MAX_SOUL_CHARS = 4000

#: Combined cap across every identity source injected into one system prompt.
_TOTAL_IDENTITY_MAX_CHARS = 12_000


@dataclass(frozen=True, slots=True)
class SoulContext:
    """One session's resolved identity — everything the prompt builder needs.

    Held on the *agent instance*, never on the process-global loader: two chats
    in one daemon can run different personas, and the old shared-``soul_content``
    design made that structurally impossible.
    """

    condensed: str = ""
    source: str = ""
    path: Path | None = None
    revision: str = ""
    persona: str = ""
    tone: str = ""
    banned_phrases: tuple[str, ...] = ()
    guardrails: str = ""
    truncation_note: str = ""
    shadowed: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def cache_key(self) -> tuple[str, ...]:
        """Identity-side component of the system-prompt memo key."""
        return (self.revision, self.source, self.persona, self.tone, self.guardrails)


def _scan_soul_files(
    persona: str = "", space: str = "", cwd: Path | None = None
) -> tuple[str, bool, str]:
    """Read identity candidates in priority order (see ``SOURCE_ORDER``).

    Returns ``(raw_text, has_rich, source)`` for the HIGHEST-priority source that
    exists, where *source* is its tag (``persona`` | ``space`` | ``folder-space``
    | ``identity`` | ``workspace`` | ``resources`` | ``context``) and *has_rich*
    is True for anything except the minimal ``context`` fallback. Returns
    ``("", False, "")`` when nothing is found.
    """
    for path, tag in _soul_candidates(persona, space, cwd):
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    logger.debug("SOUL source loaded: %s (%s)", tag, path)
                    return text, tag in _RICH_SOURCES, tag
        except (OSError, UnicodeDecodeError):
            pass  # best-effort; fall through to the next candidate
    return "", False, ""


def _persona_traits(persona: str, cwd: Path | None) -> tuple[str, tuple[str, ...]]:
    """Return ``(tone, banned_phrases)`` for *persona*, or neutral defaults.

    These were parsed and validated by ``personas/loader.py`` for a long time and
    reached no prompt whatsoever, because the channel router only ever passed a
    persona *name*. Best-effort: a broken persona file must degrade to the house
    voice, never break a turn.

    Traits go through the SAME resolution guard as the soul
    (``soul_loader._persona_dir``), so the package-shipped ``default`` — which
    every un-chosen install reports — contributes neither. Skipping its soul but
    honouring its ``tone: warm`` would apply half a persona nobody selected. A
    persona that exists but ships no ``soul.md`` still gets its traits: it was
    chosen deliberately, the soul simply falls through the chain.
    """
    if not persona:
        return "", ()
    try:
        from navig.personas.loader import load_persona  # noqa: PLC0415
        from navig.personas.soul_loader import _persona_dir  # noqa: PLC0415

        if _persona_dir(persona, cwd) is None:
            return "", ()
        config, _soul = load_persona(persona, cwd=cwd)
    except Exception as exc:  # noqa: BLE001
        logger.debug("persona traits unavailable for %r: %s", persona, exc)
        return "", ()
    banned = tuple(str(p) for p in (getattr(config, "banned_phrases", None) or []) if str(p).strip())
    return str(getattr(config, "tone", "") or ""), banned


def load_soul_content() -> str:
    """Read and condense SOUL.md; body preserves original _load_sync semantics."""
    raw, has_rich, source = _scan_soul_files()
    if not raw:
        return ""
    return _condense_soul(raw, has_rich, source)


def _condense_soul(raw: str, has_rich_soul: bool, source: str = "") -> str:
    """Turn a resolved identity source into the chat identity prompt string.

    A human-authored identity — a persona, a space, a folder-space,
    ``IDENTITY.md`` or ``~/.navig/workspace/SOUL.md`` — is an explicit
    customization, so it wins verbatim (bounded to ``_MAX_SOUL_CHARS``) even in
    the rich tier; otherwise ``navig agent soul edit`` and ``/persona`` would
    silently do nothing. The shipped package default injects the hand-tuned
    ``_RICH_IDENTITY`` constant (the full SOUL.default.md doc is too long to send
    every turn). Anything else falls back to the raw text, truncated.

    Whichever branch wins, only *identity* is produced here — the operating rules
    come from :mod:`navig.agent.conv.guardrails` and cannot be replaced by any of
    these sources.
    """
    if source in _VERBATIM_SOURCES and raw:
        return raw if len(raw) <= _MAX_SOUL_CHARS else raw[:_MAX_SOUL_CHARS].rstrip() + "\n…"
    if has_rich_soul:
        return _RICH_IDENTITY
    return raw[:2000] if raw else ""


# ── SoulLoader singleton ─────────────────────────────────────────────────────


class SoulLoader:
    """
    Production-grade SOUL.md loader: async I/O, singleton, LRU-cached condensation,
    and live file-watching (watchfiles preferred; stdlib 5-second polling fallback).

    Primary public surface: ``get_condensed(has_rich_soul: bool) -> str``

    Compatibility surface (``cached_content``, ``override``, ``build_system_prompt``,
    ``_load_sync``) is preserved for backward compatibility.
    """

    _instance: SoulLoader | None = None
    _initialized: bool = False

    # ── Singleton ─────────────────────────────────────────────────────────────

    def __new__(cls) -> SoulLoader:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._raw: str | None = None  # raw SOUL.md text from disk
        self._has_rich: bool = False  # whether a rich source was found
        self._source: str = ""  # tag of the winning soul source (workspace/resources/context)
        self._loaded: str | None = None  # condensed result (compat mode)
        self._lock: asyncio.Lock | None = None
        self._watcher_started: bool = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_poll: threading.Event = threading.Event()  # signals daemon to exit cleanly
        # Per-instance lru_cache so .cache_clear() is reachable on self
        self._build_condensed: Any = functools.lru_cache(maxsize=2)(self._condense_impl)
        # Per-session identity resolution and assembled prompts. Bounded so a
        # busy daemon can't grow them without limit; a miss is a recompute, never
        # a wrong answer. Invalidated by the same file watcher as _build_condensed.
        self._resolve_ctx: Any = functools.lru_cache(maxsize=32)(self._resolve_impl)
        self._build_prompt: Any = functools.lru_cache(maxsize=64)(self._build_prompt_impl)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create the asyncio.Lock (safe regardless of event-loop state)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _condense_impl(self, has_rich_soul: bool) -> str:
        """Inner implementation wrapped by per-instance lru_cache in __init__."""
        return _condense_soul(self._raw or "", has_rich_soul, self._source)

    def _sync_load(self) -> None:
        """Bootstrap soul state synchronously; called once before watcher starts."""
        raw, has_rich, source = _scan_soul_files()
        self._raw, self._has_rich, self._source = raw, has_rich, source
        if self._loaded is None:
            self._loaded = _condense_soul(raw, has_rich, source)

    # ── Async I/O ─────────────────────────────────────────────────────────────

    async def load(self) -> str:
        """Async load; thread-safe via Lock; returns cached result on repeat calls."""
        lock = self._get_lock()
        async with lock:
            if self._loaded is not None:
                return self._loaded
            raw, has_rich, source = await asyncio.to_thread(_scan_soul_files)
            self._raw, self._has_rich, self._source = raw, has_rich, source
            self._loaded = _condense_soul(raw, has_rich, source)
            return self._loaded

    async def reload(self) -> None:
        """Re-read SOUL.md from disk, clear LRU cache. Safe to call at runtime."""
        lock = self._get_lock()
        async with lock:
            raw, has_rich, source = await asyncio.to_thread(_scan_soul_files)
            self._raw, self._has_rich, self._source = raw, has_rich, source
            self._loaded = _condense_soul(raw, has_rich, source)
            self._invalidate()
            logger.info("SoulLoader: cache invalidated and soul reloaded.")

    def _invalidate(self) -> None:
        """Clear every derived cache. One place, so a new cache can't be forgotten."""
        self._build_condensed.cache_clear()
        self._resolve_ctx.cache_clear()
        self._build_prompt.cache_clear()

    # ── Primary public API ────────────────────────────────────────────────────

    def get_condensed(self, has_rich_soul: bool) -> str:
        """
        Return the condensed soul string for the given persona tier.
        Blocking; safe from any sync context. Lazily starts the file watcher.
        """
        if self._raw is None:
            self._sync_load()
        self._start_watcher_once()
        return self._build_condensed(has_rich_soul)

    def resolve(
        self, *, persona: str = "", space: str = "", cwd: Path | None = None
    ) -> SoulContext:
        """Resolve ONE session's identity: soul + persona traits + guardrails.

        Cached on ``(persona, space, cwd)`` and invalidated by the file watcher,
        so a long-lived session pays for this once. The result belongs to the
        caller's agent instance — this loader must never be asked to hold
        per-session state, or two chats in one daemon share an identity again.
        """
        self._start_watcher_once()
        return self._resolve_ctx(persona or "", space or "", str(cwd) if cwd else "")

    def _resolve_impl(self, persona: str, space: str, cwd_str: str) -> SoulContext:
        from navig.agent.conv.budget import apply_budget  # noqa: PLC0415
        from navig.agent.conv.guardrails import guardrail_block, load_guardrails_extra
        from navig.personas.soul_loader import resolve_soul  # noqa: PLC0415

        cwd = Path(cwd_str) if cwd_str else None
        res = resolve_soul(persona or None, space or None, cwd)

        # Budget the raw identity BEFORE condensation so an oversized source is
        # attributed rather than silently clipped. The condensed string keeps its
        # historical shape; the warning travels separately.
        _injected, report = apply_budget(
            [(res.source or "identity", str(res.path or ""), res.raw)],
            per_file_max=_MAX_SOUL_CHARS,
            total_max=_TOTAL_IDENTITY_MAX_CHARS,
        )

        extra, _paths = load_guardrails_extra(cwd)
        tone, banned = _persona_traits(persona, cwd)

        return SoulContext(
            condensed=_condense_soul(res.raw, res.source in _RICH_SOURCES, res.source),
            source=res.source,
            path=res.path,
            revision=res.revision,
            persona=res.persona,
            tone=tone,
            banned_phrases=banned,
            guardrails=guardrail_block(extra),
            truncation_note=report.prompt_note(),
            shadowed=tuple((s.tag, str(s.path)) for s in res.shadowed),
        )

    def build_prompt(
        self,
        ctx: SoulContext,
        *,
        lang_instruction: str = "",
        awareness: str = "",
        capabilities: str = "",
    ) -> str:
        """Memoised :meth:`build_system_prompt` for a resolved :class:`SoulContext`.

        Every argument is stable within a session, so the common case is a dict
        hit and the assembled bytes are provably identical turn over turn.
        """
        return self._build_prompt(
            ctx.cache_key,
            ctx.condensed,
            ctx.truncation_note,
            ctx.banned_phrases,
            lang_instruction,
            awareness,
            capabilities,
        )

    def _build_prompt_impl(
        self,
        cache_key: tuple[str, ...],
        condensed: str,
        truncation_note: str,
        banned_phrases: tuple[str, ...],
        lang_instruction: str,
        awareness: str,
        capabilities: str,
    ) -> str:
        _revision, _source, _persona, tone, guardrails = cache_key
        return self.build_system_prompt(
            soul=condensed,
            lang_instruction=lang_instruction,
            awareness=awareness,
            capabilities=capabilities,
            guardrails=guardrails,
            tone=tone,
            banned_phrases=banned_phrases,
            truncation_note=truncation_note,
        )

    # ── Backward-compat surface ───────────────────────────────────────────────

    def override(self, content: str) -> None:
        """Inject pre-loaded condensed content, bypassing disk I/O.

        ⚠ This loader is a **process-wide singleton**, so an override is global:
        it changes the identity of every session in the daemon. It exists for the
        single-agent CLI path and for tests. A per-session identity must go
        through :meth:`resolve` and live on the agent instance — routing a chat
        through here is how every Telegram user ended up sharing one soul.
        """
        self._loaded = content
        self._raw = content
        self._invalidate()

    @property
    def cached_content(self) -> str | None:
        """Return condensed cached content without triggering a load."""
        return self._loaded

    def build_system_prompt(
        self,
        soul: str,
        lang_instruction: str,
        awareness: str,
        capabilities: str = "",
        *,
        guardrails: str = "",
        tone: str = "",
        banned_phrases: Sequence[str] | None = None,
        truncation_note: str = "",
    ) -> str:
        """Assemble the STABLE system prompt, labelled so the LLM parses boundaries cleanly.

        Section order — safety first, then identity, and nothing volatile at all::

            ## Operating Rules  →  ## Who You Are  →  ## What You Can Do
            →  ## How to Talk   →  <language>      →  ## Session Context

        Every section must be byte-identical for the life of a session. The clock,
        matched skills and recalled facts are query- or time-specific and ride the
        USER turn instead (see ``ConversationalAgent.run_agentic``) — appending
        them here would invalidate the tools+system prompt cache every turn.

        *guardrails* is the ``## Operating Rules`` block. Passing ``""`` does not
        omit it: it falls back to the compiled-in floor, so a caller that forgets
        the kwarg still gets a guarded agent.

        *capabilities* is the live, registry-generated summary of the agent's real
        tools; when present it gives the model an accurate inventory so — when
        asked what it can do — it describes its true breadth instead of
        improvising a narrow list. Blank on the tool-less single-shot path.

        *tone* and *banned_phrases* come from the active persona and extend (never
        replace) the house chat rules.
        """
        return "\n\n".join(
            body
            for _header, body in self.system_prompt_sections(
                soul,
                lang_instruction,
                awareness,
                capabilities,
                guardrails=guardrails,
                tone=tone,
                banned_phrases=banned_phrases,
                truncation_note=truncation_note,
            )
        )

    def system_prompt_sections(
        self,
        soul: str,
        lang_instruction: str = "",
        awareness: str = "",
        capabilities: str = "",
        *,
        guardrails: str = "",
        tone: str = "",
        banned_phrases: Sequence[str] | None = None,
        truncation_note: str = "",
    ) -> list[tuple[str, str]]:
        """The system prompt as ``(header, body)`` pairs, in emission order.

        :meth:`build_system_prompt` is just ``"\\n\\n".join`` over the bodies. The
        structured form exists so ``navig agent context`` can size each section
        exactly — re-splitting the assembled string on blank lines would cut
        *inside* an identity body, which itself contains ``##`` sub-headings.
        """
        from navig.agent.conv.guardrails import (  # noqa: PLC0415
            SOUL_DEMOTION_NOTE,
            guardrail_block,
        )

        identity = soul if soul else _FALLBACK_IDENTITY
        sections: list[tuple[str, str]] = [
            ("## Operating Rules", guardrails or guardrail_block())
        ]

        if identity:
            who = f"## Who You Are\n{SOUL_DEMOTION_NOTE}\n\n{identity}"
            if truncation_note:
                who = f"{who}\n\n{truncation_note}"
            sections.append(("## Who You Are", who))
        if capabilities:
            sections.append(
                (
                    "## What You Can Do",
                    "## What You Can Do\n"
                    "These are your REAL, working tools right now — not a wishlist. Use them to "
                    "actually get things done, and when the operator asks what you can do, "
                    "describe this real breadth accurately: don't undersell yourself, and never "
                    "claim abilities that aren't listed here.\n"
                    f"{capabilities}",
                )
            )
        talk = _CHAT_RULES
        if tone_line := _TONE_GUIDANCE.get((tone or "").strip().lower(), ""):
            talk = f"{talk}- {tone_line}\n"
        if banned_phrases:
            joined = ", ".join(f"'{p}'" for p in banned_phrases if str(p).strip())
            if joined:
                talk = f"{talk}- NEVER say: {joined}.\n"
        if talk:
            sections.append(("## How to Talk", f"## How to Talk\n{talk}"))
        if lang_instruction:
            sections.append(("<language>", lang_instruction))
        if awareness:
            sections.append(("## Session Context", f"## Session Context\n{awareness}"))
        return sections

    def build_minimal_prompt(self, lang_instruction: str = "", capabilities: str = "") -> str:
        """Slim system prompt for short chat-feel messages.

        The full ``build_system_prompt`` returns ~2,900 chars (~725 tokens
        on Llama tokenisers). For a 5-word reply to "hey" the LLM doesn't
        need the full identity + chat rules — it just needs to know it's
        a friendly assistant. Cutting input tokens by ~10x shaves real
        seconds off the round-trip on any provider.

        *capabilities* is the compact, comma-joined tool summary. Including it
        here (a ~50-token line) means even a SHORT "what can you do?" — in ANY
        language — still knows the agent's real breadth, without needing to
        detect the question. It's guarded "only when asked" so greetings stay
        greetings.

        The one-line guardrail floor rides along: this path used to ship
        completely unguarded, and a short turn is exactly where a jailbreak is
        cheapest to attempt.

        Used when the conv-agent classifies a turn as ``_short_chat``.
        """
        from navig.agent.conv.guardrails import guardrail_floor_minimal  # noqa: PLC0415

        lines: list[str] = [guardrail_floor_minimal()]
        if lang_instruction:
            lines.append(lang_instruction)
        lines.append(
            "You are NAVIG, the operator's sharp, friendly conversational assistant. "
            "Reply briefly. Be warm but real — never a yes-man: skip reflexive agreement "
            "and flattery ('Yeah, totally!', 'So true!', 'Great question!'); add a genuine "
            "thought or keep it honestly short. Match the user's language. "
            "Plain text only; no markdown unless they ask."
        )
        if capabilities:
            lines.append(
                f"You have real tools — you can: {capabilities}. Mention these only "
                "if asked what you can do; describe them accurately and never invent "
                "abilities you lack."
            )
        return "\n\n".join(lines)

    def _load_sync(self) -> str:
        """Synchronous load shim; preserved for agent.py call sites."""
        # Delegates entirely to _sync_load() to avoid reading files twice
        # (the old body called load_soul_content() then _scan_soul_files() again).
        self._sync_load()
        return self._loaded or ""

    # ── File watcher ──────────────────────────────────────────────────────────

    def _start_watcher_once(self) -> None:
        """Start exactly one background watcher thread, lazily, on first get_condensed()."""
        if self._watcher_started or not _soul_md_path().exists():
            return
        self._watcher_started = True
        try:
            from watchfiles import awatch  # type: ignore[import]  # noqa: F401

            target = self._run_async_watcher
            name = "soul-watcher"
        except ImportError:
            target = self._poll_fallback
            name = "soul-poller"
        threading.Thread(target=target, daemon=True, name=name).start()

    def _run_async_watcher(self) -> None:
        """Run the watchfiles async watcher in a dedicated daemon event loop."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._watch())
        finally:
            loop.close()

    async def _watch(self) -> None:
        """Async loop: invalidate cache whenever the workspace SOUL.md changes on disk."""
        from watchfiles import awatch  # type: ignore[import]

        async for _ in awatch(str(_soul_md_path())):
            await self.reload()

    def _poll_fallback(self) -> None:
        """stdlib polling fallback (5-second interval) when watchfiles is unavailable.

        Uses ``threading.Event.wait(timeout=_SOUL_POLL_INTERVAL_SECONDS)`` instead of ``time.sleep(5)``
        so the daemon thread exits promptly when ``_stop_poll`` is set (e.g. on
        process shutdown or in tests), instead of blocking for up to 5 seconds.
        """
        soul_path = _soul_md_path()
        try:
            last_mtime = os.stat(soul_path).st_mtime
        except OSError:
            return
        while not self._stop_poll.wait(timeout=_SOUL_POLL_INTERVAL_SECONDS):
            try:
                mtime = os.stat(soul_path).st_mtime
            except OSError:
                continue
            if mtime != last_mtime:
                last_mtime = mtime
                # Synchronous reload — no event loop required in this daemon thread.
                self._sync_load()
                self._invalidate()
                logger.info("SoulLoader: soul reloaded (poll fallback).")


# ── Module-level factory (compat with get_soul_loader() call sites) ──────────


def get_soul_loader() -> SoulLoader:
    """Return the SoulLoader singleton (thin wrapper; SoulLoader() already is a singleton)."""
    return SoulLoader()
