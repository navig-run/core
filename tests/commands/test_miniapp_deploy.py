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
    deck = tmp_path / "deck-src"
    deck.mkdir()
    (deck / "package.json").write_text("{}", encoding="utf-8")
    assert m._find_deck_dir(str(deck)) == deck.resolve()


def test_find_deck_dir_via_env(tmp_path, monkeypatch):
    deck = tmp_path / "deck-src"
    deck.mkdir()
    (deck / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NAVIG_DECK_DIR", str(deck))
    assert m._find_deck_dir("") == deck.resolve()


def test_find_deck_dir_explicit_missing_package_json(tmp_path):
    # An explicit dir without package.json is not accepted (falls through).
    empty = tmp_path / "deck-src"
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


# ── Telegram Mini App cache-bust (the button URL must change when the deck does) ─


def _fake_cm(key: str = ""):
    class _CM:
        global_config = {"deck": {"api_key": key}}

    return lambda: _CM()


def test_connect_url_appends_version_after_key(monkeypatch):
    monkeypatch.setattr("navig.config.get_config_manager", _fake_cm("SECRET"))
    u = m._connect_url("https://deck.example.dev/", version="abc123")
    assert u == "https://deck.example.dev/connect?key=SECRET&v=abc123"


def test_connect_url_appends_version_to_bare_url_when_no_key(monkeypatch):
    monkeypatch.setattr("navig.config.get_config_manager", _fake_cm(""))
    u = m._connect_url("https://deck.example.dev", version="abc123")
    assert u == "https://deck.example.dev?v=abc123"


def test_connect_url_without_version_is_unchanged(monkeypatch):
    monkeypatch.setattr("navig.config.get_config_manager", _fake_cm("K"))
    u = m._connect_url("https://deck.example.dev")
    assert u == "https://deck.example.dev/connect?key=K"
    assert "v=" not in u


def test_deck_bundle_signature_is_deterministic_and_content_sensitive(tmp_path):
    chunks = tmp_path / "_next" / "static" / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "a-111.js").write_text("x", encoding="utf-8")
    (chunks / "b-222.js").write_text("y", encoding="utf-8")

    sig = m._deck_bundle_signature(tmp_path)
    assert sig and len(sig) == 12
    assert m._deck_bundle_signature(tmp_path) == sig  # stable for same bundle

    # A new content-hashed chunk name (a fresh build) must change the signature.
    (chunks / "c-333.js").write_text("z", encoding="utf-8")
    assert m._deck_bundle_signature(tmp_path) != sig


def test_deck_bundle_signature_empty_when_no_bundle(tmp_path):
    assert m._deck_bundle_signature(tmp_path) == ""


# ── A skipped menu-button update is not a footnote ───────────────────────────
# Telegram caches the Mini App by URL, so a deploy whose button update did not land
# is invisible to every client: the assets are live and nobody can see them. That
# outcome used to be reported with ch.dim() at exit 0, which is how this install sat
# on a months-old deck. These pin the severity AND the stated consequence.


class _Recorder:
    """Stands in for console_helper, recording what was said at which severity."""

    def __init__(self):
        self.warning_lines: list[str] = []
        self.success_lines: list[str] = []
        self.other_lines: list[str] = []

    def warning(self, msg=""):
        self.warning_lines.append(str(msg))

    def success(self, msg=""):
        self.success_lines.append(str(msg))

    def error(self, msg=""):
        self.other_lines.append(str(msg))

    def info(self, msg=""):
        self.other_lines.append(str(msg))

    def dim(self, msg=""):
        self.other_lines.append(str(msg))

    @property
    def warnings(self) -> str:
        return "\n".join(self.warning_lines)


def _run_deploy_cli(monkeypatch, *, register: bool, result_extra: dict) -> _Recorder:
    from typer.testing import CliRunner

    rec = _Recorder()
    monkeypatch.setattr(m, "_ch", lambda: rec)

    class _Cfg:
        def get(self, key, default=None):
            return "https://edge.example.workers.dev" if key == "cloud.lighthouse_url" else default

    monkeypatch.setattr("navig.core.Config", lambda *a, **k: _Cfg())

    def _fake_deploy(**kwargs):
        assert kwargs["register"] is register
        return {"ok": True, "status": "deployed", "url": "https://deck.example.dev", **result_extra}

    monkeypatch.setattr(m, "run_miniapp_deploy", _fake_deploy)

    args = ["deploy"] if register else ["deploy", "--no-register"]
    res = CliRunner().invoke(m.app, args)
    assert res.exit_code == 0, res.output
    return rec


