from __future__ import annotations

import importlib.metadata
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


def _load_export_module():
    repo_root = Path(__file__).resolve().parents[2]
    export_script = repo_root / "tools" / "export_registry.py"
    spec = importlib.util.spec_from_file_location("export_registry_check", export_script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def test_check_matches_a_fresh_export_and_writes_nothing(tmp_path: Path, capsys):
    """``--check`` is the read-only verb: green against its own output, and the tree it
    compared against is byte-identical afterwards — including the timestamp a regen
    would have bumped."""
    module = _load_export_module()
    out = tmp_path / "generated"
    flags = ["--format", "both", "--deprecations-report", "--output-dir", str(out)]
    assert module.main(flags) == 0
    before = _snapshot(out)
    assert set(before) == {
        "commands.json", "commands.md", "completions/commands.txt", "deprecations.json",
    }
    capsys.readouterr()

    assert module.main(["--check", *flags]) == 0
    captured = capsys.readouterr()
    assert "nothing written" in captured.out
    assert _snapshot(out) == before, "--check must not touch the artifacts it compares"


def test_check_names_every_stale_artifact_and_fails(tmp_path: Path, capsys):
    """Each drifted or missing artifact is named on stderr with exit 1 — a green tick over a
    stale manifest is what the freshness gate exists to prevent, and the CLI verb must not
    be softer than the gate."""
    module = _load_export_module()
    out = tmp_path / "generated"
    flags = ["--format", "both", "--deprecations-report", "--output-dir", str(out)]
    assert module.main(flags) == 0
    capsys.readouterr()

    md = out / "commands.md"
    md.write_text(md.read_text(encoding="utf-8") + "\n<!-- drift -->\n", encoding="utf-8")
    (out / "deprecations.json").unlink()
    before = _snapshot(out)

    assert module.main(["--check", *flags]) == 1
    captured = capsys.readouterr()
    assert "commands.md: differs" in captured.err
    assert "deprecations.json: missing" in captured.err
    assert "commands.json:" not in captured.err and "commands.txt:" not in captured.err, (
        "an artifact that still matches must not be reported as stale"
    )
    assert "Regenerate & commit" in captured.err
    assert _snapshot(out) == before, "a red --check must not repair anything either"


def test_check_is_blind_to_the_timestamp_alone(tmp_path: Path, capsys):
    """A regen ALWAYS changes ``generated_at``; if --check flagged that, it would be red on
    every committed manifest and teach everyone to ignore it."""
    module = _load_export_module()
    out = tmp_path / "generated"
    flags = ["--format", "both", "--deprecations-report", "--output-dir", str(out)]
    assert module.main(flags) == 0
    cj = out / "commands.json"
    payload = json.loads(cj.read_text(encoding="utf-8"))
    payload["generated_at"] = "2001-01-01T00:00:00+00:00"
    # Re-render every artifact around the pinned stamp, exactly as a regen at that instant
    # would have — the shape the committed tree is always in.
    for rel, text in module._render_artifacts(payload, "both", True).items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text, encoding="utf-8")
    capsys.readouterr()
    assert module.main(["--check", *flags]) == 0, capsys.readouterr().err


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


def test_committed_manifest_reflects_this_tree_not_a_drifted_installed_plugin():
    """A plugin install that has drifted ahead of this checkout used to leak its
    commands into the committed manifest (``export_registry`` bound ``navig`` to this
    tree but left the first-party plugins resolving through the interpreter's
    installed copies). It now pins every ``plugins/navig-*/`` dir onto ``sys.path``,
    so the committed artifact carries only THIS tree's commands — plus the new
    ``navig mind`` group this branch adds.
    """
    paths = {c["path"] for c in _shipped_manifest()["commands"]}
    assert "navig mind status" in paths
    # ``navig email otp …`` is part of THIS tree now, so the committed manifest must carry it:
    # the artifact follows the checkout, which is the whole point of the test.
    assert "navig email otp fill" in paths
    # Same class of contamination, still absent: a command from a newer navig_social build.
    assert not [p for p in paths if p.startswith("navig social cards")]


