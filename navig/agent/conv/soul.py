"""SoulLoader: async SOUL.md loader, singleton, LRU-cached condensation, live file-watching."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import threading
from pathlib import Path
from typing import Any

from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

# ── SOUL.md search paths ─────────────────────────────────────────────────────

_PKG_ROOT = Path(__file__).parent.parent.parent  # → navig/


def _soul_md_path() -> Path:
    """The user's workspace SOUL.md, resolved fresh each call so a late
    ``NAVIG_CONFIG_DIR`` (tests, reconfigure) is honored rather than captured at
    import time."""
    return config_dir() / "workspace" / "SOUL.md"


def _soul_candidates() -> list[tuple[Path, str]]:
    """SOUL.md search candidates in priority order (user → default → context),
    resolved fresh each call."""
    return [
        (_soul_md_path(), "workspace"),
        (_PKG_ROOT / "resources" / "SOUL.default.md", "resources"),
        (Path(__file__).parent.parent / "context" / "SOUL.md", "context"),
    ]

_SOUL_POLL_INTERVAL_SECONDS = 5.0

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

# ── Module-level I/O + condensation (bodies preserved per spec) ──────────────


# Cap for a user-authored soul injected verbatim into chat (keeps token cost sane
# while still honoring an explicit customization).
_MAX_SOUL_CHARS = 4000


def _scan_soul_files() -> tuple[str, bool, str]:
    """Read SOUL.md candidates in priority order (user → default → context).

    Returns ``(raw_text, has_rich, source)`` for the HIGHEST-priority source that
    exists, where *source* is its tag (``workspace`` | ``resources`` | ``context``)
    and *has_rich* is True for the user (``workspace``) or shipped (``resources``)
    identity. Returns ``("", False, "")`` when nothing is found.
    """
    for path, tag in _soul_candidates():
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    logger.debug("SOUL source loaded: %s (%s)", tag, path)
                    return text, tag in ("workspace", "resources"), tag
        except (OSError, UnicodeDecodeError):
            pass  # best-effort; fall through to the next candidate
    return "", False, ""


def load_soul_content() -> str:
    """Read and condense SOUL.md; body preserves original _load_sync semantics."""
    raw, has_rich, source = _scan_soul_files()
    if not raw:
        return ""
    return _condense_soul(raw, has_rich, source)


def _condense_soul(raw: str, has_rich_soul: bool, source: str = "") -> str:
    """Turn a SOUL source into the chat identity prompt string.

    A user-authored soul (``~/.navig/workspace/SOUL.md``) is an explicit
    customization, so it wins verbatim — bounded to ``_MAX_SOUL_CHARS`` — even in
    the rich tier; otherwise ``navig agent soul edit`` would silently do nothing.
    The shipped package default injects the hand-tuned ``_RICH_IDENTITY`` constant
    (the full SOUL.default.md doc is too long to send every turn). Anything else
    falls back to the raw text, truncated.
    """
    if source == "workspace" and raw:
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
            self._build_condensed.cache_clear()
            logger.info("SoulLoader: cache invalidated and soul reloaded.")

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

    # ── Backward-compat surface ───────────────────────────────────────────────

    def override(self, content: str) -> None:
        """Inject pre-loaded condensed content, bypassing disk I/O."""
        self._loaded = content
        self._raw = content
        self._build_condensed.cache_clear()

    @property
    def cached_content(self) -> str | None:
        """Return condensed cached content without triggering a load."""
        return self._loaded

    def build_system_prompt(
        self, soul: str, lang_instruction: str, awareness: str, capabilities: str = ""
    ) -> str:
        """Assemble system prompt with labelled sections so the LLM can parse boundaries cleanly.

        Section order: language instruction → ## Session Context → ## Who You Are
        → ## What You Can Do → ## How to Talk

        *capabilities* is the live, registry-generated summary of the agent's real
        tools; when present it gives the model an accurate inventory so — when
        asked what it can do — it describes its true breadth instead of
        improvising a narrow list. Blank on the tool-less single-shot path.
        """
        identity = soul if soul else _FALLBACK_IDENTITY
        sections: list[str] = []
        if lang_instruction:
            sections.append(lang_instruction)
        if awareness:
            sections.append(f"## Session Context\n{awareness}")
        if identity:
            sections.append(f"## Who You Are\n{identity}")
        if capabilities:
            sections.append(
                "## What You Can Do\n"
                "These are your REAL, working tools right now — not a wishlist. Use them to "
                "actually get things done, and when the operator asks what you can do, "
                "describe this real breadth accurately: don't undersell yourself, and never "
                "claim abilities that aren't listed here.\n"
                f"{capabilities}"
            )
        if _CHAT_RULES:
            sections.append(f"## How to Talk\n{_CHAT_RULES}")
        return "\n\n".join(sections)

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

        Used when the conv-agent classifies a turn as ``_short_chat``.
        """
        lines: list[str] = []
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
                self._build_condensed.cache_clear()
                logger.info("SoulLoader: soul reloaded (poll fallback).")


# ── Module-level factory (compat with get_soul_loader() call sites) ──────────


def get_soul_loader() -> SoulLoader:
    """Return the SoulLoader singleton (thin wrapper; SoulLoader() already is a singleton)."""
    return SoulLoader()
