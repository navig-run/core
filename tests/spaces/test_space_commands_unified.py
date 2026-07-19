from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.space import space_app

pytestmark = pytest.mark.integration

runner = CliRunner()


class _FakeConfigManager:
    def __init__(self, base: Path, global_config: dict | None = None):
        self.global_config_dir = str(base)
        self.global_config = global_config or {}


def test_space_create_switch_current_list_and_delete(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    created = runner.invoke(space_app, ["create", "work"])
    assert created.exit_code == 0
    assert (global_cfg / "spaces" / "work").is_dir()

    switched = runner.invoke(space_app, ["use", "work"])
    assert switched.exit_code == 0
    assert "Active space: work" in switched.stdout

    current = runner.invoke(space_app, ["current"])
    assert current.exit_code == 0
    assert "Active space: work" in current.stdout

    listed = runner.invoke(space_app, ["list"])
    assert listed.exit_code == 0
    assert "work" in listed.stdout

    deleted = runner.invoke(space_app, ["delete", "work", "--yes"])
    assert deleted.exit_code == 0
    assert not (global_cfg / "spaces" / "work").exists()

    current_after = runner.invoke(space_app, ["current"])
    assert current_after.exit_code == 0
    assert (
        "default" in current_after.stdout
    )  # displayed as "My Space (default)" or "Active space: default"


def test_space_init_scaffolds_inbox_distillery(tmp_path, monkeypatch):
    """Every new space is born with the /inbox distillery: the skill capability under
    .navig/skills/inbox and the .navig/refs/notes library + drop-zone dirs."""
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    created = runner.invoke(space_app, ["init", "studio"])
    assert created.exit_code == 0
    space = global_cfg / "spaces" / "studio"

    # the skill (machine-local capability, wired into .claude/ by `navig wire`)
    skill = space / ".navig" / "skills" / "inbox"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "references" / "rubrics.md").is_file()
    assert (skill / "references" / "template.md").is_file()
    assert (skill / "references" / "preprocess.md").is_file()
    assert (skill / "references" / "project-profile.md").is_file()
    assert (skill / "BOOTSTRAP.md").is_file()

    # the committed library + the never-delete drop-zone dirs
    assert (space / ".navig" / "refs" / "notes" / "README.md").is_file()
    assert (space / ".navig" / "refs" / "notes" / "INDEX.md").is_file()
    assert (space / ".navig" / "inbox" / "_originals").is_dir()
    # sources are distilled IN PLACE now — no processed/ or _intake/ shuffling
    assert not (space / ".navig" / "inbox" / "processed").exists()
    assert (space / ".inbox").exists()  # drop zone surfaced as the hidden .inbox alias

    # auto-wired: the skill is visible to Claude Code under .claude/skills (junction →
    # .navig/skills), so /inbox works right after init — no separate `navig wire` step.
    assert (space / ".claude" / "skills" / "inbox" / "SKILL.md").is_file()


def test_space_init_distillery_never_clobbers_existing(tmp_path, monkeypatch):
    """Scaffolding onto an existing repo leaves a user's own SKILL.md byte-for-byte."""
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    space = global_cfg / "spaces" / "mine"
    skill = space / ".navig" / "skills" / "inbox"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("MY OWN SKILL — DO NOT TOUCH\n", encoding="utf-8")

    created = runner.invoke(space_app, ["init", "mine"])
    assert created.exit_code == 0
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "MY OWN SKILL — DO NOT TOUCH\n"
    # but missing engine files are still added alongside the preserved one
    assert (skill / "references" / "rubrics.md").is_file()


def test_space_doctor_healthy_after_init(tmp_path, monkeypatch):
    """A freshly-initialized space passes the doctor with zero missing items."""
    global_cfg = tmp_path / "global"
    monkeypatch.setattr(
        "navig.commands.space.get_config_manager", lambda: _FakeConfigManager(global_cfg)
    )
    assert runner.invoke(space_app, ["init", "studio"]).exit_code == 0
    space = global_cfg / "spaces" / "studio"

    res = runner.invoke(space_app, ["doctor", str(space)])
    assert res.exit_code == 0
    assert "0 missing" in res.stdout
    assert "Distillery" in res.stdout
    assert "Knowledge homes" in res.stdout   # wiki/docs/.lab-refs/plans/tasks/ideas map
    assert "Media tools" in res.stdout        # ffmpeg/whisper readiness for `navig media`
    # a never-configured profile must be reported as UNCONFIGURED (not spoofed by the
    # comment that mentions "Status: CONFIGURED"), pointing the user at the first /inbox run.
    assert "UNCONFIGURED" in res.stdout


