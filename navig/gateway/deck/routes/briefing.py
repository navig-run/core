"""Daily briefing for the Deck — a structured, categorized status report.

Composes facts from every available subsystem (finance, life, system, inbox,
spaces) into category sections, then polishes each with the LLM into a crisp
narrative.

    GET  /api/deck/briefing             → latest briefing (builds if stale/absent)
    POST /api/deck/briefing/regenerate  → rebuild now and return it

**Freshness:** the cache used to have NO expiry — `_load_cache()` returned the
file forever, so the "Daily" briefing was frozen at whenever it was first built
and only the Regenerate button ever moved it. It went on quoting a revenue
figure from a past state while the Finance app showed the real one. A briefing
is now rebuilt when it is from a previous day or older than ``_MAX_AGE``.

Registered in ``navig/gateway/deck/__init__.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

_CACHE: dict | None = None

# A briefing older than this (or from a previous calendar day) is rebuilt on the
# next read. Bounds the LLM polish to a handful of calls a day while keeping the
# numbers honest — the whole point of the thing.
_MAX_AGE = timedelta(hours=6)

# One build at a time: concurrent dashboard loads must not each fire the LLM.
_BUILD_LOCK: asyncio.Lock | None = None

# Strong refs to in-flight background rebuilds (a bare create_task() can be
# garbage-collected mid-run).
_BG_TASKS: set = set()


def _build_lock() -> asyncio.Lock:
    global _BUILD_LOCK
    if _BUILD_LOCK is None:
        _BUILD_LOCK = asyncio.Lock()
    return _BUILD_LOCK


def _is_stale(briefing: dict | None) -> bool:
    """True when the cached briefing is from a previous day or past _MAX_AGE.

    An unparseable/missing timestamp counts as stale — better one extra build
    than serving a frozen briefing forever (the bug this replaces).
    """
    if not isinstance(briefing, dict):
        return True
    raw = briefing.get("generated_at")
    if not raw:
        return True
    try:
        made = datetime.fromisoformat(str(raw))
    except ValueError:
        return True
    now = datetime.now()
    if made.tzinfo is not None:  # naive on write, but be tolerant of old files
        made = made.replace(tzinfo=None)
    return made.date() != now.date() or (now - made) > _MAX_AGE


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _gateway(request: "web.Request"):
    return request.app.get("gateway") if hasattr(request, "app") else None


def _cache_path():
    try:
        from navig.platform import paths

        return paths.data_dir() / "briefing.json"
    except Exception:
        return None


def _greeting() -> str:
    h = datetime.now().hour
    if h < 5:
        return "Still going"
    if h < 12:
        return "Good morning"
    if h < 18:
        return "Good afternoon"
    return "Good evening"


# ── Category fact gatherers (each best-effort; returns a section or None) ──────


def _section(sid: str, title: str, icon: str, items: list[str], tone: str = "neutral") -> dict | None:
    items = [str(i).strip() for i in items if str(i).strip()]
    if not items:
        return None
    return {"id": sid, "title": title, "icon": icon, "items": items[:8], "tone": tone, "summary": ""}


def _finance() -> dict | None:
    try:
        from navig_harbor import bizops

        snap = bizops.get_overview()
        if not isinstance(snap, dict):
            return None

        # The snapshot's figures are folded into ITS base currency — narrating a
        # EUR ledger in dollars is the same lie the Finance app just stopped
        # telling.
        ccy = str(snap.get("currency") or "USD").upper()
        prefix = "$" if ccy == "USD" else f"{ccy} "

        def money(cents) -> str:
            try:
                c = float(cents)
            except (TypeError, ValueError):
                return "—"
            sign = "-" if c < 0 else ""
            a = abs(c)
            # cents → unit: 1k = 100_000 cents, 1M = 100_000_000 cents.
            if a >= 1_000_000_00:
                return f"{sign}{prefix}{a / 1_000_000_00:.1f}M"
            if a >= 1_000_00:
                return f"{sign}{prefix}{a / 1_000_00:.1f}k"
            return f"{sign}{prefix}{a / 100:.2f}"

        items: list[str] = []
        if snap.get("total_cash_cents") is not None:
            items.append(f"Cash on hand: {money(snap['total_cash_cents'])}")
        if snap.get("runway_months"):
            items.append(f"Runway: {float(snap['runway_months']):.1f} months")
        if snap.get("monthly_revenue_cents") is not None:
            items.append(f"Revenue (mo): {money(snap['monthly_revenue_cents'])}")
        if snap.get("net_profit_cents") is not None:
            items.append(f"Net profit (mo): {money(snap['net_profit_cents'])}")
        if snap.get("open_invoices_count"):
            items.append(f"Open invoices: {snap['open_invoices_count']} ({money(snap.get('open_invoices_cents', 0))})")
        if snap.get("overdue_invoices_count"):
            items.append(f"⚠ Overdue invoices: {snap['overdue_invoices_count']}")
        tone = "bad" if snap.get("overdue_invoices_count") else "good" if (snap.get("net_profit_cents") or 0) >= 0 else "warn"
        return _section("finance", "Finance", "💼", items, tone)
    except Exception:
        logger.debug("briefing finance failed", exc_info=True)
        return None


def _life() -> dict | None:
    try:
        from navig.commands.life_dashboard import build_dashboard

        d = build_dashboard()
        txt = d if isinstance(d, str) else (d.get("text") if isinstance(d, dict) else "")
        lines = [ln.strip(" -•\t") for ln in str(txt).splitlines() if ln.strip()]
        return _section("life", "Life & Habits", "🌿", lines, "neutral")
    except Exception:
        logger.debug("briefing life failed", exc_info=True)
        return None


def _spaces() -> dict | None:
    try:
        from navig.spaces.briefing import build_spaces_briefing_lines

        lines = build_spaces_briefing_lines()
        if isinstance(lines, (list, tuple)):
            rows = [str(x) for x in lines]
        else:
            rows = [ln for ln in str(lines).splitlines() if ln.strip()]
        return _section("spaces", "Spaces & Knowledge", "🧠", rows, "neutral")
    except Exception:
        logger.debug("briefing spaces failed", exc_info=True)
        return None


def _system() -> dict | None:
    try:
        import psutil

        items: list[str] = []
        cpu = psutil.cpu_percent(interval=0.0)
        ram = psutil.virtual_memory()
        items.append(f"CPU load: {cpu:.0f}%")
        items.append(f"Memory: {ram.percent:.0f}% used ({ram.used / 1e9:.1f} / {ram.total / 1e9:.1f} GB)")

        # The SYSTEM drive only — never `psutil.disk_partitions()`.
        #
        # That call blocks indefinitely on a machine with a cold/disconnected
        # network drive (measured here: >100s, never returned) and it does NOT
        # release the GIL, so building a briefing froze the WHOLE gateway — every
        # endpoint timed out until the daemon was restarted. `monitor` already
        # learned this and exposes the fast system-drive path; use it.
        from navig.commands.monitor import get_system_disk

        for d in get_system_disk():
            items.append(f"Disk {d['mountpoint']}: {d['percent']:.0f}% used")

        tone = "bad" if (ram.percent >= 90 or cpu >= 90) else "warn" if ram.percent >= 75 else "good"
        return _section("system", "System & Infra", "🖥️", items, tone)
    except Exception:
        logger.debug("briefing system failed", exc_info=True)
        return None


def _inbox(gw) -> dict | None:
    try:
        items: list[str] = []
        am = getattr(gw, "approval_manager", None) if gw else None
        reg = getattr(gw, "request_registry", None) if gw else None
        approvals = len(am.get_pending()) if am else 0
        questions = len(reg.get_pending()) if reg else 0
        if approvals:
            items.append(f"{approvals} approval{'s' if approvals != 1 else ''} awaiting your decision")
        if questions:
            items.append(f"{questions} question{'s' if questions != 1 else ''} from navig")
        # Pending inbox documents (best-effort live scan)
        try:
            from navig.gateway.deck.routes.inbox import _find_project_root, _scan_inbox_dirs

            pending_docs = len(_scan_inbox_dirs(_find_project_root()))
            if pending_docs:
                items.append(f"{pending_docs} document{'s' if pending_docs != 1 else ''} waiting to be routed")
        except Exception:
            pass
        if not items:
            items.append("Inbox clear — nothing needs your attention.")
        tone = "warn" if (approvals or questions) else "good"
        return _section("inbox", "Inbox & Asks", "📥", items, tone)
    except Exception:
        logger.debug("briefing inbox failed", exc_info=True)
        return None


def _gather(gw) -> list[dict]:
    out = []
    for fn in (lambda: _inbox(gw), _finance, _life, _spaces, _system):
        try:
            sec = fn()
        except Exception:
            sec = None
        if sec:
            out.append(sec)
    return out


# ── LLM polish ────────────────────────────────────────────────


def _polish(sections: list[dict]) -> tuple[str, bool]:
    """Return (headline, ai_polished). Writes section['summary'] in place."""
    facts = {s["id"]: {"title": s["title"], "items": s["items"]} for s in sections}
    try:
        from navig.llm.generate import llm_generate

        out = llm_generate(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are NAVIG's daily briefer. Given JSON facts grouped by category, "
                        "write a crisp daily report. Respond ONLY with JSON of the form "
                        '{"headline": "<one punchy sentence>", "sections": {"<id>": "<1-2 sentence narrative>"}}. '
                        "Be factual and specific, reference the numbers, no filler, no preamble."
                    ),
                },
                {"role": "user", "content": json.dumps(facts)[:4000]},
            ],
            mode="summarize",
            temperature=0.4,
            max_tokens=600,
        )
        data = json.loads((out or "").strip().strip("`"))
        summaries = data.get("sections", {}) if isinstance(data, dict) else {}
        for s in sections:
            # Per-section narrative, falling back to its own facts if the model
            # skipped this category.
            s["summary"] = str(summaries.get(s["id"], "")).strip() or " · ".join(s["items"][:3])
        headline = str(data.get("headline", "")).strip()
        if headline:
            return headline, True
    except Exception:
        logger.debug("briefing polish failed", exc_info=True)

    # Fallback — no LLM: derive headline + per-section summary from the facts.
    for s in sections:
        s["summary"] = " · ".join(s["items"][:3])
    headline = next((s["items"][0] for s in sections if s["items"]), "Here's where things stand.")
    return headline, False


def _build(gw) -> dict:
    sections = _gather(gw)
    headline, polished = _polish(sections)
    briefing = {
        "generated_at": datetime.now().isoformat(),
        "greeting": _greeting(),
        "headline": headline,
        "sections": sections,
        "ai_polished": polished,
    }
    global _CACHE
    _CACHE = briefing
    path = _cache_path()
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(briefing), encoding="utf-8")
        except Exception:
            pass
    return briefing


def _load_cache() -> dict | None:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    path = _cache_path()
    if path is not None and path.is_file():
        try:
            _CACHE = json.loads(path.read_text(encoding="utf-8"))
            return _CACHE
        except Exception:
            return None
    return None


async def _rebuild_in_background(gw) -> None:
    """Refresh the cache off the request path (the LLM polish takes minutes)."""
    lock = _build_lock()
    if lock.locked():
        return  # a rebuild is already running — don't queue a second one
    async with lock:
        try:
            await asyncio.to_thread(_build, gw)
            logger.debug("briefing refreshed in background")
        except Exception:
            logger.debug("background briefing rebuild failed", exc_info=True)


async def handle_deck_briefing(request: "web.Request") -> "web.Response":
    """Return the latest briefing; refresh it when stale.

    Stale-while-revalidate: a stale briefing is served IMMEDIATELY and refreshed
    in the background. Building blocks on an LLM polish that can take minutes —
    doing that on the request path would hang the dashboard's briefing card
    every time the cache expired (a worse bug than the stale copy it replaced).
    The card renders "as of <time>", so a stale read is visible, and the next
    load shows the fresh one.
    """
    cached = _load_cache()
    if cached is not None:
        if _is_stale(cached):
            task = asyncio.create_task(_rebuild_in_background(_gateway(request)))
            # Keep a reference so the task isn't garbage-collected mid-flight.
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)
        return _ok(cached)

    # Nothing cached at all — there is nothing to serve but a fresh build.
    async with _build_lock():
        fresh = _load_cache()
        if fresh is not None:
            return _ok(fresh)
        try:
            briefing = await asyncio.to_thread(_build, _gateway(request))
        except Exception as exc:
            logger.exception("briefing build failed")
            return _err(str(exc))
    return _ok(briefing)


async def handle_deck_briefing_regenerate(request: "web.Request") -> "web.Response":
    """Rebuild the briefing now (the dashboard regenerate button)."""
    async with _build_lock():
        try:
            briefing = await asyncio.to_thread(_build, _gateway(request))
        except Exception as exc:
            logger.exception("briefing regenerate failed")
            return _err(str(exc))
    return _ok(briefing)
