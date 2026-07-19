"""Regression tests: environment-sensitive paths resolve at CALL time, not import time.

PR #179 proved the danger of module-level path constants derived from
``config_dir()`` / ``Path.home()``: they freeze BEFORE ``NAVIG_CONFIG_DIR``
isolation applies (pytest session fixtures, daemons finalizing config after
imports), and vault auto-migration copied the operator's REAL credentials
into isolated test vaults.

Every test here follows the same shape, which the pre-fix code fails:

1. import the module (path would freeze here pre-fix),
2. THEN set ``NAVIG_CONFIG_DIR`` (or home) to an isolated directory,
3. call the resolver and assert the isolated directory won.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def cfg(tmp_path, monkeypatch) -> Path:
    """Isolated NAVIG_CONFIG_DIR applied AFTER the module imports above/below."""
    custom = tmp_path / "isolated_cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    # NAVIG_HOME (legacy) would shadow NAVIG_CONFIG_DIR in navig.memory.paths;
    # make sure it does not leak into these assertions.
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    return custom


# ---------------------------------------------------------------------------
# vault (the PR #179 reference fix)
# ---------------------------------------------------------------------------


def test_vault_legacy_db_path(cfg):
    from navig.vault.migrate import _legacy_db_path

    assert _legacy_db_path() == cfg / "credentials" / "vault.db"


def test_vault_migration_report_default_source(cfg):
    from navig.vault.migrate import MigrationReport

    assert MigrationReport().source == cfg / "credentials" / "vault.db"


# ---------------------------------------------------------------------------
# agent modules
# ---------------------------------------------------------------------------


def test_background_task_output_dir(cfg):
    from navig.agent.background_task import BackgroundTaskManager, _default_output_dir

    assert _default_output_dir() == cfg / "bg_tasks"
    assert BackgroundTaskManager()._output_dir == cfg / "bg_tasks"


def test_pattern_observer_db_path(cfg):
    from navig.agent.pattern_observer import PatternObserver, default_db_path

    assert default_db_path() == cfg / "data" / "pattern_log.sqlite"
    assert PatternObserver().db_path == cfg / "data" / "pattern_log.sqlite"


def test_tool_caps_spillover_dir(cfg):
    from navig.agent.tool_caps import _spillover_dir

    assert _spillover_dir() == cfg / "tmp" / "tool_spillover"


def test_tool_caps_spillover_test_seam(cfg, tmp_path):
    import navig.agent.tool_caps as mod

    seam = tmp_path / "seam"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, "SPILLOVER_DIR", seam)
        assert mod._spillover_dir() == seam
    assert mod._spillover_dir() == cfg / "tmp" / "tool_spillover"


def test_skills_context_global_dir(cfg):
    from navig.agent.skills_context import _global_skills_dir

    assert _global_skills_dir() == cfg / "skills"


# ---------------------------------------------------------------------------
# platform.paths — user-content dir helpers (the single home per type)
# ---------------------------------------------------------------------------


def test_user_content_dir_helpers(cfg):
    """scripts_dir / skills_dir / workflows_dir resolve at CALL time (honour
    NAVIG_CONFIG_DIR). These are the ONE home each type — `navig script`, `navig evolve *`,
    navig ahk / mount, skills_context, and skill_drafter all route through them so the
    location can never diverge again (see #276/#281/#285)."""
    from navig.platform.paths import scripts_dir, skills_dir, workflows_dir

    assert scripts_dir() == cfg / "scripts"
    assert skills_dir() == cfg / "skills"
    assert workflows_dir() == cfg / "workflows"


def test_consumers_route_through_the_helpers(cfg):
    """Every reader/writer must agree with the shared helper — the invariant that broke in
    #285 (navig script pointed at navig/scripts while everything else used config_dir/scripts)."""
    from navig.agent.skill_drafter import SkillDrafter
    from navig.agent.skills_context import _global_skills_dir
    from navig.commands.script import _get_scripts_dir
    from navig.core.automation_engine import WorkflowEngine
    from navig.core.evolution.script import ScriptEvolver
    from navig.core.evolution.skill import SkillEvolver
    from navig.core.evolution.workflow import WorkflowEvolver
    from navig.platform.paths import scripts_dir, skills_dir, workflows_dir

    assert _get_scripts_dir() == scripts_dir()
    assert ScriptEvolver()._scripts_dir == scripts_dir()
    assert _global_skills_dir() == skills_dir()
    assert SkillEvolver()._skills_root == skills_dir()
    assert SkillDrafter().output_dir == skills_dir()
    assert WorkflowEvolver()._workflows_dir == workflows_dir()
    assert WorkflowEngine()._workflows_dir == workflows_dir()


def test_system_dir_helpers(cfg):
    """plugins_dir / spaces_dir / wiki_dir resolve at CALL time (honour NAVIG_CONFIG_DIR).
    spaces is PLURAL — the singular config_dir()/space is a non-existent directory."""
    from navig.platform.paths import plugins_dir, spaces_dir, wiki_dir

    assert plugins_dir() == cfg / "plugins"
    assert spaces_dir() == cfg / "spaces"
    assert wiki_dir() == cfg / "wiki"


def test_local_resolvers_delegate_to_the_homes(cfg):
    """The existing local resolvers must now agree with the canonical homes so there is one
    source of truth for each dir."""
    from navig.commands.plugin import _user_plugins_dir
    from navig.commands.wiki import get_global_wiki_path
    from navig.platform.paths import plugins_dir, wiki_dir

    assert _user_plugins_dir() == plugins_dir()
    assert get_global_wiki_path() == wiki_dir()


def test_spaces_consumers_route_through_spaces_dir(cfg):
    """The space RESOLUTION path routes through spaces_dir() (single home). `navig space`
    reads config-manager global_config_dir, now itself a LIVE property (see
    test_config_manager_global_config_dir_is_live) — so both sides honour NAVIG_CONFIG_DIR."""
    from navig.platform.paths import spaces_dir
    from navig.spaces.resolver import spaces_roots

    # The global resolution root routes through spaces_dir() (honours NAVIG_CONFIG_DIR).
    assert spaces_roots()[0] == spaces_dir()


def test_config_manager_global_config_dir_is_live(tmp_path, monkeypatch):
    """REGRESSION (#179 class): config.py's ConfigManager.global_config_dir must resolve LIVE,
    never freeze at construction. get_config_manager() CACHES the instance, so a frozen value
    split-brained `navig space` (reads global_config_dir) against the space resolver / deck
    (read paths.config_dir() live). The cached manager must follow NAVIG_CONFIG_DIR applied
    AFTER it was built — and `navig space`'s own dir resolution must follow with it."""
    from navig.commands.space import _spaces_dir
    from navig.config import get_config_manager
    from navig.platform.paths import config_dir, spaces_dir

    cm = get_config_manager()  # the SAME cached manager `navig space` uses
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    assert cm.global_config_dir == config_dir() == tmp_path
    # End-to-end: navig space and the resolver now agree under isolation (the split-brain).
    assert _spaces_dir(create=False) == spaces_dir() == tmp_path / "spaces"


def test_config_singleton_paths_are_live(tmp_path, monkeypatch):
    """REGRESSION (#179 class): the OTHER config system — shared_config.ConfigSingleton
    (navig.core.Config) — must also resolve its paths LIVE, not freeze at construction, so it
    can never diverge from config.py's ConfigManager (#317). Build the singleton FIRST, apply
    isolation AFTER, and assert every derived path followed."""
    from navig.core.shared_config import ConfigSingleton
    from navig.platform.paths import config_dir

    cm = ConfigSingleton()  # build the singleton BEFORE isolation — a frozen attr caches here
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    assert cm.global_config_dir == config_dir() == tmp_path
    assert cm.cache_dir == tmp_path / "cache"
    assert cm.plugins_dir == tmp_path / "plugins"
    assert cm.global_config_path == tmp_path / "config.yaml"


def test_config_manager_active_space_file_is_live_and_global(tmp_path, monkeypatch):
    """REGRESSION: ConfigManager.active_space_file must be GLOBAL and LIVE, never a frozen
    base_dir-relative copy. The active-space pointer lives at config_dir()/cache/active_space.txt
    — the ONE location navig space (_active_space_cache_file) writes and the deck reads. A
    base_dir-relative attr (its old form) would diverge to <project>/.navig/cache/ inside a
    project, split-braining the three readers. Build the cached manager FIRST, isolate AFTER,
    and assert it tracks config_dir() and matches navig space's own path."""
    from navig.commands.space import _active_space_cache_file
    from navig.config import get_config_manager
    from navig.platform.paths import config_dir

    cm = get_config_manager()  # the SAME cached manager the rest of the CLI uses
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    expected = config_dir() / "cache" / "active_space.txt"
    assert cm.active_space_file == expected == tmp_path / "cache" / "active_space.txt"
    # All three active-space sites agree: config manager == navig space's read/write path.
    assert cm.active_space_file == _active_space_cache_file()


def test_active_space_file_stays_global_under_project_base_dir(tmp_path, monkeypatch):
    """An explicit (project-local) base_dir must NOT drag active_space_file project-local.
    hosts/apps ARE base_dir-relative; the active SPACE is global. A ConfigManager whose base_dir
    is a project .navig still points active_space_file at ~/.navig/cache/active_space.txt."""
    from navig.config import ConfigManager
    from navig.platform.paths import config_dir

    global_home = tmp_path / "home"
    project = tmp_path / "project" / ".navig"
    project.mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(global_home))

    cm = ConfigManager(config_dir=project)  # base_dir = project-local .navig
    assert cm.base_dir == project  # project-local, as requested
    # …but the active-space pointer stays GLOBAL, never project/.navig/cache/…
    assert cm.active_space_file == config_dir() / "cache" / "active_space.txt"
    assert cm.active_space_file == global_home / "cache" / "active_space.txt"
    assert cm.active_space_file != project / "cache" / "active_space.txt"


