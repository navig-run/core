"""Stage 8 — signer-shape cache: learns endpoint shape (no secrets), TTL, invalidation."""

from __future__ import annotations

from navig.browser import signer_cache as sc

# ── shape_from_request ────────────────────────────────────────────────────────

def test_shape_extracts_path_and_param_names_only():
    req = {
        "url": "https://www.tiktok.com/api/comment/list/?aweme_id=123&msToken=SECRET&X-Bogus=SIG",
        "method": "GET",
        "header_names": ["cookie", "user-agent", "x-bogus"],
    }
    tpl = sc.shape_from_request("tiktok.com", req, clock=lambda: 1000.0)
    assert tpl.path == "/api/comment/list/"
    assert "aweme_id" in tpl.param_names
    assert "msToken" in tpl.param_names  # NAME kept…
    # …but the secret VALUE never appears anywhere in the template
    assert "SECRET" not in str(tpl.__dict__)
    assert "SIG" not in str(tpl.__dict__)
    assert tpl.header_names == ["cookie", "user-agent", "x-bogus"]


def test_shape_none_for_empty_request():
    assert sc.shape_from_request("d", None) is None
    assert sc.shape_from_request("d", {"url": ""}) is None


# ── SignerCache ───────────────────────────────────────────────────────────────

def _tpl(domain="tiktok.com", path="/api/comment/list/", t=1000.0, ttl=100.0):
    return sc.SignerTemplate(domain=domain, path=path, learned_at=t, ttl=ttl,
                             param_names=["aweme_id"], header_names=["cookie"],
                             success_count=1)


def test_put_get_roundtrip(tmp_path):
    clock = {"t": 1000.0}
    cache = sc.SignerCache(tmp_path / "sc.json", clock=lambda: clock["t"])
    cache.put(_tpl())
    got = cache.get("tiktok.com")
    assert got is not None and got.path == "/api/comment/list/"
    assert (tmp_path / "sc.json").exists()  # persisted


def test_expired_template_is_dropped(tmp_path):
    clock = {"t": 1000.0}
    cache = sc.SignerCache(tmp_path / "sc.json", clock=lambda: clock["t"])
    cache.put(_tpl(t=1000.0, ttl=100.0))
    clock["t"] = 1101.0  # past TTL
    assert cache.get("tiktok.com") is None


def test_put_bumps_success_count_on_same_path(tmp_path):
    clock = {"t": 1000.0}
    cache = sc.SignerCache(tmp_path / "sc.json", clock=lambda: clock["t"])
    cache.put(_tpl())
    cache.put(_tpl(t=1001.0))
    assert cache.get("tiktok.com").success_count == 2


def test_put_resets_count_on_different_path(tmp_path):
    clock = {"t": 1000.0}
    cache = sc.SignerCache(tmp_path / "sc.json", clock=lambda: clock["t"])
    cache.put(_tpl(path="/api/comment/list/"))
    cache.put(_tpl(path="/api/v2/comment/list/", t=1001.0))
    assert cache.get("tiktok.com").success_count == 1


def test_invalidate_forces_reheal(tmp_path):
    clock = {"t": 1000.0}
    cache = sc.SignerCache(tmp_path / "sc.json", clock=lambda: clock["t"])
    cache.put(_tpl())
    cache.invalidate("tiktok.com")
    assert cache.get("tiktok.com") is None


def test_persistence_across_instances(tmp_path):
    clock = {"t": 1000.0}
    p = tmp_path / "sc.json"
    sc.SignerCache(p, clock=lambda: clock["t"]).put(_tpl())
    reopened = sc.SignerCache(p, clock=lambda: clock["t"])
    assert reopened.get("tiktok.com") is not None


def test_corrupt_cache_is_non_fatal(tmp_path):
    p = tmp_path / "sc.json"
    p.write_text("{ not json", encoding="utf-8")
    cache = sc.SignerCache(p, clock=lambda: 1000.0)
    assert cache.get("tiktok.com") is None  # tolerated, empty
