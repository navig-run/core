"""Tests for `navig miniapp deploy` pure helpers (no Node/wrangler invoked)."""

from __future__ import annotations

from navig.commands import miniapp as m


def test_parse_pages_url_prefers_stable_alias():
    out = "Uploaded. Take a peek at https://a1b2c3.navig-deck.pages.dev\nAlias: https://navig-deck.pages.dev"
    assert m._parse_pages_url(out, "navig-deck") == "https://navig-deck.pages.dev"


def test_parse_pages_url_falls_back_to_deployment_url():
    out = "Deployment complete: https://a1b2c3.navig-deck.pages.dev"
    assert m._parse_pages_url(out, "navig-deck") == "https://a1b2c3.navig-deck.pages.dev"


def test_parse_pages_url_constructs_when_absent():
    assert m._parse_pages_url("no url in here", "navig-deck") == "https://navig-deck.pages.dev"


def test_find_deck_dir_explicit(tmp_path):
    deck = tmp_path / "navig-deck"
    deck.mkdir()
    (deck / "package.json").write_text("{}", encoding="utf-8")
    assert m._find_deck_dir(str(deck)) == deck.resolve()


def test_find_deck_dir_via_env(tmp_path, monkeypatch):
    deck = tmp_path / "navig-deck"
    deck.mkdir()
    (deck / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NAVIG_DECK_DIR", str(deck))
    assert m._find_deck_dir("") == deck.resolve()


def test_find_deck_dir_explicit_missing_package_json(tmp_path):
    # An explicit dir without package.json is not accepted (falls through).
    empty = tmp_path / "navig-deck"
    empty.mkdir()
    # No package.json here; explicit candidate is rejected. (cwd-walk may still
    # find the real repo deck, so we only assert it's not the empty dir.)
    assert m._find_deck_dir(str(empty)) != empty.resolve()


# ── Prebuilt bundle resolution (end-user path: no source, no Node) ───────────


def test_find_prebuilt_deck_out_explicit_bundle(tmp_path):
    bundle = tmp_path / "static"
    bundle.mkdir()
    (bundle / "index.html").write_text("<html></html>", encoding="utf-8")
    assert m._find_prebuilt_deck_out(str(bundle)) == bundle.resolve()


def test_find_prebuilt_deck_out_explicit_out_subdir(tmp_path):
    base = tmp_path / "deck"
    (base / "out").mkdir(parents=True)
    (base / "out" / "index.html").write_text("x", encoding="utf-8")
    assert m._find_prebuilt_deck_out(str(base)) == (base / "out").resolve()


def test_find_prebuilt_deck_out_requires_index_html(tmp_path):
    base = tmp_path / "static"
    base.mkdir()
    # No index.html → not a valid bundle. (cwd-walk may find the real repo bundle,
    # so we only assert it does not return this incomplete dir.)
    assert m._find_prebuilt_deck_out(str(base)) != base.resolve()


# ── Deploy-time lighthouse URL bake (sentinel replacement) ───────────────────


def test_bake_lighthouse_replaces_sentinel(tmp_path):
    src = tmp_path / "static"
    src.mkdir()
    (src / "index.html").write_text('w="__NAVIG_LIGHTHOUSE_URL__"', encoding="utf-8")
    (src / "app.js").write_text('const u="__NAVIG_LIGHTHOUSE_URL__";', encoding="utf-8")
    out = m._bake_lighthouse_into_prebuilt(src, "https://edge.example.dev")
    assert out != src  # a writable temp copy
    js = (out / "app.js").read_text(encoding="utf-8")
    assert "__NAVIG_LIGHTHOUSE_URL__" not in js
    assert "https://edge.example.dev" in js


def test_bake_lighthouse_noop_without_sentinel(tmp_path):
    src = tmp_path / "static"
    src.mkdir()
    (src / "index.html").write_text("no sentinel here", encoding="utf-8")
    # No sentinel → original dir returned unchanged (deck uses runtime Settings).
    assert m._bake_lighthouse_into_prebuilt(src, "https://edge.example.dev") == src