def test_space_doctor_detects_then_fixes_without_overwriting(tmp_path, monkeypatch):
    """The core promise: on an already-initialized space, doctor reports what's missing,
    --fix adds ONLY the missing pieces, and a user's edited file is left byte-for-byte."""
    global_cfg = tmp_path / "global"
    monkeypatch.setattr(
        "navig.commands.space.get_config_manager", lambda: _FakeConfigManager(global_cfg)
    )
    assert runner.invoke(space_app, ["init", "studio"]).exit_code == 0
    space = global_cfg / "spaces" / "studio"

    # simulate a partially-degraded space: user customized the INDEX, and a rubric + the
    # library README went missing.
    index = space / ".navig" / "refs" / "notes" / "INDEX.md"
    index.write_text("# MY CURATED INDEX — keep this\n", encoding="utf-8")
    (space / ".navig" / "refs" / "notes" / "README.md").unlink()
    (space / ".navig" / "skills" / "inbox" / "references" / "rubrics.md").unlink()

    # doctor (read-only) should flag the gaps and exit non-zero, writing nothing.
    report = runner.invoke(space_app, ["doctor", str(space)])
    assert report.exit_code == 1
    assert "Next actions" in report.stdout
    assert "navig space doctor --fix" in report.stdout  # the exact remedy command is shown
    assert "README.md" in report.stdout  # the missing library file is named
    assert not (space / ".navig" / "refs" / "notes" / "README.md").exists()  # untouched by read-only run

    # --fix restores exactly the missing pieces...
    fixed = runner.invoke(space_app, ["doctor", str(space), "--fix"])
    assert fixed.exit_code == 0
    assert "0 missing" in fixed.stdout
    assert (space / ".navig" / "refs" / "notes" / "README.md").is_file()
    assert (space / ".navig" / "skills" / "inbox" / "references" / "rubrics.md").is_file()
    # ...and never clobbers the user's own edit.
    assert index.read_text(encoding="utf-8") == "# MY CURATED INDEX — keep this\n"


def test_space_doctor_agents_flag_wires_all_assistants(tmp_path, monkeypatch):
    """--agents makes the space legible to Claude · Copilot · Cursor · Gemini · Codex —
    additively (idempotent, never overwrites an existing instruction file)."""
    global_cfg = tmp_path / "global"
    monkeypatch.setattr(
        "navig.commands.space.get_config_manager", lambda: _FakeConfigManager(global_cfg)
    )
    assert runner.invoke(space_app, ["init", "studio"]).exit_code == 0
    space = global_cfg / "spaces" / "studio"

    # a user already has their own Copilot file — it must be preserved.
    copilot = space / ".github" / "copilot-instructions.md"
    copilot.parent.mkdir(parents=True, exist_ok=True)
    copilot.write_text("MY COPILOT RULES\n", encoding="utf-8")

    res = runner.invoke(space_app, ["doctor", str(space), "--agents"])
    assert res.exit_code == 0
    assert (space / "AGENTS.md").is_file()
    assert (space / ".cursor" / "rules" / "navig.mdc").is_file()
    assert (space / "GEMINI.md").is_file()
    assert (space / "CLAUDE.md").is_file()  # scaffolded by init, untouched
    # the user's own Copilot file is preserved byte-for-byte
    assert copilot.read_text(encoding="utf-8") == "MY COPILOT RULES\n"

    # idempotent: a second run changes nothing.
    before = {p.name: p.read_text(encoding="utf-8") for p in (space / ".cursor" / "rules").iterdir()}
    assert runner.invoke(space_app, ["doctor", str(space), "--agents"]).exit_code == 0
    after = {p.name: p.read_text(encoding="utf-8") for p in (space / ".cursor" / "rules").iterdir()}
    assert before == after


