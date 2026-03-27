"""Static file server for Deck SPA."""

import logging
from pathlib import Path

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _find_deck_static_dir(override: str | None = None) -> Path | None:
    if override:
        p = Path(override).expanduser()
        if p.is_dir() and (p / "index.html").exists():
            return p
        logger.warning("Deck static_dir override not found: %s", override)

    candidates = [
        Path(__file__).parent.parent.parent.parent.parent / "deck-static",
        Path.home() / "navig-core" / "deck-static",
        Path(__file__).parent.parent.parent.parent.parent.parent / "navig-deck" / "dist",
    ]
    for p in candidates:
        if p.is_dir() and (p / "index.html").exists():
            return p
    return None


async def handle_deck_index(request: "web.Request") -> "web.Response":
    static_dir = _find_deck_static_dir()
    if not static_dir:
        return web.Response(text="Deck not built. Run: cd navig-deck && npm run build", status=404)
    return web.FileResponse(static_dir / "index.html")