def test_deploy_warns_loudly_when_the_button_update_failed(monkeypatch):
    rec = _run_deploy_cli(
        monkeypatch, register=True, result_extra={"registered": False, "register_error": "Unauthorized"}
    )
    assert "Unauthorized" in rec.warnings
    # The consequence, not just the symptom: clients keep the bundle they cached.
    assert "caches the Mini App by URL" in rec.warnings
    assert "navig miniapp register" in "\n".join(rec.other_lines + rec.warning_lines)


def test_deploy_warns_when_no_bot_token_skipped_the_button(monkeypatch):
    rec = _run_deploy_cli(monkeypatch, register=True, result_extra={"registered": False})
    assert "NOT updated" in rec.warnings
    assert "caches the Mini App by URL" in rec.warnings


def test_deploy_warns_on_explicit_no_register(monkeypatch):
    """--no-register used to say nothing at all — a silent path to a stale client."""
    rec = _run_deploy_cli(monkeypatch, register=False, result_extra={"registered": False})
    assert "--no-register" in rec.warnings
    assert "caches the Mini App by URL" in rec.warnings


def test_deploy_does_not_cry_wolf_when_the_button_was_set(monkeypatch):
    rec = _run_deploy_cli(monkeypatch, register=True, result_extra={"registered": True})
    assert any("Mini App button set" in line for line in rec.success_lines)
    assert "caches the Mini App by URL" not in rec.warnings


# ── `--skip-build` must not ship stale deploy-config assets ──────────────────
# `_headers` lives in the deck's `public/` and Next copies it into `out/` at build
# time. `--skip-build` reuses a previous `out/`, so editing `_headers` alone and
# redeploying reported success and changed nothing — the deploy-config file is
# exactly the kind that gets edited WITHOUT a code change, so this path is the
# common one, not the rare one. It bit during the work that introduced the header
# pipeline: the edit deployed cleanly and the live headers were unchanged.


def _deck_with(public: dict[str, str], out: dict[str, str], tmp_path):
    deck = tmp_path / "deck"
    (deck / "public").mkdir(parents=True)
    (deck / "out").mkdir(parents=True)
    for name, body in public.items():
        (deck / "public" / name).write_text(body, encoding="utf-8")
    for name, body in out.items():
        (deck / "out" / name).write_text(body, encoding="utf-8")
    return deck


def test_a_stale_headers_file_in_out_is_refreshed(tmp_path):
    deck = _deck_with({"_headers": "/*\n  X-New: yes\n"}, {"_headers": "/*\n  X-Old: yes\n"}, tmp_path)
    changed = m._refresh_passthrough_assets(deck, deck / "out")
    assert changed == ["_headers"]
    assert "X-New" in (deck / "out" / "_headers").read_text(encoding="utf-8")


def test_an_unchanged_file_is_not_touched(tmp_path):
    same = "/*\n  X-Same: yes\n"
    deck = _deck_with({"_headers": same}, {"_headers": same}, tmp_path)
    assert m._refresh_passthrough_assets(deck, deck / "out") == []


def test_redirects_is_covered_too_and_a_missing_source_is_not_an_error(tmp_path):
    deck = _deck_with({"_redirects": "/old /new 301\n"}, {}, tmp_path)
    assert m._refresh_passthrough_assets(deck, deck / "out") == ["_redirects"]
    # No `_headers` in public/ — that is simply nothing to copy, not a failure.
    assert not (deck / "out" / "_headers").exists()


def test_only_the_named_config_files_are_synced(tmp_path):
    """Not a general public/ sync: a stale out/ may legitimately differ elsewhere,
    and papering over that would trade one invisible staleness for another."""
    deck = _deck_with(
        {"_headers": "/*\n  A: b\n", "favicon.ico": "NEW", "robots.txt": "NEW"},
        {"favicon.ico": "OLD", "robots.txt": "OLD"},
        tmp_path,
    )
    assert m._refresh_passthrough_assets(deck, deck / "out") == ["_headers"]
    assert (deck / "out" / "favicon.ico").read_text(encoding="utf-8") == "OLD"
    assert (deck / "out" / "robots.txt").read_text(encoding="utf-8") == "OLD"
