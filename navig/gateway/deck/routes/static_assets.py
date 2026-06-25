"""Static file server for Deck SPA."""

import logging
from pathlib import Path

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _find_deck_static_dir(override: str | None = None) -> Path | None:
    """Locate the Deck SPA's static bundle.

    Resolution order:
      1. Explicit ``--static-dir`` / config override
      2. Installed ``navig-deck`` PyPI wheel (primary path for end users —
         ``pip install navig`` pulls navig-deck as a dependency, which
         installs the compiled out/ as package data at
         ``site-packages/navig_deck/static/``)
      3. Dev tree neighbours (for monorepo development)
      4. None — daemon serves a friendly 404 from the static_assets handler
    """
    if override:
        p = Path(override).expanduser()
        if p.is_dir() and (p / "index.html").exists():
            return p
        logger.warning("Deck static_dir override not found: %s", override)

    # 2. Installed wheel — the primary distribution path.
    try:
        import navig_deck  # type: ignore[import-not-found]
        installed = navig_deck.static_dir()
        if installed.is_dir() and (installed / "index.html").is_file():
            return installed
    except ImportError:
        pass  # navig-deck not installed; fall through to dev tree
    except Exception as exc:  # noqa: BLE001
        logger.debug("navig_deck.static_dir() raised %r", exc)

    # 3. Dev-tree neighbours for monorepo work.
    candidates = [
        Path(__file__).parent.parent.parent.parent.parent / "deck-static",
        Path.home() / "navig-core" / "deck-static",
        Path(__file__).parent.parent.parent.parent.parent.parent / "navig-deck" / "out",
        Path(__file__).parent.parent.parent.parent.parent.parent / "navig-deck" / "dist",
        # Also handle the wheel-builder's pre-copy staging dir for local CI tests
        Path(__file__).parent.parent.parent.parent.parent.parent / "navig-deck" / "python" / "navig_deck" / "static",
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
