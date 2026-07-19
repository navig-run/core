"""/api/deck/spaces resolves the active space through the ONE canonical reader.

Regression: the handler used to read ONLY the cache file inline, so a config-key-mirror-but-
no-cache-file state (config.yaml has space.active, cache file absent) resolved to the discovery
default instead of the real space — and it ignored NAVIG_SPACE. It now delegates to
`resolve_active_space` (env → cache file → config keys), keeping the discovery-default fallback.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import context as context_mod

    app = web.Application()
    app.router.add_get("/spaces", context_mod.handle_deck_spaces)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    from navig.config import reset_config_manager

    reset_config_manager()
    yield tmp_path
    reset_config_manager()


async def _get_active(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        r = await client.get("/spaces")
        assert r.status == 200
        body = await r.json()
        assert body["ok"] is True
        return body["data"]["active"]


async def test_spaces_active_resolves_from_config_when_no_cache_file(isolated):
    """The gap the old inline reader missed: config mirror set, cache file absent."""
    from navig.config import get_config_manager

    get_config_manager().set_global("space.active", "cfg-space")

    assert await _get_active(isolated) == "cfg-space"


async def test_spaces_active_reads_cache_file(isolated):
    from navig.platform.paths import config_dir

    cache = config_dir() / "cache" / "active_space.txt"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("cached-space", encoding="utf-8")

    assert await _get_active(isolated) == "cached-space"


async def test_spaces_active_falls_back_to_default_when_unset(isolated):
    # Nothing set anywhere → the discovery default ("default", since NAVIG_SPACE is cleared).
    assert await _get_active(isolated) == "default"


def test_file_count_prunes_noise_dirs(tmp_path):
    """The count is the space's own content — build/VCS/cache dirs are pruned, `.navig` kept."""
    from navig.gateway.deck.routes.context import _file_count_in

    space = tmp_path / "space"
    (space / ".navig").mkdir(parents=True)
    (space / ".navig" / "plan.md").write_text("x", encoding="utf-8")  # counts (space content)
    (space / "src").mkdir()
    (space / "src" / "app.py").write_text("x", encoding="utf-8")      # counts
    (space / "README.md").write_text("x", encoding="utf-8")           # counts
    # Noise — must NOT be counted, and must NOT be descended into.
    for noise in ("node_modules", ".git", "__pycache__", "dist"):
        d = space / noise
        d.mkdir()
        for i in range(50):
            (d / f"f{i}.js").write_text("x", encoding="utf-8")

    assert _file_count_in(space) == 3


def test_file_count_missing_dir_is_zero(tmp_path):
    from navig.gateway.deck.routes.context import _file_count_in

    assert _file_count_in(tmp_path / "does-not-exist") == 0


def test_file_count_caps_large_tree(tmp_path):
    """The cap bounds the walk; a huge non-pruned tree returns exactly max_walk, not more."""
    from navig.gateway.deck.routes.context import _file_count_in

    big = tmp_path / "big"
    big.mkdir()
    for i in range(30):
        (big / f"f{i}.txt").write_text("x", encoding="utf-8")

    assert _file_count_in(big, max_walk=10) == 10


async def test_spaces_endpoint_reports_pruned_file_count(isolated, monkeypatch):
    """End-to-end: the /spaces handler counts a discovered space's real content, off-loop."""
    from types import SimpleNamespace

    space = isolated / "proj"
    (space / ".navig").mkdir(parents=True)
    (space / "keep.md").write_text("x", encoding="utf-8")
    nm = space / "node_modules"
    nm.mkdir()
    (nm / "junk.js").write_text("x", encoding="utf-8")

    fake = SimpleNamespace(path=str(space), canonical_name="proj", scope="project")

    def _fake_discover(*a, **k):
        return {"proj": fake}

    import navig.spaces.resolver as resolver
    monkeypatch.setattr(resolver, "discover_space_paths", _fake_discover)
    monkeypatch.setattr(resolver, "get_default_space", lambda: "default")

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        r = await client.get("/spaces")
        assert r.status == 200
        body = await r.json()
        proj = next(s for s in body["data"]["spaces"] if s["name"] == "proj")
        assert proj["exists"] is True
        assert proj["file_count"] == 1  # keep.md only — node_modules pruned
