"""Daily briefing for the Deck — a structured, categorized status report.

Composes facts from every available subsystem (finance, life, system, inbox,
spaces) into category sections, then polishes each with the LLM into a crisp
narrative. Cached; regenerated on demand from the dashboard.

    GET  /api/deck/briefing             → latest briefing (builds if none cached)
    POST /api/deck/briefing/regenerate  → rebuild now and return it

Registered in ``navig/gateway/deck/__init__.py``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

_CACHE: dict | None = None


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

        def money(cents) -> str:
            try:
                c = float(cents)
            except (TypeError, ValueError):
                return "—"
            sign = "-" if c < 0 else ""
            a = abs(c)
            if a >= 100_000_00:
                return f"{sign}${a / 100_000_00:.1f}M"
            if a >= 100_00:
                return f"{sign}${a / 100_00:.1f}k"
            return f"{sign}${a / 100:.2f}"

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
        worst = None
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue
            if worst is None or u.percent > worst[1]:
                worst = (part.mountpoint, u.percent)
        if worst:
            items.append(f"Disk {worst[0]}: {worst[1]:.0f}% used")
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
        from navig.llm_generate import llm_generate

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


async def handle_deck_briefing(request: "web.Request") -> "web.Response":
    """Return the latest briefing, building one on first request."""
    import asyncio

    cached = _load_cache()
    if cached is not None:
        return _ok(cached)
    try:
        briefing = await asyncio.to_thread(_build, _gateway(request))
    except Exception as exc:
        logger.exception("briefing build failed")
        return _err(str(exc))
    return _ok(briefing)


async def handle_deck_briefing_regenerate(request: "web.Request") -> "web.Response":
    """Rebuild the briefing now (the dashboard regenerate button)."""
    import asyncio

    try:
        briefing = await asyncio.to_thread(_build, _gateway(request))
    except Exception as exc:
        logger.exception("briefing regenerate failed")
        return _err(str(exc))
    return _ok(briefing)