def test_session_store_base_dir(cfg):
    from navig.agent.session_store import _default_base_dir

    assert _default_base_dir() == cfg / "sessions"


def test_soul_file_property(cfg):
    from navig.agent.soul import Soul

    assert Soul.SOUL_FILE.fget(None) == cfg / "workspace" / "SOUL.md"


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def test_work_navig_root_and_db(cfg):
    from navig.commands.work import _db_path, _navig_root

    assert _navig_root() == cfg
    assert _db_path() == cfg / "store" / "work.db"


def test_prompts_dir(cfg):
    from navig.commands.prompts import _prompts_dir

    assert _prompts_dir() == cfg / "store" / "prompts"


def test_tray_lock_file(cfg):
    from navig.commands.tray import _lock_file

    assert _lock_file() == cfg / "tray.lock"


def test_init_default_navig_dir(cfg):
    from navig.commands.init import _default_navig_dir

    assert _default_navig_dir() == cfg


def test_dashboard_state_files(cfg):
    from navig.commands.dashboard import (
        _daemon_pid_file,
        _daemon_state_file,
        _tunnels_file,
    )

    assert _daemon_pid_file() == cfg / "daemon" / "supervisor.pid"
    assert _daemon_state_file() == cfg / "daemon" / "state.json"
    assert _tunnels_file() == cfg / "cache" / "tunnels.json"