def test_space_doctor_update_refreshes_engine_but_keeps_profile(tmp_path, monkeypatch):
    """--update refreshes the shared engine files to the shipped version but never touches the
    per-space project-profile.md or the distilled library (the update propagation path)."""
    global_cfg = tmp_path / "global"
    monkeypatch.setattr(
        "navig.commands.space.get_config_manager", lambda: _FakeConfigManager(global_cfg)
    )
    assert runner.invoke(space_app, ["init", "studio"]).exit_code == 0
    space = global_cfg / "spaces" / "studio"
    skill = space / ".navig" / "skills" / "inbox"

    # simulate an outdated engine copy + a user-configured profile.
    (skill / "SKILL.md").write_text("OLD v0.9 SKILL — outdated\n", encoding="utf-8")
    profile = skill / "references" / "project-profile.md"
    profile.write_text("- **Status:** CONFIGURED\nMY OWN BINDINGS\n", encoding="utf-8")

    # doctor flags the drift.
    report = runner.invoke(space_app, ["doctor", str(space)])
    assert "outdated" in report.stdout

    # --update refreshes the engine...
    res = runner.invoke(space_app, ["doctor", str(space), "--update"])
    assert res.exit_code == 0
    from navig.commands.space import _SCAFFOLD_TEMPLATES
    shipped = (_SCAFFOLD_TEMPLATES / "space-distillery" / "skill" / "SKILL.md").read_text(encoding="utf-8")
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == shipped  # engine now current
    # ...and leaves the per-space profile byte-for-byte.
    assert profile.read_text(encoding="utf-8") == "- **Status:** CONFIGURED\nMY OWN BINDINGS\n"


def test_space_doctor_interactive_menu_fixes_then_quits(tmp_path, monkeypatch):
    """In a TTY the doctor loops on a menu instead of exiting: choosing '1' repairs, 'q' quits."""
    global_cfg = tmp_path / "global"
    monkeypatch.setattr(
        "navig.commands.space.get_config_manager", lambda: _FakeConfigManager(global_cfg)
    )
    monkeypatch.setattr("navig.commands.space._stdin_is_tty", lambda: True)  # pretend a terminal
    assert runner.invoke(space_app, ["init", "studio"]).exit_code == 0
    space = global_cfg / "spaces" / "studio"
    (space / ".navig" / "refs" / "notes" / "README.md").unlink()

    # menu: option 1 = Fix, then quit.
    res = runner.invoke(space_app, ["doctor", str(space)], input="1\nq\n")
    assert res.exit_code == 0
    assert "What next?" in res.stdout
    assert (space / ".navig" / "refs" / "notes" / "README.md").is_file()  # repaired via the menu


def test_space_create_invalid_slug_shows_builtin_hint(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    result = runner.invoke(space_app, ["create", "Bad_Name"])
    assert result.exit_code != 0
    combined = (
        (result.stdout or "")
        + (getattr(result, "stderr", "") or "")
        + (getattr(result, "output", "") or "")
    )
    assert "Invalid space name" in combined
    assert "Common spaces" in combined


def test_space_current_prefers_env_var(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg, {"space": {"active": "work"}})
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)
    monkeypatch.setenv("NAVIG_SPACE", "focus")

    result = runner.invoke(space_app, ["current"])
    assert result.exit_code == 0
    assert "Active space: focus" in result.stdout


def test_space_init_scaffolds_full_structure(tmp_path):
    dest = tmp_path / "demo"
    result = runner.invoke(space_app, ["init", "demo", "--path", str(dest)])
    assert result.exit_code == 0
    # canonical state dirs + plans + hygiene zones
    assert (dest / ".navig" / "plans" / "CURRENT_PHASE.md").is_file()
    assert (dest / ".navig" / "inbox").is_dir()
    assert (dest / ".dev").is_dir() and (dest / ".local").is_dir() and (dest / "docs").is_dir()
    assert (dest / ".navig" / "space.config.json").is_file()
    # root links resolve into .navig (junction on Windows, symlink on POSIX).
    # the inbox drop zone is surfaced as the hidden dotfolder `.inbox`.
    assert (dest / "plans" / "VISION.md").exists()
    assert (dest / ".inbox" / "_originals").exists()  # drop-zone dir via the .inbox root link
    # .local is gitignored
    assert ".local/" in (dest / ".gitignore").read_text()


def test_space_init_is_purely_additive(tmp_path):
    dest = tmp_path / "proj"
    # a user file that must survive untouched
    (dest / ".navig" / "plans").mkdir(parents=True)
    sentinel = dest / ".navig" / "plans" / "CURRENT_PHASE.md"
    sentinel.write_text("USER CONTENT — KEEP")
    # a FILE sitting where a folder belongs must not be clobbered
    (dest / ".dev").write_text("i am a file")

    result = runner.invoke(space_app, ["init", "proj", "--path", str(dest)])
    assert result.exit_code == 0
    assert sentinel.read_text() == "USER CONTENT — KEEP"   # never overwritten
    assert (dest / ".dev").is_file()                        # conflict, not clobbered
    assert (dest / ".dev").read_text() == "i am a file"
    assert "conflict" in result.output.lower()

    # idempotent: a second pass creates nothing new
    from navig.commands.space import _scaffold_space_skeleton

    summary = _scaffold_space_skeleton(dest, "proj")
    assert summary["created"] == []


