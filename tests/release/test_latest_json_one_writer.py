"""latest.json has ONE writer, ONE schema, and never links to an asset nobody verified.

`core/latest.json` is the published release manifest (served at navig.run/latest.json via
its `web/www/public` copy). It had three writers with three shapes: the hand-release tool
wrote six keys with a `download_url` fabricated from a template; the tag workflow inlined
its own three-key payload with a DATETIME where the tool writes a date; and the site copy
re-fabricated the URL from the same template whatever core said. Publish by hand (org
Actions billing-blocked, so the tag workflow never fires) and the template URL points at
a GitHub Release nobody cut — measured on 3.25.0: 404, from a public manifest, for two
weeks, until the owner created the release by hand.

Now: the workflow runs the tool; the tool HEADs the asset and writes null when it is not
there; the site copies what core says; `released_at` is a date everywhere and survives a
re-sync of the same version.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "core" / "tools" / "_version_sync.py"
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
SITE_WRITER = REPO / "web" / "www" / "scripts" / "sync-site-version.mjs"
CORE_LATEST = REPO / "core" / "latest.json"
WWW_LATEST = REPO / "web" / "www" / "public" / "latest.json"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("_version_sync_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated_latest(tool, tmp_path: Path, monkeypatch):
    """Point the tool at a private latest.json so the tests never touch the real manifest."""
    path = tmp_path / "latest.json"
    monkeypatch.setattr(tool, "LATEST_JSON_PATH", path)
    return path


# ── the download_url is verified, never fabricated ───────────────────────────


def test_a_missing_asset_is_written_as_null_not_as_a_template_url(tool, isolated_latest) -> None:
    m = tool.build_manifest("9.9.9", verify=lambda url: False)
    assert m["download_url"] is None
    assert m["pypi"] == "https://pypi.org/project/navig/9.9.9/"


def test_an_existing_asset_is_linked(tool, isolated_latest) -> None:
    m = tool.build_manifest("9.9.9", verify=lambda url: True)
    assert m["download_url"] == tool.DOWNLOAD_URL_TEMPLATE.format(version="9.9.9")


def test_the_verifier_treats_every_failure_as_absent(tool, monkeypatch) -> None:
    """404, timeout, DNS, offline — a URL that could not be verified is not emitted."""
    import urllib.request

    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.delenv("NAVIG_VERSION_SYNC_OFFLINE", raising=False)
    assert tool._asset_exists("https://example.invalid/x.tar.gz") is False


def test_the_offline_switch_never_touches_the_network(tool, monkeypatch) -> None:
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call made"))
    monkeypatch.setenv("NAVIG_VERSION_SYNC_OFFLINE", "1")
    assert tool._asset_exists("https://example.invalid/x.tar.gz") is False


# ── released_at: a date, preserved across a re-sync ──────────────────────────


def test_a_resync_of_the_same_version_keeps_its_release_date(tool, isolated_latest) -> None:
    isolated_latest.write_text(json.dumps({"version": "9.9.9", "released_at": "2026-01-02"}), encoding="utf-8")
    m = tool.build_manifest("9.9.9", verify=lambda url: False)
    assert m["released_at"] == "2026-01-02"


def test_a_new_version_gets_today(tool, isolated_latest) -> None:
    isolated_latest.write_text(json.dumps({"version": "9.9.8", "released_at": "2026-01-02"}), encoding="utf-8")
    m = tool.build_manifest("9.9.9", verify=lambda url: False)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", m["released_at"]) and m["released_at"] != "2026-01-02"


def test_an_explicit_release_date_wins_and_is_truncated_to_a_date(tool, isolated_latest) -> None:
    m = tool.build_manifest("9.9.9", released_at="2026-03-04T05:06:07Z", verify=lambda url: False)
    assert m["released_at"] == "2026-03-04"


def test_a_recorded_datetime_from_the_old_workflow_is_normalised(tool, isolated_latest) -> None:
    """The old inline writer stored 2026-09-05T10:11:12Z; a re-sync must not keep that shape."""
    isolated_latest.write_text(json.dumps({"version": "9.9.9", "released_at": "2026-09-05T10:11:12Z"}), encoding="utf-8")
    assert tool.build_manifest("9.9.9", verify=lambda url: False)["released_at"] == "2026-09-05"


# ── one schema ───────────────────────────────────────────────────────────────


def test_the_manifest_has_exactly_the_schema_keys_in_order(tool, isolated_latest) -> None:
    m = tool.build_manifest("9.9.9", verify=lambda url: False)
    assert tuple(m) == tool.MANIFEST_KEYS


def test_the_committed_manifests_have_the_schema_and_agree() -> None:
    core, www = (json.loads(p.read_text(encoding="utf-8")) for p in (CORE_LATEST, WWW_LATEST))
    keys = ("version", "channel", "pypi", "download_url", "changelog_url", "released_at")
    assert tuple(core) == keys, tuple(core)
    assert tuple(www) == keys, tuple(www)
    assert core == www, "the site copy must be what core says — it is a copy, not a second opinion"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", core["released_at"]), core["released_at"]


# ── one writer ───────────────────────────────────────────────────────────────


def test_the_release_workflow_runs_the_tool_instead_of_its_own_payload() -> None:
    src = WORKFLOW.read_text(encoding="utf-8")
    assert "python core/tools/_version_sync.py" in src, "the workflow must call the one writer"
    assert "payload = {" not in src, "the workflow must not inline a second latest.json shape"
    assert "%Y-%m-%dT%H:%M:%SZ" not in src, "released_at is a date everywhere, not a datetime"


def test_the_site_writer_never_fabricates_a_download_url() -> None:
    src = SITE_WRITER.read_text(encoding="utf-8")
    assert "releases/download/" not in src, (
        "the site copy re-fabricated the GitHub asset URL from a template; it must copy what "
        "core/latest.json recorded (which was verified) and otherwise write null"
    )


def test_the_tool_is_import_light_for_the_bare_workflow_checkout() -> None:
    """release.yml runs the tool in a job with no `pip install`: a module-level `navig`
    import would ImportError there. The www step needs it and imports lazily."""
    src = TOOL.read_text(encoding="utf-8")
    top = src.split("def ", 1)[0]
    assert "from navig" not in top and "import navig" not in top, "keep navig imports inside functions"


def test_every_print_is_ascii_so_a_cp1251_console_cannot_kill_a_release(tool) -> None:
    """Measured: the tool died on this machine's console with UnicodeEncodeError on a
    `\\u2705`. A release tool must not depend on the operator's code page."""
    for i, line in enumerate(TOOL.read_text(encoding="utf-8").splitlines(), 1):
        if "print(" in line:
            assert all(ord(c) < 128 for c in line), f"line {i}: non-ASCII in a print(): {line.strip()[:60]}"