# ---------------------------------------------------------------------------
# Entry-point tree scope. Pinning sys.path fixes WHICH source a plugin loads; it cannot
# uninstall a package this repository does not ship. On 2026-10-25 the tracked manifest
# carried 11 commands (6 `navig display`, 5 `navig signing`) whose plugin directories were
# tracked in NO commit, so a clean clone could not regenerate its own manifest.
# ---------------------------------------------------------------------------

# Installed on a developer machine, tracked in no commit: legitimate at runtime, absent
# from this repository's command surface, therefore absent from the committed manifest.
_UNTRACKED_PLUGIN_MODULES = (
    "navig_display",
    "navig_signing",
    "navig_leaks",
    "navig_disk",
    "navig_exif",
    "navig_flipper",
    "navig_proc",
    "navig_domain_audit",
)


def _entry_point(name: str, value: str, group: str = "navig.commands") -> importlib.metadata.EntryPoint:
    return importlib.metadata.EntryPoint(name=name, value=value, group=group)


def test_entry_point_scope_keeps_this_tree_and_drops_the_rest():
    """The rule is "tracked in this checkout", not "under this worktree".

    Editable installs legitimately resolve to another checkout of the same repository (a
    worktree), so a path prefix would throw away real plugins; a package the repository
    does not track has no tracked file at all and must go.
    """
    module = _load_export_module()
    tracked = module._tracked_in_this_checkout(module._REPO_ROOT)
    assert tracked is not None, "this test only makes sense inside a git checkout"
    toplevels: dict[str, str | None] = {}

    in_tree = _entry_point("core", "navig.registry.manifest:build_public_manifest")
    foreign = _entry_point("pytest", "pytest:main")

    assert module.entry_point_is_in_this_tree(in_tree, tracked, toplevels) is True
    assert module.entry_point_is_in_this_tree(foreign, tracked, toplevels) is False


def test_entry_point_scope_filters_only_the_command_group(monkeypatch):
    """Scoping must be invisible to every other entry-point group, and must undo itself."""
    module = _load_export_module()
    in_tree = _entry_point("core", "navig.registry.manifest:build_public_manifest")
    foreign = _entry_point("pytest", "pytest:main")
    unrelated = _entry_point("other", "pytest:main", group="other.group")

    def fake_entry_points(*args: object, **kwargs: object):
        group = kwargs.get("group")
        if group == "navig.commands":
            return importlib.metadata.EntryPoints((in_tree, foreign))
        if group == "other.group":
            return importlib.metadata.EntryPoints((unrelated,))
        return importlib.metadata.EntryPoints(())

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)

    with module.tree_scoped_entry_points() as dropped:
        kept = importlib.metadata.entry_points(group="navig.commands")
        untouched = importlib.metadata.entry_points(group="other.group")

    assert [ep.name for ep in kept] == ["core"]
    assert [ep.name for ep in untouched] == ["other"]
    assert any(entry.startswith("pytest -> ") for entry in dropped)
    assert importlib.metadata.entry_points is fake_entry_points


def test_committed_manifest_names_no_untracked_plugin_module():
    """Pin on the artifacts: a plugin no commit ships must not be documented."""
    modules = {(c.get("module") or "").split(".", 1)[0] for c in _shipped_manifest()["commands"]}
    leaked = sorted(modules & set(_UNTRACKED_PLUGIN_MODULES))
    assert not leaked, (
        "generated/commands.json documents commands from plugin packages that no commit "
        f"tracks: {leaked}. Regenerate with: python tools/export_registry.py --format both "
        "--deprecations-report"
    )


def test_git_queries_ignore_an_inherited_git_dir(monkeypatch, tmp_path):
    """The pre-commit hook runs the exporter from INSIDE a commit, so the process inherits
    ``GIT_DIR`` / ``GIT_WORK_TREE`` binding git to the repository that called out. Honouring
    them made every entry point look foreign (measured: 65 of 65 dropped, a core-only
    manifest), which the hook then reported as stale generated surfaces on a clean checkout.
    """
    module = _load_export_module()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-a-repo"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))

    tracked = module._tracked_in_this_checkout(module._REPO_ROOT)
    assert tracked is not None, "git must still answer for the checkout we asked about"
    assert "core/tools/export_registry.py" in tracked

    toplevels: dict[str, str | None] = {}
    in_tree = _entry_point("core", "navig.registry.manifest:build_public_manifest")
    assert module.entry_point_is_in_this_tree(in_tree, tracked, toplevels) is True




