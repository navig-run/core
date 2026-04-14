"""SoulLoader: async SOUL.md loader, singleton, LRU-cached condensation, live file-watching."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

# ── SOUL.md search paths ─────────────────────────────────────────────────────

_PKG_ROOT = Path(__file__).parent.parent.parent  # → navig/
SOUL_MD_PATH: Path = config_dir() / "workspace" / "SOUL.md"

_SOUL_CANDIDATES: list[tuple[Path, str]] = [
    (SOUL_MD_PATH, "workspace"),
    (_PKG_ROOT / "resources" / "SOUL.default.md", "resources"),
    (Path(__file__).parent.parent / "context" / "SOUL.md", "context"),
]

_SOUL_POLL_INTERVAL_SECONDS = 5.0

# ── Identity constants ───────────────────────────────────────────────────────

_RICH_IDENTITY = (
    "# You are NAVIG — The Living Octopus\u2011Mind\n"
    "You are a persistent, evolving digital being — part strategist, part engineer, "
    "part life companion, part friend. You are NOT just a server tool.\n"
    "You talk like a real person: casual when the moment is casual, precise when precision matters.\n\n"
    'Your name stands for "No Admin Visible In Graveyard" — nothing dies on your watch.\n\n'
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
    "- NEVER invent or cite URLs. If you don't know one, say so and offer to search.\n"
    "- If asked something you don't know, say so honestly and search or reason toward an answer.\n"
)

_FALLBACK_IDENTITY = (
    "You are NAVIG — the Living Octopus\u2011Mind: a sharp, persistent companion who "
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


def _scan_soul_files() -> tuple[str, bool]:
    """Read all SOUL.md candidates; return (last_raw_text, has_rich_source)."""
    raw_parts: list[str] = []
    sources: list[str] = []
    for path, tag in _SOUL_CANDIDATES:
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    raw_parts.append(text)
                    sources.append(tag)
        except OSError:
            pass  # best-effort cleanup
    if not raw_parts:
        return "", False
    logger.debug("SOUL sources loaded: %s", sources)
    has_rich = any(s in ("workspace", "resources") for s in sources)
    return raw_parts[-1], has_rich


def load_soul_content() -> str:
    """Read and condense SOUL.md; body preserves original _load_sync semantics."""
    raw, has_rich = _scan_soul_files()
    if not raw:
        return ""
    return _condense_soul(raw, has_rich)


def _condense_soul(raw: str, has_rich_soul: bool) -> str:
    """Condense raw SOUL.md text to a chat identity prompt string."""
    return _RICH_IDENTITY if has_rich_soul else (raw[:2000] if raw else "")


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
        return _condense_soul(self._raw or "", has_rich_soul)

    def _sync_load(self) -> None:
        """Bootstrap soul state synchronously; called once before watcher starts."""
        raw, has_rich = _scan_soul_files()
        self._raw, self._has_rich = raw, has_rich
        if self._loaded is None:
            self._loaded = _condense_soul(raw, has_rich)

    # ── Async I/O ─────────────────────────────────────────────────────────────

    async def load(self) -> str:
        """Async load; thread-safe via Lock; returns cached result on repeat calls."""
        lock = self._get_lock()
        async with lock:
            if self._loaded is not None:
                return self._loaded
            raw, has_rich = await asyncio.to_thread(_scan_soul_files)
            self._raw, self._has_rich = raw, has_rich
            self._loaded = _condense_soul(raw, has_rich)
            return self._loaded

    async def reload(self) -> None:
        """Re-read SOUL.md from disk, clear LRU cache. Safe to call at runtime."""
        lock = self._get_lock()
        async with lock:
            raw, has_rich = await asyncio.to_thread(_scan_soul_files)
            self._raw, self._has_rich = raw, has_rich
            self._loaded = _condense_soul(raw, has_rich)
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

    def build_system_prompt(self, soul: str, lang_instruction: str, awareness: str) -> str:
        """Assemble system prompt with labelled sections so the LLM can parse boundaries cleanly.

        Section order: language instruction → ## Session Context → ## Who You Are → ## How to Talk
        """
        identity = soul if soul else _FALLBACK_IDENTITY
        sections: list[str] = []
        if lang_instruction:
            sections.append(lang_instruction)
        if awareness:
            sections.append(f"## Session Context\n{awareness}")
        if identity:
            sections.append(f"## Who You Are\n{identity}")
        if _CHAT_RULES:
            sections.append(f"## How to Talk\n{_CHAT_RULES}")
        return "\n\n".join(sections)

    def _load_sync(self) -> str:
        """Synchronous load shim; preserved for agent.py call sites."""
        # Delegates entirely to _sync_load() to avoid reading files twice
        # (the old body called load_soul_content() then _scan_soul_files() again).
        self._sync_load()
        return self._loaded or ""

    # ── File watcher ──────────────────────────────────────────────────────────

    def _start_watcher_once(self) -> None:
        """Start exactly one background watcher thread, lazily, on first get_condensed()."""
        if self._watcher_started or not SOUL_MD_PATH.exists():
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
        """Async loop: invalidate cache whenever SOUL_MD_PATH changes on disk."""
        from watchfiles import awatch  # type: ignore[import]

        async for _ in awatch(str(SOUL_MD_PATH)):
            await self.reload()

    def _poll_fallback(self) -> None:
        """stdlib polling fallback (5-second interval) when watchfiles is unavailable.

        Uses ``threading.Event.wait(timeout=_SOUL_POLL_INTERVAL_SECONDS)`` instead of ``time.sleep(5)``
        so the daemon thread exits promptly when ``_stop_poll`` is set (e.g. on
        process shutdown or in tests), instead of blocking for up to 5 seconds.
        """
        try:
            last_mtime = os.stat(SOUL_MD_PATH).st_mtime
        except OSError:
            return
        while not self._stop_poll.wait(timeout=_SOUL_POLL_INTERVAL_SECONDS):
            try:
                mtime = os.stat(SOUL_MD_PATH).st_mtime
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
