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
    #
    # `repo` is the monorepo root: routes -> deck -> gateway -> navig -> core -> root.
    # Every entry below used to name `navig-deck/`, the PRE-monorepo sibling repo, so this
    # list had no monorepo path at all and the gateway could not serve a locally-built deck
    # after the migration. `commands/miniapp.py` carried the identical bug in the function
    # this one is documented as mirroring — one class, two copies, fixed together.
    core_dir = Path(__file__).parent.parent.parent.parent.parent
    repo = core_dir.parent
    candidates = [
        core_dir / "deck-static",
        Path.home() / "navig-core" / "deck-static",  # dead-path-ok: harmless stale fallback candidate
        repo / "apps" / "deck" / "out",
        repo / "apps" / "deck" / "dist",
        # Also handle the wheel-builder's pre-copy staging dir for local CI tests
        repo / "apps" / "deck" / "python" / "navig_deck" / "static",
        # Legacy polyrepo checkouts, tried after the monorepo layout (same order as
        # miniapp.py's deck-source search).
        repo / "navig-deck" / "out",  # dead-path-ok: deliberate legacy-checkout fallback
        repo / "navig-deck" / "dist",  # dead-path-ok: deliberate legacy-checkout fallback
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