def test_onboard_defaults(cfg):
    from navig.commands.onboard import (
        NavigConfig,
        _default_config_file,
        _default_navig_dir,
        _default_workspace_dir,
    )

    assert _default_navig_dir() == cfg
    assert _default_workspace_dir() == cfg / "workspace"
    assert _default_config_file() == cfg / "navig.json"
    # Dataclass field default must be a factory, not a frozen class attribute.
    assert NavigConfig().workspace_root == str(cfg / "workspace")


# ---------------------------------------------------------------------------
# gateway / contracts / providers
# ---------------------------------------------------------------------------


def test_audit_log_default_path(cfg):
    from navig.gateway.audit_log import AuditLog, _default_path

    assert _default_path() == cfg / "runtime" / "audit.jsonl"
    assert AuditLog()._path == cfg / "runtime" / "audit.jsonl"


def test_contracts_store_dir(cfg):
    from navig.contracts.store import _default_store_dir

    assert _default_store_dir() == cfg / "runtime"


def test_bridge_grid_path(cfg):
    from navig.providers.bridge_grid_reader import _grid_path

    assert _grid_path() == cfg / "bridge-grid.json"


def test_deck_apps_wiki_root(cfg):
    aiohttp = pytest.importorskip("aiohttp")  # noqa: F841 — route module needs it
    from navig.gateway.deck.routes.apps import _wiki_root

    assert _wiki_root() == cfg / "wiki"


def test_audio_menu_store_dir(cfg):
    from navig.gateway.channels.audio_menu.state import _store_dir

    assert _store_dir() == cfg / "config" / "audio"


