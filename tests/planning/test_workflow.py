"""Tests for the retired workflow engine + the ops Blocks that replaced it.

The System-A workflow engine (``WorkflowManager``: YAML command-sequences run via
``navig task``/``navig flow``) was retired; its builtin content migrated to
**Blocks** (``navig apply``). ``navig task``/``navig flow`` remain as deprecation
shims. See ``docs/blocks-vs-workflows.md``.
"""

import pytest

pytestmark = pytest.mark.integration


# ============================================================================
# RETIREMENT — the engine is gone, the CLI verbs survive as shims
# ============================================================================


class TestWorkflowEngineRetired:
    """System-A engine classes removed; the verbs remain as Block redirects."""

    def test_engine_classes_removed(self):
        import navig.commands.workflow as wf

        assert not hasattr(wf, "WorkflowManager"), "WorkflowManager should be retired"
        assert not hasattr(wf, "WorkflowStep"), "WorkflowStep should be retired"

    def test_task_app_still_exposed(self):
        # `navig flow`/`job` and registration.py import task_app by name.
        from navig.commands.workflow import task_app

        assert task_app is not None

    @pytest.mark.parametrize(
        "fn",
        [
            "list_workflows",
            "show_workflow",
            "run_workflow",
            "validate_workflow",
            "create_workflow",
            "delete_workflow",
            "edit_workflow",
        ],
    )
    def test_shim_functions_importable(self, fn):
        # `navig flow` imports these by name — they must stay callable.
        import navig.commands.workflow as wf

        assert callable(getattr(wf, fn))

    def test_run_unknown_name_errors(self):
        from navig.commands.workflow import run_workflow

        with pytest.raises(SystemExit):
            run_workflow("definitely-not-a-block-xyz")

    def test_run_block_name_redirects_without_error(self):
        """A Block name prints the `navig apply` redirect and returns cleanly."""
        from navig.commands.workflow import run_workflow

        # server-health is a migrated builtin Block; the shim must NOT raise.
        run_workflow("server-health")


# ============================================================================
# BUILTIN OPS BLOCK TESTS
# ============================================================================
# The four builtin ops workflows migrated to Blocks (installable, verifiable
# outcomes — `navig apply`), shipped as BLOCK.md via the community registry.


class TestBuiltinOpsBlocks:
    """The four builtin ops workflows now ship as Blocks."""

    EXPECTED = ["safe-deployment", "db-snapshot", "emergency-debug", "server-health"]

    def test_builtin_blocks_exist(self):
        from navig.blocks import discover_blocks

        ids = {b.id for b in discover_blocks()}
        for bid in self.EXPECTED:
            assert bid in ids, f"Built-in block '{bid}' not found (did the migration ship?)"

    def test_builtin_blocks_valid(self):
        from navig.blocks import find_block, validate_block

        for bid in self.EXPECTED:
            block = find_block(bid)
            assert block is not None, f"Failed to load block '{bid}'"
            problems = validate_block(block)
            assert not problems, f"Block '{bid}' has problems: {problems}"

    def test_all_builtin_blocks_valid(self):
        """Every BLOCK.md shipped in the builtin store is linter-clean."""
        from navig.blocks.loader import discover_blocks, validate_block
        from navig.platform.paths import builtin_store_dir

        blocks_dir = builtin_store_dir() / "blocks"
        discovered = discover_blocks([blocks_dir])
        assert discovered, "no builtin blocks discovered"
        for b in discovered:
            problems = validate_block(b)
            assert not problems, f"builtin block '{b.id}': {problems}"

    def test_safe_deployment_structure(self):
        """Deploy is a verifiable outcome (post-deploy health check)."""
        from navig.blocks import find_block

        block = find_block("safe-deployment")
        assert block is not None
        assert {"host", "app_path", "build_dir", "service"} <= {i.key for i in block.inputs}
        assert len(block.steps) >= 5
        assert block.verify.kind == "command"

    def test_db_snapshot_structure(self):
        """The dump lands as a machine-verifiable local file."""
        from navig.blocks import find_block

        block = find_block("db-snapshot")
        assert block is not None
        assert "db_name" in {i.key for i in block.inputs}
        assert any("dump" in s.id for s in block.steps)
        assert block.verify.kind == "file_exists"

    def test_mutating_steps_are_gated(self):
        """Server-mutating steps compute to destructive risk (need a named --approve)."""
        from navig.blocks import capability_risk, find_block

        block = find_block("safe-deployment")
        risks = {s.id: capability_risk(s.capabilities) for s in block.steps}
        for sid in ("backup", "upload", "restart"):
            assert risks[sid] == "destructive", f"'{sid}' must be destructive, got {risks[sid]}"

    def test_diagnostics_are_readonly(self):
        """Diagnostics have no verifiable outcome and no destructive step."""
        from navig.blocks import capability_risk, find_block

        for bid in ("server-health", "emergency-debug"):
            block = find_block(bid)
            assert block.verify.kind == "none"
            assert all(capability_risk(s.capabilities) != "destructive" for s in block.steps)


# ============================================================================
# MIGRATED RUNBOOK BLOCKS — the community pack + single-workflow packages
# ============================================================================


class TestMigratedRunbookBlocks:
    """The community runbooks + single-workflow packages are now instruction Blocks."""

    MIGRATED = [
        "deployment-checklist",
        "docker-health",
        "security-audit",
        "devops-shortcuts",
        "backup-runbook",
        "startup",
        "backup-essential",
        "lifeos",
    ]

    def test_migrated_blocks_exist(self):
        from navig.blocks import discover_blocks

        ids = {b.id for b in discover_blocks()}
        for bid in self.MIGRATED:
            assert bid in ids, f"Migrated runbook block '{bid}' not found"

    def test_migrated_blocks_are_instruction_only(self):
        """Doc-style runbooks are guided instruction Blocks — no fake execution."""
        from navig.blocks import find_block

        for bid in self.MIGRATED:
            block = find_block(bid)
            assert block is not None, f"Failed to load '{bid}'"
            assert block.verify.kind == "none"
            assert all(s.kind == "instruction" for s in block.steps), (
                f"'{bid}' should be instruction-only"
            )
