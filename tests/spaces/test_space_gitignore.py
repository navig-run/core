"""What a space commits is decided once, and it is what the flagship repo does.

Both writers of a space's `.gitignore` — the scaffold `navig space init` lays down and
the managed block `navig wire` refreshes — used to carry their own copy of the rules,
and both blanket-ignored `.navig/`. That contradicted the product three times over
(the flagship commits `.navig/plans`, every registry space commits `.navig/plans`, the
registry gitignore's header says "Safe to commit: skills … wiki"), and it hid a
per-platform inconsistency: the root `plans` LINK was not ignored, so `git add .` in a
fresh space committed the plan files *through the junction* on Windows and a dangling
symlink into an ignored directory on POSIX — the same command, different repository
content per OS.

Now there is one definition (`navig.spaces.gitignore`), it mirrors the flagship —
ignore the root links and the private state under `.navig/`, commit the rest — and
this file pins it with real `git` verdicts rather than string matching.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from navig.commands import space as space_cmd
from navig.spaces import gitignore as gi

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture(autouse=True)
def _keep_the_registry_to_this_test():
    """Undo this module's registry writes after every test.

    Seven tests here scaffold a space with the id ``demo``, and scaffolding
    registers it. The config-dir isolation in the root conftest is **session**
    scoped, so every test in a run shares one ``spaces.json`` — meaning those
    registrations leak forward and any later test that inits a space called
    ``demo`` fails with "already the id of another space". It only shows up when
    this file and the other one land in the same batch, which made it look like a
    flake in whichever change happened to pair them.

    Snapshotting the file is deliberately blunter than forgetting known ids: it
    cannot miss a write, and it keeps the blast radius inside this module rather
    than changing isolation for the whole suite.
    """
    from navig.spaces import registry as _registry  # noqa: PLC0415

    registry_file = _registry._registry_file()
    before = registry_file.read_bytes() if registry_file.is_file() else None
    try:
        yield
    finally:
        if before is None:
            registry_file.unlink(missing_ok=True)
        else:
            registry_file.write_bytes(before)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8"
    )


def _ignored(cwd: Path, path: str) -> bool:
    return _git(cwd, "check-ignore", "-q", path).returncode == 0


@pytest.fixture
def fresh_space(tmp_path: Path) -> Path:
    """A brand-new space, scaffolded and linked the way `navig space init` does it."""
    _git(tmp_path, "init", "-q", ".")
    space_cmd._scaffold_space_skeleton(tmp_path, "demo")
    space_cmd._link_space_roots(tmp_path)
    return tmp_path


# ── one owner ────────────────────────────────────────────────────────────────


def test_the_scaffold_has_no_navig_rule_outside_the_managed_block() -> None:
    """A blanket `.navig/` above the block cannot be overridden from inside it."""
    text = gi.scaffold_gitignore()
    assert not gi.blanket_navig_rule_outside_block(text)
    head = text[: text.index(gi.MANAGED_START)]
    assert ".navig" not in head, head


def test_the_scaffold_ends_with_exactly_the_managed_block() -> None:
    assert gi.scaffold_gitignore().endswith(gi.managed_block())
    assert gi.managed_block().count(gi.MANAGED_START) == 1
    assert gi.managed_block().count(gi.MANAGED_END) == 1


# ── the verdicts, from git itself ────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "plans",
        ".inbox",
        ".navig/state/session.json",
        ".navig/vault/secrets.db",
        ".navig/credentials/token",
        ".navig/memory/recall.json",
        ".navig/inbox/dropped.md",
        ".navig/refs/notes/INDEX.md",  # /inbox distillery output: private R&D
        ".navig/data/x.db",
        ".navig/logs/run.log",
        ".navig/navig.log",
        ".navig/id.key",
        ".claude/skills",
        ".claude/rules",
        ".dev/tmp/x",
        ".local/x",
    ],
)
def test_private_and_linked_paths_are_ignored(fresh_space: Path, path: str) -> None:
    assert _ignored(fresh_space, path), f"{path} should be ignored"


@pytest.mark.parametrize(
    "path",
    [
        ".navig/plans/CURRENT_PHASE.md",
        ".navig/plans/DEV_PLAN.md",
        ".navig/plans/ROADMAP.md",
        ".navig/plans/VISION.md",
        ".navig/skills/inbox/SKILL.md",
        ".navig/wiki/index.md",
        ".navig/space.json",
        ".navig/GENESIS.md",
        ".claude/settings.json",
        "NAVIG.md",
    ],
)
def test_shareable_content_is_committable(fresh_space: Path, path: str) -> None:
    assert not _ignored(fresh_space, path), f"{path} should be committable"


def test_git_add_never_walks_through_the_plans_link(fresh_space: Path) -> None:
    """The regression: on Windows `plans` is a junction and git walks into it, so the
    plan files were staged TWICE — once as `.navig/plans/…` and once as `plans/…`
    (and on POSIX the symlink went in instead). Ignoring the link makes both OSes
    commit the one canonical path."""
    r = _git(fresh_space, "ls-files", "--others", "--exclude-standard")
    listed = r.stdout.splitlines()
    assert any(p.startswith(".navig/plans/") for p in listed), listed
    assert not any(p.startswith("plans/") or p == "plans" for p in listed), listed


# (parity with the flagship's own .gitignore is a wired guard:
#  tests/quality/test_space_gitignore_matches_flagship.py)


# ── migrating spaces that were initialised before ────────────────────────────


def _wire(target: Path) -> None:
    import typer
    from typer.testing import CliRunner

    from navig.commands import wire as wire_cmd

    app = typer.Typer()
    app.command()(wire_cmd.wire_command)
    r = CliRunner().invoke(app, [str(target), "--no-register"])
    assert r.exit_code == 0, r.output


def test_wire_retires_the_old_scaffold_head_verbatim(tmp_path: Path) -> None:
    """An older init wrote a blanket `.navig/` head. It is the scaffold's own text,
    unmodified, so `navig wire` may retire it — and plans become committable."""
    _git(tmp_path, "init", "-q", ".")
    (tmp_path / ".gitignore").write_text(gi.LEGACY_SCAFFOLD_HEAD + "*.log\n", encoding="utf-8")
    assert _ignored(tmp_path, ".navig/plans/CURRENT_PHASE.md")  # the old state

    _wire(tmp_path)

    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert gi.LEGACY_SCAFFOLD_HEAD not in text
    assert "*.log" in text  # the operator's own lines survive
    assert gi.MANAGED_START in text
    assert not _ignored(tmp_path, ".navig/plans/CURRENT_PHASE.md")
    assert _ignored(tmp_path, ".navig/vault/k")
    assert _ignored(tmp_path, "plans")


def test_wire_leaves_an_edited_head_alone_and_warns(tmp_path: Path, capsys) -> None:
    """If the operator changed the head, it is theirs: keep it, say why plans stay
    uncommitted, and let them decide."""
    _git(tmp_path, "init", "-q", ".")
    edited = "# my rules\n.navig/\nbuild/\n"
    (tmp_path / ".gitignore").write_text(edited, encoding="utf-8")

    import typer
    from typer.testing import CliRunner

    from navig.commands import wire as wire_cmd

    app = typer.Typer()
    app.command()(wire_cmd.wire_command)
    r = CliRunner().invoke(app, [str(tmp_path), "--no-register"])
    assert r.exit_code == 0, r.output

    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith(edited.rstrip("\n")), text  # untouched
    assert "blanket" in r.output and ".navig/" in r.output, r.output
    assert _ignored(tmp_path, ".navig/plans/CURRENT_PHASE.md")  # still — honestly


def test_wire_is_idempotent_on_the_new_layout(fresh_space: Path) -> None:
    before = (fresh_space / ".gitignore").read_text(encoding="utf-8")
    _wire(fresh_space)
    after = (fresh_space / ".gitignore").read_text(encoding="utf-8")
    assert after == before


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (".navig/\n", True),
        ("/.navig/\n", True),
        (".navig\n", True),
        (".navig/state/\n", False),
        (gi.managed_block(), False),
        (".navig/\n" + gi.managed_block(), True),
        (gi.managed_block() + "\n.navig/\n", True),
    ],
)
def test_blanket_rule_detection(text: str, expected: bool) -> None:
    assert gi.blanket_navig_rule_outside_block(text) is expected


# ── space doctor reports the same fact, read-only ────────────────────────────


def _doctor_gitignore_row(space: Path) -> dict:
    rows = space_cmd._diagnose_space(space, "demo")["groups"]
    structure = next(g for g in rows if g["name"] == "Structure")["checks"]
    return next(c for c in structure if c["label"].startswith(".gitignore"))


def test_doctor_reports_a_fresh_space_as_committable(fresh_space: Path) -> None:
    row = _doctor_gitignore_row(fresh_space)
    assert row["status"] == "ok", row
    assert "committable" in row["label"]


def test_doctor_warns_on_a_blanket_navig_rule_outside_the_block(fresh_space: Path) -> None:
    gi_path = fresh_space / ".gitignore"
    gi_path.write_text(".navig/\n" + gi_path.read_text(encoding="utf-8"), encoding="utf-8")
    row = _doctor_gitignore_row(fresh_space)
    assert row["status"] == "warn", row
    assert "blanket" in row["detail"] and row["action"] == "retire-navig-rule"  # the menu offers it


def test_doctor_reports_a_missing_managed_block_as_fixable(tmp_path: Path) -> None:
    space_cmd._scaffold_space_skeleton(tmp_path, "demo")
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")  # no block
    row = _doctor_gitignore_row(tmp_path)
    assert row["status"] == "missing" and row["action"] == "fix", row
    assert "navig wire" in row["detail"]


def test_doctor_never_writes_the_gitignore(fresh_space: Path) -> None:
    gi_path = fresh_space / ".gitignore"
    gi_path.write_text(".navig/\n", encoding="utf-8")
    before = gi_path.read_text(encoding="utf-8")
    _doctor_gitignore_row(fresh_space)
    assert gi_path.read_text(encoding="utf-8") == before


def test_wire_twice_on_a_space_with_no_block_changes_nothing_the_second_time(tmp_path: Path) -> None:
    """The append path wrote a blank line before the block that the refresh path then
    collapsed — so the FIRST wire appended and the SECOND reported "refresh": 19 real
    spaces reported a change on a run that changed nothing. Append must lay the block
    down exactly as refresh normalises it."""
    _git(tmp_path, "init", "-q", ".")
    (tmp_path / ".gitignore").write_text("node_modules/\n*.log\n", encoding="utf-8")
    _wire(tmp_path)
    after_first = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "*.log\n" + gi.MANAGED_START in after_first, "one newline before the block, no blank line"
    _wire(tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == after_first


def test_wire_creates_the_gitignore_when_absent_and_is_then_stable(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", ".")
    assert not (tmp_path / ".gitignore").exists()
    _wire(tmp_path)
    first = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    # the skeleton step inside wire lays down the full scaffold file (head + block)
    assert first == gi.scaffold_gitignore()
    assert first.count(gi.MANAGED_START) == 1
    _wire(tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == first


# ── doctor --fix repairs what doctor reports, with the code wire runs ────────


def test_doctor_fix_retires_the_legacy_head_like_wire_does(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", ".")
    space_cmd._scaffold_space_skeleton(tmp_path, "demo")
    (tmp_path / ".gitignore").write_text(gi.LEGACY_SCAFFOLD_HEAD + "*.log\n", encoding="utf-8")
    assert _doctor_gitignore_row(tmp_path)["status"] == "warn"  # before

    space_cmd._apply_fix(tmp_path, "demo")

    assert _doctor_gitignore_row(tmp_path)["status"] == "ok"
    assert not _ignored(tmp_path, ".navig/plans/CURRENT_PHASE.md")
    assert _ignored(tmp_path, "plans")


def test_doctor_fix_adds_a_missing_block(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", ".")
    space_cmd._scaffold_space_skeleton(tmp_path, "demo")
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    assert _doctor_gitignore_row(tmp_path)["status"] == "missing"
    space_cmd._apply_fix(tmp_path, "demo")
    assert _doctor_gitignore_row(tmp_path)["status"] == "ok"


def test_doctor_fix_leaves_an_edited_head_alone_too(tmp_path: Path) -> None:
    """The operator's own blanket rule is theirs, whichever door the repair comes through."""
    _git(tmp_path, "init", "-q", ".")
    space_cmd._scaffold_space_skeleton(tmp_path, "demo")
    edited = "# mine\n.navig/\nbuild/\n"
    (tmp_path / ".gitignore").write_text(edited, encoding="utf-8")
    space_cmd._apply_fix(tmp_path, "demo")
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith(edited.rstrip("\n")), text
    assert _doctor_gitignore_row(tmp_path)["status"] == "warn"  # still, honestly