def test_ipc_pipe_paths(cfg):
    from navig.ipc_pipe import _promoted_flag, _shadow_log

    assert _promoted_flag() == cfg / ".ipc_promoted"
    assert _shadow_log() == cfg / "shadow_ipc_anomalies.log"


# ---------------------------------------------------------------------------
# perf / llm / onboarding
# ---------------------------------------------------------------------------


def test_profiler_perf_dir(cfg):
    from navig.perf.profiler import _perf_dir

    assert _perf_dir() == cfg / "perf"


def test_trace_log_path(cfg):
    from navig.llm.routing.trace import _trace_log_path

    assert _trace_log_path() == cfg / "logs" / "router_traces.jsonl"


def test_onboarding_pinged_marker(cfg):
    from navig.onboarding.telemetry import _pinged_marker

    assert _pinged_marker() == cfg / ".pinged"


# ---------------------------------------------------------------------------
# selfheal
# ---------------------------------------------------------------------------


def test_heal_patches_dir(cfg):
    from navig.selfheal.heal_pr_submitter import _heal_patches_dir

    assert _heal_patches_dir() == cfg / "heal_patches"


def test_core_repo_dir(cfg):
    from navig.selfheal.git_manager import core_repo_dir

    assert core_repo_dir() == cfg / "core-repo"


def test_ssh_healer_home_paths(tmp_path, monkeypatch):
    """The healer WRITES to ~/.ssh — a frozen Path.home() constant meant an
    isolated test could append to the operator's real known_hosts."""
    from navig.selfheal.ssh_healer import _default_ssh_key_path, _known_hosts_path

    fake_home = tmp_path / "fake_home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    assert _known_hosts_path() == fake_home / ".ssh" / "known_hosts"
    assert _default_ssh_key_path() == fake_home / ".ssh" / "id_ed25519"


# ---------------------------------------------------------------------------
# workspace family
# ---------------------------------------------------------------------------


def test_workspace_ownership_dirs(cfg):
    from navig.workspace_ownership import user_navig_dir, user_workspace_dir

    assert user_navig_dir() == cfg
    assert user_workspace_dir() == cfg / "workspace"


def test_workspace_default_config_file(cfg):
    from navig.workspace import _default_config_file

    assert _default_config_file() == cfg / "navig.json"


def test_tui_config_model_defaults(cfg):
    from navig.tui.config_model import (
        NavigConfig,
        _default_config_file,
        _default_workspace_dir,
    )

    assert _default_workspace_dir() == cfg / "workspace"
    assert _default_config_file() == cfg / "navig.json"
    assert NavigConfig().workspace_root == str(cfg / "workspace")


def test_cost_tracker_history_path(cfg):
    from navig.cost_tracker import SessionCostTracker

    expected = cfg / "workspace" / "session_costs.jsonl"
    assert SessionCostTracker._history_path_static() == expected


# ---------------------------------------------------------------------------
# daemon family
# ---------------------------------------------------------------------------


def test_supervisor_paths(cfg):
    from navig.daemon.supervisor import _daemon_dir, _pid_file, _state_file

    assert _daemon_dir() == cfg / "daemon"
    assert _pid_file() == cfg / "daemon" / "supervisor.pid"
    assert _state_file() == cfg / "daemon" / "state.json"


def test_supervisor_pid_file_test_seam(cfg, tmp_path):
    import navig.daemon.supervisor as sup

    seam = tmp_path / "test.pid"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sup, "PID_FILE", seam)
        assert sup._pid_file() == seam
    assert sup._pid_file() == cfg / "daemon" / "supervisor.pid"


def test_entry_daemon_config_path(cfg):
    from navig.daemon.entry import _daemon_config_path

    assert _daemon_config_path() == cfg / "daemon" / "config.json"


def test_service_manager_paths(cfg):
    from navig.daemon.service_manager import (
        _log_dir,
        _navig_home,
        _stop_flag_path,
        _watchdog_deadline_path,
        daemon_dir,
    )

    assert _navig_home() == cfg
    assert _log_dir() == cfg / "logs"
    assert daemon_dir() == cfg / "daemon"
    assert _stop_flag_path() == cfg / "daemon" / "stop_requested"
    assert _watchdog_deadline_path() == cfg / "daemon" / "stop_watchdog_deadline"
