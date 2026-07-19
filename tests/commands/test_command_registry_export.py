from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_build_public_manifest_has_required_keys():
    from navig.registry.manifest import build_public_manifest

    manifest = build_public_manifest(validate=False)

    assert manifest["schema_version"] == "1.0.0"
    assert isinstance(manifest["commands"], list)
    assert manifest["total"] == len(manifest["commands"])
    assert manifest["total"] > 0

    sample = manifest["commands"][0]
    required = {
        "path",
        "summary",
        "module",
        "handler",
        "status",
        "since",
        "aliases",
        "tags",
        "examples",
    }
    assert required.issubset(sample.keys())


def test_manifest_contains_annotated_host_and_db_commands():
    from navig.registry.manifest import build_public_manifest

    manifest = build_public_manifest(validate=False)
    by_path = {row["path"]: row for row in manifest["commands"]}

    assert "navig host list" in by_path
    assert "navig db query" in by_path
    assert "navig db list" in by_path

    assert "hosts" in by_path["navig host list"]["tags"]
    assert "database" in by_path["navig db query"]["tags"]


def test_markdown_render_contains_reference_sections():
    from navig.registry.manifest import build_public_manifest, render_markdown

    manifest = build_public_manifest(validate=False)
    markdown = render_markdown(manifest)

    assert "# NAVIG Command Reference" in markdown
    assert "## `navig host list`" in markdown or "## `navig db query`" in markdown