def test_space_init_dry_run_writes_nothing(tmp_path):
    dest = tmp_path / "ghost"
    result = runner.invoke(space_app, ["init", "ghost", "--path", str(dest), "--dry-run"])
    assert result.exit_code == 0
    assert "Would create" in result.output
    assert not dest.exists()  # nothing written


# ── an INCOMPLETE INSTALL must be loud, not a silent partial space ────────────

def test_space_init_reports_a_missing_packaged_template(tmp_path, monkeypatch):
    """REGRESSION (found by installing the real wheel): when `navig/scaffold-templates` was
    absent from the package — which was true of EVERY published wheel, because package-data
    never declared it — `space init` silently skipped the whole /inbox distillery capability,
    printed "✓ Created space" AND "Ready: … run /inbox", and produced a space that
    `navig space doctor` then failed with exit 1. A missing capability must be reported.
    """
    from navig.commands import space as space_mod

    monkeypatch.setattr(space_mod, "_SCAFFOLD_TEMPLATES", tmp_path / "not-installed")
    dest = tmp_path / "proj"
    result = runner.invoke(space_app, ["init", "proj", "--path", str(dest)])

    assert result.exit_code == 0                       # the space itself is still created
    assert not (dest / ".navig" / "skills" / "inbox" / "SKILL.md").exists()
    out = result.output.lower()
    assert "incomplete" in out, "a missing packaged template must be reported, not skipped"
    assert "scaffold-templates" in out
    # …and it must NOT promise a capability it did not create
    assert "run /inbox to distil" not in result.output


def test_space_init_scaffolds_the_distillery_when_the_template_ships(tmp_path):
    """The happy path the wheel now delivers: the /inbox skill + library land in the space."""
    dest = tmp_path / "proj"
    result = runner.invoke(space_app, ["init", "proj", "--path", str(dest)])
    assert result.exit_code == 0
    assert "incomplete" not in result.output.lower()
    for rel in ("SKILL.md", "BOOTSTRAP.md", "references/rubrics.md",
                "references/template.md", "references/preprocess.md",
                "references/project-profile.md"):
        assert (dest / ".navig" / "skills" / "inbox" / rel).is_file(), rel
    assert (dest / ".navig" / "refs" / "notes" / "INDEX.md").is_file()


# ---------------------------------------------------------------------------
# resolve_active_space() — the single canonical reader (env → cache file → config)
# ---------------------------------------------------------------------------

def _cache_cm(base: Path, global_config: dict | None = None):
    """A fake CM whose global_config_dir is `base` (where the cache file lives)."""
    return _FakeConfigManager(base, global_config)


def test_resolve_active_space_none_when_unset(tmp_path, monkeypatch):
    from navig.commands.space import get_active_space, resolve_active_space

    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: _cache_cm(tmp_path))

    assert resolve_active_space() is None
    # get_active_space supplies the "default" fallback over the same resolver.
    assert get_active_space() == "default"


def test_resolve_active_space_reads_cache_file_source_of_truth(tmp_path, monkeypatch):
    """The cache file is what `navig space switch` writes FIRST — it must win over an empty
    config. This is the gap the deck's old config-only reader missed."""
    from navig.commands.space import resolve_active_space

    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    # Config is EMPTY, but the cache file (source of truth) names a space.
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: _cache_cm(tmp_path, {}))
    cache = tmp_path / "cache" / "active_space.txt"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("from-cache", encoding="utf-8")

    assert resolve_active_space() == "from-cache"


def test_resolve_active_space_env_overrides_everything(tmp_path, monkeypatch):
    from navig.commands.space import resolve_active_space

    monkeypatch.setattr(
        "navig.commands.space.get_config_manager",
        lambda: _cache_cm(tmp_path, {"space": {"active": "cfg"}}),
    )
    cache = tmp_path / "cache" / "active_space.txt"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("from-cache", encoding="utf-8")
    monkeypatch.setenv("NAVIG_SPACE", "from-env")

    assert resolve_active_space() == "from-env"


def test_resolve_active_space_falls_back_to_config_keys(tmp_path, monkeypatch):
    """No env, no cache file → the config-key mirror (space.active) resolves."""
    from navig.commands.space import resolve_active_space

    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    monkeypatch.setattr(
        "navig.commands.space.get_config_manager",
        lambda: _cache_cm(tmp_path, {"space": {"active": "cfg-space"}}),
    )

    assert resolve_active_space() == "cfg-space"
