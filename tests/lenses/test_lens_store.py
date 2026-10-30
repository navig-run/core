"""The Lens store: save/update/enable/remove, the agent rule, @require caching,
entitlement filtering, and the failed-read-never-writes invariant."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.core.json_io import JsonReadError
from navig.lenses import LensFetchError, LensFormatError, LensStore, fetch_text, lenses_dir
from navig.lenses.compile import compile_lens, runtime_source
from navig.lenses.manifest import parse_manifest


def _src(name: str = "Banner", *, extra: str = "", body: str = "window.__hit = 1;") -> str:
    return (
        "// ==UserScript==\n"
        f"// @name {name}\n"
        "// @match *://example.com/*\n"
        f"{extra}"
        "// ==/UserScript==\n"
        f"{body}\n"
    )


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LensStore:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    root = lenses_dir()
    # Never touch the operator's real ~/.navig: prove where this store writes first.
    assert root == tmp_path / "cfg" / "lenses"
    return LensStore()


def test_save_writes_the_source_byte_for_byte_and_enables_a_new_lens(store: LensStore) -> None:
    src = _src(extra="// @custom keep-me\n")
    rec = store.save(src)
    assert rec.enabled is True and rec.origin == "user"
    assert (store.root / rec.id / "lens.user.js").read_text(encoding="utf-8") == src
    assert [r.id for r in store.records()] == [rec.id]


def test_update_keeps_enabled_state_and_created_at(store: LensStore) -> None:
    rec = store.save(_src())
    store.set_enabled(rec.id, False)
    again = store.save(_src(body="window.__hit = 2;"))
    assert again.id == rec.id
    assert again.enabled is False
    assert again.created_at == rec.created_at


def test_an_agent_edit_of_an_approved_lens_is_staged_not_run(store: LensStore) -> None:
    rec = store.save(_src())
    assert rec.enabled is True
    by_agent = store.save(_src(body="window.__agent = 1;"), origin="agent", enabled=True)
    # The approved copy keeps running, unchanged; the agent's edit waits for a person.
    assert by_agent.enabled is True
    assert by_agent.origin == "user"
    assert "window.__hit" in by_agent.source
    assert by_agent.pending is not None and by_agent.pending["kind"] == "agent"
    assert "window.__agent" not in store.compiled("os", caps=[]).lenses[0].code


def test_syntax_errors_are_rejected_before_anything_is_written(store: LensStore) -> None:
    import shutil

    if not shutil.which("node"):
        pytest.skip("node not installed — the store saves unchecked by design")
    with pytest.raises(LensFormatError, match="line 5"):
        store.save(_src(body="const = ;"))
    assert store.records() == []
    assert not store.index_path.exists()


def test_a_failed_index_read_refuses_to_write(
    store: LensStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = store.save(_src())
    before = store.index_path.read_text(encoding="utf-8")

    def locked(*_a, **_k):
        raise PermissionError("sharing violation")

    monkeypatch.setattr("navig.core.json_io.read_text_retrying", locked)
    with pytest.raises(JsonReadError):
        store.save(_src(name="Other"))
    with pytest.raises(JsonReadError):
        store.set_enabled(rec.id, False)
    with pytest.raises(JsonReadError):
        store.remove(rec.id)
    monkeypatch.undo()
    assert store.index_path.read_text(encoding="utf-8") == before
    assert not (store.root / parse_manifest(_src(name="Other")).id).exists()


def test_require_is_fetched_once_cached_and_pruned(store: LensStore) -> None:
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return f"window.__lib = {json.dumps(url)};"

    a = "// @require https://cdn.test/a.js\n"
    rec = store.save(_src(extra=a), fetch=fetch)
    store.save(_src(extra=a, body="window.__hit = 3;"), fetch=fetch)
    assert calls == ["https://cdn.test/a.js"]  # second save reused the cache
    assert len(list((store.root / rec.id / "require").glob("*.js"))) == 1

    store.save(_src(extra="// @require https://cdn.test/b.js\n"), fetch=fetch)
    files = list((store.root / rec.id / "require").glob("*.js"))
    assert len(files) == 1 and "b.js" in files[0].read_text(encoding="utf-8")

    compiled = store.compiled("os", caps=[]).lenses[0].code
    assert "cdn.test/b.js" in compiled and "cdn.test/a.js" not in compiled


def test_compiled_skips_disabled_locked_and_matchless(store: LensStore) -> None:
    free = store.save(_src(name="Free"))
    paid = store.save(_src(name="Paid", extra="// @navig-capability item:paid\n"))
    off = store.save(_src(name="Off"))
    store.set_enabled(off.id, False)
    store.save("// ==UserScript==\n// @name Nowhere\n// ==/UserScript==\n1;\n")

    locked = store.compiled("os", caps=[])
    assert [lens.id for lens in locked.lenses] == [free.id]
    reasons = {s["id"]: s["reason"] for s in locked.skipped}
    assert "locked" in reasons[paid.id]
    assert "runs nowhere" in reasons["nowhere"]

    owned = store.compiled("os", caps=["item:paid"])
    assert {lens.id for lens in owned.lenses} == {free.id, paid.id}
    assert owned.etag != locked.etag


def test_premium_lenses_fail_closed_when_the_license_is_unreadable(
    store: LensStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    free = store.save(_src(name="Free"))
    store.save(_src(name="Paid", extra="// @navig-capability item:paid\n"))

    def broken():
        raise RuntimeError("license subsystem down")

    monkeypatch.setattr("navig.license.entitlement.live_caps_and_rank", broken)
    assert [lens.id for lens in store.compiled("chrome").lenses] == [free.id]


def test_remove_deletes_the_lens_and_its_folder(store: LensStore) -> None:
    rec = store.save(_src())
    assert store.remove(rec.id) is True
    assert store.get(rec.id) is None
    assert not (store.root / rec.id).exists()
    assert store.remove(rec.id) is False


@pytest.mark.parametrize("bad", ["..", "../x", "a/b", ""])
def test_ids_from_api_input_cannot_escape_the_store(store: LensStore, bad: str) -> None:
    with pytest.raises(ValueError):
        store._lens_dir(bad)


def test_runtime_never_evaluates_strings() -> None:
    # CSP-safe by construction: the Lens body is spliced in as code, never eval'd.
    import re

    rt = re.sub(r"/\*.*?\*/", "", runtime_source(), flags=re.DOTALL)  # prose may say "eval"
    rt = re.sub(r"(?m)^\s*//.*$", "", rt)
    for needle in ("eval(", "new Function", "Function("):
        assert needle not in rt
    code = compile_lens(parse_manifest(_src()), _src(), host="os").code
    assert code.count("function navigLensRuntime(") == 1
    assert code.count('navigLensRuntime({"id":') == 1  # exactly one call, manifest inline


def test_chrome_prefilter_is_never_narrower_than_the_lens() -> None:
    only_match = compile_lens(parse_manifest(_src()), _src(), host="chrome")
    assert only_match.host_matches == ("*://example.com/*",)
    with_include = _src(extra="// @include /foo/\n")
    broad = compile_lens(parse_manifest(with_include), with_include, host="chrome")
    assert broad.host_matches == ("<all_urls>",)


@pytest.mark.parametrize(
    ("url", "msg"),
    [
        ("http://example.com/a.js", "https"),
        ("https://127.0.0.1/a.js", "non-public"),
        ("https://[::1]/a.js", "non-public"),
        ("https://169.254.169.254/latest", "non-public"),
    ],
)
def test_fetch_refuses_plain_http_and_private_addresses(url: str, msg: str) -> None:
    with pytest.raises(LensFetchError, match=msg):
        fetch_text(url)


# ── review: agent proposals, staged changes, updates from the install link ──────────────


def test_a_new_agent_lens_is_proposed_and_turning_it_on_approves_it(store: LensStore) -> None:
    rec = store.save(_src(), origin="agent", enabled=True)
    assert rec.enabled is False and rec.review == "proposed" and rec.needs_review
    assert [r.id for r in store.reviews()] == [rec.id]
    details = store.review_details(rec.id)
    assert details["kind"] == "proposed"
    assert any(c["field"] == "matches" and c["broadens"] for c in details["changes"])
    approved = store.set_enabled(rec.id, True)
    assert approved.enabled is True and approved.review is None
    assert store.reviews() == []


def test_the_agent_revising_its_own_unapproved_proposal_replaces_it(store: LensStore) -> None:
    store.save(_src(), origin="agent")
    rec = store.save(_src(body="window.__v2 = 1;"), origin="agent")
    assert rec.pending is None and rec.review == "proposed" and "__v2" in rec.source


def test_approving_a_staged_agent_edit_applies_it_and_keeps_the_state(store: LensStore) -> None:
    rec = store.save(_src())
    store.save(_src(body="window.__agent = 1;"), origin="agent")
    details = store.review_details(rec.id)
    assert details["kind"] == "agent"
    assert "+window.__agent = 1;" in details["diff"]
    out = store.approve(rec.id)
    assert out.enabled is True and out.pending is None and out.origin == "user"
    assert "window.__agent" in out.source
    assert not store._pending_path(rec.id).exists()


def test_rejecting_a_proposal_deletes_it_and_rejecting_an_edit_keeps_the_lens(
    store: LensStore,
) -> None:
    proposed = store.save(_src("Proposed"), origin="agent")
    assert store.reject(proposed.id) is False
    assert store.get(proposed.id) is None
    rec = store.save(_src())
    store.save(_src(body="window.__agent = 1;"), origin="agent")
    assert store.reject(rec.id) is True
    kept = store.get(rec.id)
    assert kept is not None and kept.pending is None and "__agent" not in kept.source


def test_a_staged_change_must_be_the_same_lens(store: LensStore) -> None:
    rec = store.save(_src())
    with pytest.raises(LensFormatError, match="different Lens"):
        store.stage(rec.id, _src("Other"), kind="update")


def _installed(store: LensStore, version: str = "1.0.0") -> str:
    url = "https://lenses.example/banner.user.js"
    src = _src(extra=f"// @version {version}\n")
    return store.save(src, origin="url", install_url=url).id


def test_an_update_is_staged_never_applied_and_shows_what_it_widens(store: LensStore) -> None:
    lens_id = _installed(store)
    newer = _src(extra="// @version 1.1.0\n// @match <all_urls>\n// @grant GM_setValue\n")
    results = store.check_updates(fetch=lambda url: newer)
    assert [r["status"] for r in results] == ["staged"]
    rec = store.get(lens_id)
    assert rec is not None and rec.pending and rec.pending["kind"] == "update"
    assert "<all_urls>" not in rec.source  # the running copy is untouched
    changes = store.review_details(lens_id)["changes"]
    assert changes[0]["broadens"] is True  # what widens the Lens is listed first
    widened = {c["field"]: c["added"] for c in changes if c["broadens"]}
    assert widened["matches"] == ["<all_urls>"] and widened["grants"] == ["GM_setValue"]
    assert store.last_update_check() is not None
    applied = store.approve(lens_id)
    assert "<all_urls>" in applied.source and applied.install_url == rec.install_url


def test_update_check_skips_current_pending_rejected_and_older_versions(
    store: LensStore,
) -> None:
    lens_id = _installed(store, "2.0.0")
    current = store.get(lens_id).source  # type: ignore[union-attr]
    assert store.check_updates(fetch=lambda url: current)[0]["status"] == "current"
    older = _src(extra="// @version 1.9.0\n", body="window.__old = 1;")
    assert store.check_updates(fetch=lambda url: older)[0]["status"] == "older"
    newer = _src(extra="// @version 2.1.0\n", body="window.__new = 1;")
    assert store.check_updates(fetch=lambda url: newer)[0]["status"] == "staged"
    assert store.check_updates(fetch=lambda url: newer)[0]["status"] == "pending"
    assert store.reject(lens_id) is True
    assert store.check_updates(fetch=lambda url: newer)[0]["status"] == "rejected"


def test_an_update_check_never_overwrites_an_agent_edit_awaiting_review(
    store: LensStore,
) -> None:
    """Review F2730: the daily monitor used to stage the fetched update over the agent's
    proposal, discarding it before a person decided."""
    lens_id = _installed(store, "1.0.0")
    agent_src = _src(extra="// @version 1.0.0\n", body="window.__agent = 1;")
    store.stage(lens_id, agent_src, kind="agent")
    pending_file = store._pending_path(lens_id)
    newer = _src(extra="// @version 1.1.0\n", body="window.__new = 1;")

    [row] = store.check_updates(fetch=lambda url: newer)

    assert row["status"] == "pending" and row["pending_kind"] == "agent"
    rec = store.get(lens_id)
    assert rec is not None and rec.pending and rec.pending["kind"] == "agent"
    assert pending_file.read_text(encoding="utf-8") == agent_src


def test_an_unreachable_install_link_is_an_error_row_not_a_crash(store: LensStore) -> None:
    _installed(store)

    def down(url: str) -> str:
        raise LensFetchError("could not fetch: offline")

    [row] = store.check_updates(fetch=down)
    assert row["status"] == "error" and "offline" in row["error"]


def _write_legacy_index(store: LensStore, entry: dict) -> None:
    """An index.json as a NAVIG from before the review flow wrote it — no `review` key."""
    import json

    store.save(_src(), origin="user")  # creates the folder + source
    data = json.loads(store.index_path.read_text(encoding="utf-8"))
    data["lenses"]["banner"] = {**data["lenses"]["banner"], **entry}
    data["lenses"]["banner"].pop("review", None)
    store.index_path.write_text(json.dumps(data), encoding="utf-8")


def test_an_agent_lens_saved_before_the_review_flow_is_still_a_proposal(store: LensStore) -> None:
    _write_legacy_index(store, {"origin": "agent", "enabled": False})
    rec = store.get("banner")
    assert rec is not None and rec.review == "proposed" and rec.needs_review
    assert [r.id for r in store.reviews()] == ["banner"]
    # An agent edit of a legacy proposal replaces it (still unapproved), never stages.
    edited = store.save(_src(body="window.__v2 = 1;"), origin="agent")
    assert edited.pending is None and "__v2" in edited.source and edited.review == "proposed"


def test_an_approved_agent_lens_switched_off_is_not_proposed_again(store: LensStore) -> None:
    rec = store.save(_src(), origin="agent")
    store.set_enabled(rec.id, True)  # the approval
    off = store.set_enabled(rec.id, False)
    assert off.review is None and not off.needs_review
    # ...and approving a staged agent edit while it is off keeps it approved.
    store.save(_src(body="window.__agent = 1;"), origin="agent")
    applied = store.approve(rec.id)
    assert applied.enabled is False and applied.review is None and not applied.needs_review


def test_only_the_chrome_host_isolates_a_lens_granted_navig_capture() -> None:
    granted = _src(extra="// @grant navig.capture\n")
    m = parse_manifest(granted)
    assert "navig.capture" not in m.unsupported_grants and not m.warnings
    assert compile_lens(m, granted, host="chrome").to_dict()["world"] == "USER_SCRIPT"
    assert compile_lens(m, granted, host="os").world == "MAIN"         # no bridge there yet
    assert compile_lens(m, granted, host="playwright").world == "MAIN"
    plain = _src()
    assert compile_lens(parse_manifest(plain), plain, host="chrome").world == "MAIN"


def test_concurrent_saves_never_drop_a_lens_from_the_index(store: LensStore) -> None:
    # A save with a slow @require used to read index.json, fetch for a while, then write
    # back the index it read, deleting every Lens saved by anyone else in between.
    import threading

    fetching = threading.Event()
    release = threading.Event()

    def slow_fetch(url: str) -> str:
        fetching.set()
        assert release.wait(5), "the second save never finished"
        return "window.__lib = 1;"

    errors: list[BaseException] = []

    def save_slow() -> None:
        try:
            store.save(_src("Slow", extra="// @require https://cdn.test/lib.js\n"), fetch=slow_fetch)
        except BaseException as exc:  # noqa: BLE001 - surfaced in the assert below
            errors.append(exc)

    t = threading.Thread(target=save_slow)
    t.start()
    assert fetching.wait(5), "the slow save never reached its fetch"
    fast = store.save(_src("Fast"))  # runs while the slow save is mid-fetch
    release.set()
    t.join(10)
    assert not t.is_alive() and not errors, errors
    ids = {r.id for r in store.records()}
    assert ids == {fast.id, "slow"}, f"index lost a Lens: {sorted(ids)}"


# ── Stored values: isolated on Chrome so the page cannot read them (R2) ──────────────


def _granted(*lines: str) -> str:
    return _src(extra="".join(f"// {line}\n" for line in lines))


@pytest.mark.parametrize("grant", ["GM_getValue", "GM_setValue", "GM.listValues", "GM_addValueChangeListener"])
def test_a_storage_grant_isolates_the_lens_on_chrome_with_a_storage_bridge(grant: str) -> None:
    src = _granted(f"@grant {grant}")
    compiled = compile_lens(parse_manifest(src), src, host="chrome")
    assert compiled.world == "USER_SCRIPT"
    assert compiled.to_dict()["bridge"] == ["storage"]


def test_capture_and_storage_share_one_world_and_one_bridge() -> None:
    src = _granted("@grant navig.capture", "@grant GM_setValue")
    compiled = compile_lens(parse_manifest(src), src, host="chrome")
    assert (compiled.world, compiled.bridge) == ("USER_SCRIPT", ("capture", "storage"))


def test_hosts_without_per_lens_worlds_and_ungranted_lenses_get_no_bridge() -> None:
    src = _granted("@grant GM_setValue")
    m = parse_manifest(src)
    for host in ("os", "playwright", "preview"):
        compiled = compile_lens(m, src, host=host)
        assert (compiled.world, compiled.bridge) == ("MAIN", ()), host
    plain = _granted("@grant GM_addStyle")
    assert compile_lens(parse_manifest(plain), plain, host="chrome").to_dict()["bridge"] == []


def test_sandbox_raw_is_the_authors_explicit_choice_of_the_page_world() -> None:
    # Tampermonkey's own directive: a Lens that must touch page JavaScript (unsafeWindow)
    # opts out of isolation — and with it out of the bridge, so capture is absent too.
    src = _granted("@grant GM_setValue", "@grant navig.capture", "@sandbox raw")
    compiled = compile_lens(parse_manifest(src), src, host="chrome")
    assert (compiled.world, compiled.bridge) == ("MAIN", ())
    for mode in ("JavaScript", "DOM"):
        isolated = _granted("@grant GM_setValue", f"@sandbox {mode}")
        assert compile_lens(parse_manifest(isolated), isolated, host="chrome").world == "USER_SCRIPT"