def test_export_script_writes_artifacts(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[2]
    export_script = repo_root / "tools" / "export_registry.py"
    spec = importlib.util.spec_from_file_location("export_registry", export_script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    main = module.main

    output_dir = tmp_path / "generated"
    code = main(
        [
            "--validate",
            "--format",
            "both",
            "--deprecations-report",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert code == 0
    commands_json = output_dir / "commands.json"
    commands_md = output_dir / "commands.md"
    deprecations_json = output_dir / "deprecations.json"
    completions_txt = output_dir / "completions" / "commands.txt"

    assert commands_json.exists()
    assert commands_md.exists()
    assert deprecations_json.exists()
    assert completions_txt.exists()

    payload = json.loads(commands_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0.0"
    assert payload["total"] == len(payload["commands"])
    assert payload["total"] > 0


# ---------------------------------------------------------------------------
# Anti-rot: the checked-in manifest must cover every registered command.
# ---------------------------------------------------------------------------


def _shipped_manifest() -> dict:
    repo_root = Path(__file__).resolve().parents[2]
    shipped = repo_root / "generated" / "commands.json"
    return json.loads(shipped.read_text(encoding="utf-8"))


def _is_core_command(cmd: dict) -> bool:
    """True when a manifest command comes from the core ``navig`` package.

    Core modules are ``navig`` / ``navig.commands.*``; every first-party plugin
    lives in its own top-level package (``navig_download``, ``navig_antivirus``,
    …). The distinction matters for the coverage guard below: which plugins are
    importable depends on the interpreter and install (e.g. ``navig_antivirus``
    resolves under 3.14 but not 3.13 on this machine), so plugin commands can't
    be pinned by a checked-in manifest — only core commands can.
    """
    top = (cmd.get("module") or "navig").split(".", 1)[0]
    return top == "navig"


def test_shipped_manifest_covers_every_registered_command():
    """The checked-in ``generated/commands.json`` must list every CORE command the
    live Typer app registers.

    It silently rotted before (PR #322: ``ledger``, ``audit`` and ``undo`` were
    registered commands yet absent from the shipped manifest, so ``navig
    --schema`` and ``navig help --json`` never listed them). CI regenerates the
    manifest and diffs it, but that gate is billing-blocked and never runs — so
    this local, deterministic test is the real guard.

    Scope is **core commands only** (``module`` in the ``navig`` package).
    Plugin commands (``navig_*`` packages) are excluded: which plugins import —
    and even which subcommands a plugin exposes — varies by interpreter and
    install (``navig_antivirus`` resolves under 3.14 but not 3.13 here), so a
    checked-in manifest can never be their reproducible source of truth. Core
    rot — the actual PR #322 failure — is what this guards. Compares PATHS only
    (``generated_at`` is non-deterministic by design).
    """
    from navig.registry.manifest import build_public_manifest

    shipped_paths = {c["path"] for c in _shipped_manifest()["commands"]}
    live_core_paths = {
        c["path"] for c in build_public_manifest(validate=False)["commands"] if _is_core_command(c)
    }

    missing = sorted(live_core_paths - shipped_paths)
    assert not missing, (
        "generated/commands.json is stale — these registered core commands are "
        f"missing from it: {missing}. Regenerate with: "
        "python tools/export_registry.py --format both --deprecations-report"
    )


def test_shipped_manifest_pins_previously_dropped_commands():
    """Regression pin for PR #322: the exact commands that had rotted out of the
    shipped manifest must stay present, independent of the plugin environment."""
    shipped_paths = {c["path"] for c in _shipped_manifest()["commands"]}

    for path in ("navig undo", "navig ledger show", "navig ledger verify", "navig audit tail"):
        assert path in shipped_paths, (
            f"'{path}' missing from generated/commands.json — regenerate the manifest "
            "(python tools/export_registry.py --format both --deprecations-report)"
        )


# ---------------------------------------------------------------------------
# Anti-rot: the FOUR generated artifacts must stay in step with each other.
# ---------------------------------------------------------------------------

# Every command section in commands.md is a `## `navig …`` header, one per command.
_MD_COMMAND_HEADER = re.compile(r"^## `(navig[^`]*)`", re.MULTILINE)


def _shipped_text(rel: str) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / "generated" / rel).read_text(encoding="utf-8")


def _shipped_completion_paths() -> set[str]:
    return {
        ln.strip() for ln in _shipped_text("completions/commands.txt").splitlines() if ln.strip()
    }


def _shipped_markdown_paths() -> set[str]:
    return set(_MD_COMMAND_HEADER.findall(_shipped_text("commands.md")))


def test_shipped_artifacts_are_mutually_consistent():
    """``commands.md`` and ``completions/commands.txt`` must list the SAME command
    set as ``commands.json``.

    All four artifacts are written from ONE manifest in a single export run, so
    their command sets are identical when regenerated together. They silently
    drifted apart before (PR #343: ``commands.md`` and ``completions/commands.txt``
    were 6-11 days staler than ``commands.json`` — the markdown was +9050 lines out
    of date — because a partial regen refreshed one artifact but not the others,
    and only ``commands.json`` had a CI freshness gate). Comparing the committed
    snapshots against EACH OTHER is deterministic and interpreter-independent:
    unlike a compare against a freshly-built manifest (whose plugin surface differs
    across 3.13/3.14), these are all the same committed export.
    """
    json_paths = {c["path"] for c in _shipped_manifest()["commands"]}
    regen_hint = (
        "Regenerate & commit all four artifacts: "
        "python tools/export_registry.py --validate --format both --deprecations-report"
    )

    completion_paths = _shipped_completion_paths()
    assert completion_paths == json_paths, (
        "generated/completions/commands.txt is out of sync with commands.json — "
        f"only in completions.txt: {sorted(completion_paths - json_paths)}; "
        f"missing from completions.txt: {sorted(json_paths - completion_paths)}. " + regen_hint
    )

    markdown_paths = _shipped_markdown_paths()
    assert markdown_paths == json_paths, (
        "generated/commands.md is out of sync with commands.json — "
        f"only in commands.md: {sorted(markdown_paths - json_paths)}; "
        f"missing from commands.md: {sorted(json_paths - markdown_paths)}. " + regen_hint
    )


def test_markdown_renderer_covers_every_manifest_command():
    """``render_markdown`` must emit exactly one ``## `navig …`` section per command
    in the manifest it is given — the markdown was the +9050-line drift in PR #343,
    so a renderer regression that silently dropped sections is the risk to guard.

    Built from ONE freshly-built manifest and checked against THAT manifest's own
    commands, so it is interpreter-independent (no cross-environment count compare).
    """
    from navig.registry.manifest import build_public_manifest, render_markdown

    manifest = build_public_manifest(validate=False)
    manifest_paths = {c["path"] for c in manifest["commands"]}

    rendered_paths = set(_MD_COMMAND_HEADER.findall(render_markdown(manifest)))
    assert rendered_paths == manifest_paths, (
        "render_markdown dropped or added command sections vs the manifest: "
        f"only in markdown: {sorted(rendered_paths - manifest_paths)}; "
        f"missing from markdown: {sorted(manifest_paths - rendered_paths)}"
    )