def test_wire_and_doctor_fix_produce_the_same_gitignore(tmp_path: Path) -> None:
    """One implementation: the row doctor offers to fix cannot drift from what wire does."""
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir(); _git(d, "init", "-q", ".")
        space_cmd._scaffold_space_skeleton(d, "demo")
        (d / ".gitignore").write_text(gi.LEGACY_SCAFFOLD_HEAD + "build/\n", encoding="utf-8")
    _wire(a)
    space_cmd._apply_fix(b, "demo")
    assert (a / ".gitignore").read_text(encoding="utf-8") == (b / ".gitignore").read_text(encoding="utf-8")


def test_reconcile_is_idempotent_and_reports_nothing_the_second_time(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(gi.LEGACY_SCAFFOLD_HEAD + "x/\n", encoding="utf-8")
    first = gi.reconcile(tmp_path)
    assert any("retire" in a for a in first) and any("managed block" in a for a in first)
    assert gi.reconcile(tmp_path) == []


# ── one rule for the registry's source column ────────────────────────────────


def test_source_for_is_root_only_for_a_direct_child_of_the_spaces_dir(tmp_path: Path, monkeypatch) -> None:
    from navig.spaces import registry as reg

    spaces = tmp_path / "cfg" / "spaces"
    spaces.mkdir(parents=True)
    monkeypatch.setattr(reg.paths, "spaces_dir", lambda: spaces)
    (spaces / "homelab").mkdir()
    (spaces / "homelab" / "sub").mkdir()
    (tmp_path / "proj").mkdir()
    assert reg.source_for(spaces / "homelab") == "root"
    assert reg.source_for(spaces / "homelab" / "sub") == "external"  # nested = a sub-space
    assert reg.source_for(tmp_path / "proj") == "external"
    assert reg.source_for(str(spaces / "homelab")) == "root"  # str accepted


def test_doctor_fix_files_an_external_project_as_external(tmp_path: Path, monkeypatch) -> None:
    """It hardcoded "root": an unregistered project folder repaired by doctor was filed
    as a ~/.navig/spaces resident."""
    from navig.spaces import registry as reg

    monkeypatch.setattr(reg.paths, "spaces_dir", lambda: tmp_path / "spaces")
    seen: dict = {}
    monkeypatch.setattr(reg, "ensure_registered", lambda path, **kw: seen.update(kw))
    proj = tmp_path / "proj"
    proj.mkdir()
    space_cmd._scaffold_space_skeleton(proj, "proj")
    space_cmd._apply_fix(proj, "proj")
    assert seen.get("source") == "external", seen


# ── retiring the operator's own blanket rule: offered, shown, confirmed, exact ─


def test_blanket_rule_lines_are_found_outside_the_block_only(tmp_path: Path) -> None:
    text = "# navig session artifacts\n.navig/\nbuild/\n" + gi.managed_block() + "/.navig\n"
    assert gi.blanket_navig_rule_lines(text) == [2, 2 + len(gi.managed_block().splitlines()) + 2]


def test_retire_removes_only_the_rule_and_its_label(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "node_modules/\n# navig session artifacts\n.navig/\nbuild/\n" + gi.managed_block(), encoding="utf-8"
    )
    removed = gi.retire_blanket_navig_rules(tmp_path)
    assert removed == ["# navig session artifacts", ".navig/"]
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith("node_modules/\nbuild/\n"), text
    assert gi.MANAGED_START in text and ".navig/state/" in text  # the block is untouched
    assert not gi.blanket_navig_rule_outside_block(text)
    assert gi.retire_blanket_navig_rules(tmp_path) == []  # idempotent


def test_retire_keeps_an_unrelated_comment_above_the_rule(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("# build output\n.navig/\n", encoding="utf-8")
    assert gi.retire_blanket_navig_rules(tmp_path) == [".navig/"]
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "# build output\n"


def test_the_doctor_menu_offers_retire_only_when_a_blanket_rule_exists(fresh_space: Path) -> None:
    keys = [k for k, _ in space_cmd._doctor_menu_options(space_cmd._diagnose_space(fresh_space, "demo"))]
    assert "retire" not in keys
    gi_path = fresh_space / ".gitignore"
    gi_path.write_text(".navig/\n" + gi_path.read_text(encoding="utf-8"), encoding="utf-8")
    keys = [k for k, _ in space_cmd._doctor_menu_options(space_cmd._diagnose_space(fresh_space, "demo"))]
    assert "retire" in keys


def test_retire_from_the_menu_asks_and_a_no_changes_nothing(fresh_space: Path, monkeypatch) -> None:
    gi_path = fresh_space / ".gitignore"
    gi_path.write_text(".navig/\n" + gi_path.read_text(encoding="utf-8"), encoding="utf-8")
    before = gi_path.read_text(encoding="utf-8")
    answers = iter(["retire", "q"])
    monkeypatch.setattr("typer.prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)  # the operator says no
    space_cmd._doctor_interactive_loop(fresh_space, "demo")
    assert gi_path.read_text(encoding="utf-8") == before


def test_retire_from_the_menu_with_a_yes_removes_the_rule_and_plans_become_committable(
    fresh_space: Path, monkeypatch
) -> None:
    gi_path = fresh_space / ".gitignore"
    gi_path.write_text(".navig/\n" + gi_path.read_text(encoding="utf-8"), encoding="utf-8")
    assert _ignored(fresh_space, ".navig/plans/CURRENT_PHASE.md")
    answers = iter(["retire", "q"])
    monkeypatch.setattr("typer.prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    space_cmd._doctor_interactive_loop(fresh_space, "demo")
    assert not gi.blanket_navig_rule_outside_block(gi_path.read_text(encoding="utf-8"))
    assert not _ignored(fresh_space, ".navig/plans/CURRENT_PHASE.md")
    assert _ignored(fresh_space, ".navig/vault/k")


def test_the_confirm_defaults_to_no() -> None:
    """A confirm that defaults to Yes is an edit that happens on Enter."""
    import inspect

    src = inspect.getsource(space_cmd._doctor_interactive_loop)
    assert 'typer.confirm("Remove these lines?", default=False)' in src
