"""The four install choices are real: they persist, they agree, and they warn.

An installer answer that nothing reads is decoration. These tests pin the chain from
``INSTALL_PROFILES`` → ``brain.role`` in config → the ownership guard, so "I chose
connect to an existing Brain" cannot end with a daemon starting on that machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.brain import roles
from navig.brain.lease import ROLE_ALL_IN_ONE, ROLE_BRAIN, ROLE_CLIENT, ROLES, brain_settings
from navig.installer.profiles import (
    INSTALL_PROFILES,
    daemon_cli_args,
    resolve_install_profile,
    validate_profile_manifest,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("NAVIG_BRAIN_ROLE", raising=False)
    monkeypatch.delenv("NAVIG_BRAIN_TTL", raising=False)
    monkeypatch.delenv("NAVIG_BRAIN_AUTO_PROMOTE", raising=False)


# ── the four choices ───────────────────────────────────────────────────────


def test_there_are_exactly_four_choices_with_consequences() -> None:
    assert [c["id"] for c in roles.CHOICES] == ["all-in-one", "client", "brain", "migrate"]
    for choice in roles.CHOICES:
        assert choice["role"] in ROLES, choice["id"]
        assert choice["title"], choice["id"]
        # The consequence is the sentence that prevents the misunderstanding.
        assert len(str(choice["consequence"])) > 40, choice["id"]
        assert choice["host_dependency"] in ("workstation", "none", "always-on")
        assert choice["next_step"].startswith("navig "), choice["id"]


def test_each_choice_says_what_it_runs_and_what_it_does_not() -> None:
    for choice in roles.CHOICES:
        assert choice["runs"], choice["id"]
        assert choice["does_not_run"], choice["id"]


def test_the_client_choice_is_the_one_that_is_safe_to_switch_off() -> None:
    client = roles.choice("client")
    assert client is not None
    assert client["host_dependency"] == "none"
    assert "safe to switch off" in client["consequence"]
    assert "daemon" in client["does_not_run"]


def test_a_brain_on_a_workstation_is_warned_about_not_blocked() -> None:
    warning = roles.host_dependency_warning(ROLE_BRAIN, always_on=False)
    assert warning is not None
    assert "powered off" in warning or "workstation" in warning
    assert roles.host_dependency_warning(ROLE_BRAIN, always_on=True) is None
    # A client has no dependency on the host, so it must never be nagged.
    assert roles.host_dependency_warning(ROLE_CLIENT, always_on=False) is None


# ── profiles carry the role, and the role becomes config ───────────────────


def test_every_public_profile_declares_a_role_and_it_matches_its_config() -> None:
    for name, manifest in INSTALL_PROFILES.items():
        assert manifest.get("role") in ROLES, name
        assert manifest["config"]["brain.role"] == manifest["role"], name


def test_profile_role_mapping_is_the_expected_one() -> None:
    assert roles.role_for_profile("cli") == ROLE_CLIENT
    assert roles.role_for_profile("minimal") == ROLE_CLIENT
    assert roles.role_for_profile("server") == ROLE_BRAIN
    assert roles.role_for_profile("full") == ROLE_ALL_IN_ONE
    # An unknown/legacy profile must not silently become a Brain.
    assert roles.role_for_profile("nonsense") == ROLE_ALL_IN_ONE


def test_the_profile_manifests_still_validate() -> None:
    for name in INSTALL_PROFILES:
        manifest = resolve_install_profile(name)
        assert manifest is not None
        assert validate_profile_manifest(name, manifest) == [], name


def test_a_manifest_that_disagrees_with_itself_is_refused() -> None:
    manifest = dict(INSTALL_PROFILES["server"])
    manifest["config"] = {"vault.role": "client", "brain.role": "client"}
    errors = validate_profile_manifest("server", manifest)
    assert any("disagrees" in error for error in errors)


def test_an_unknown_role_is_refused() -> None:
    manifest = dict(INSTALL_PROFILES["server"])
    manifest["role"] = "godmode"
    manifest["config"] = {"brain.role": "godmode"}
    errors = validate_profile_manifest("server", manifest)
    assert any("unknown brain role" in error for error in errors)


def test_the_written_role_is_what_the_guard_reads(tmp_path: Path) -> None:
    """The chain end to end: a ``server`` install's brain.role is ``brain``."""
    from navig.brain import guard
    from navig.brain.lease import LeaseStore

    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text("brain:\n  role: brain\n", encoding="utf-8")
    guard.reset_process_lease()
    assert brain_settings()["role"] == ROLE_BRAIN
    decision = guard.check(
        service="daemon", store=LeaseStore(cfg / "brain" / "lease.json"), acquire=False
    )
    assert decision.allowed is True
    assert decision.role == ROLE_BRAIN


def test_a_cli_install_cannot_run_the_daemon(tmp_path: Path) -> None:
    from navig.brain import guard
    from navig.brain.lease import LeaseStore

    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text("brain:\n  role: client\n", encoding="utf-8")
    guard.reset_process_lease()
    decision = guard.check(
        service="daemon", store=LeaseStore(cfg / "brain" / "lease.json"), acquire=False
    )
    assert decision.allowed is False
    assert "client" in decision.message


# ── daemon flags agree with the role ───────────────────────────────────────


@pytest.mark.parametrize("profile", ["cli", "minimal"])
def test_no_service_profiles_run_no_daemon(profile: str) -> None:
    manifest = resolve_install_profile(profile)
    assert manifest is not None
    assert manifest["daemon"] is None
    assert daemon_cli_args(manifest["daemon"]) == []
    # …and they are clients, so the guard would refuse a singleton anyway.
    assert roles.role_for_profile(profile) == ROLE_CLIENT


def test_the_server_profile_runs_the_bot_and_the_gateway() -> None:
    manifest = resolve_install_profile("server")
    assert manifest is not None
    args = daemon_cli_args(manifest["daemon"])
    assert "--bot" in args and "--gateway" in args


# ── detection ──────────────────────────────────────────────────────────────


def test_detect_install_context_reports_what_is_already_here(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text(
        "install:\n  profile: server\nbrain:\n  role: brain\ndeck:\n  api_key: abc1234567890123\n",
        encoding="utf-8",
    )
    context = roles.detect_install_context(cfg)
    assert context["has_config"] is True
    assert context["role"] == ROLE_BRAIN
    assert context["profile"] == "server"
    assert context["lease"]["state"] == "free"
    assert context["deck_key_fingerprint"]
    assert "abc1234567890123" not in str(context)  # the key itself never surfaces
    assert context["moved"] == {}


def test_detect_install_context_sees_a_moved_marker(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    (cfg / "daemon").mkdir(parents=True, exist_ok=True)
    (cfg / "daemon" / "moved.json").write_text('{"to": "outpost"}', encoding="utf-8")
    context = roles.detect_install_context(cfg)
    assert context["moved"] == {"to": "outpost"}


def test_detect_install_context_on_a_bare_dir_does_not_raise(tmp_path: Path) -> None:
    context = roles.detect_install_context(tmp_path / "nothing-here")
    assert context["has_config"] is False
    assert context["lease"]["state"] == "free"
    assert context["this_owner"].startswith("brn-")