# ── the release BODY has one writer too (2026-09-19) ─────────────────────────


def test_the_release_workflow_takes_its_body_from_the_one_release_notes_writer() -> None:
    """release.sh pasted the raw changelog block; the workflow inlined an install snippet
    plus auto-generated notes — two shapes of one public page, exactly the class the
    latest.json step above closed. Both now hand `changelog_assemble.py --release-notes`
    to the release step, which also fails BEFORE the release exists when the changelog
    has no block for the tag."""
    src = WORKFLOW.read_text(encoding="utf-8")
    assert "changelog_assemble.py --release-notes" in src
    assert "body_path: release-notes.md" in src
    assert "pip install navig==${{" not in src, "the install snippet lives in the one writer now"
    assert src.index("--release-notes") < src.index("softprops/action-gh-release"), "composed before the release step"


def test_the_release_workflow_refuses_a_tag_the_tree_does_not_build() -> None:
    """v3.26.0 over a pyproject that still says 3.25.0 builds navig-3.25.0, which PyPI
    refuses (file exists) — after the tag and release already went out. The check must
    run first, in validate, before any artifact is built."""
    src = WORKFLOW.read_text(encoding="utf-8")
    guard = src.index("The tag is the version the tree builds")
    assert guard < src.index("pip install -e"), "before the install, let alone the build"
    step = src[guard: src.index("- name:", guard + 10)]
    assert "GITHUB_REF_NAME#v" in step and "core/pyproject.toml" in step and "exit 1" in step
