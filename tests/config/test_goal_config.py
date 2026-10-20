"""The `goal:` config block — shipped defaults the session-goal loop reads.

The loop's knobs live in `core/config/defaults.yaml` next to `missions:`/`verifier:`
(same pattern: no GlobalConfig field — extra top-level sections pass validation
untouched). These tests pin the shipped values and the string-safety of every read:
`navig config set goal.enabled false` stores the four characters "false", and the
reader must end up False (the coerce_bool trap this repo keeps regressing on).

Isolation note: `navig.config.get` reads `ConfigSingleton`, whose frozen
`_global_cache_path` outlives a NAVIG_CONFIG_DIR change — every test here resets BOTH
singletons (the leak that made an absent-config test inherit the previous test's
`"false"`), mirroring `tests/checkins/conftest.py::ci_env`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

DEFAULTS = Path(__file__).resolve().parents[2] / "config" / "defaults.yaml"


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg_dir))
    reset_config_manager()
    monkeypatch.setattr(ConfigSingleton, "_instance", None, raising=False)
    yield cfg_dir
    reset_config_manager()
    ConfigSingleton._instance = None


def _goal_block() -> dict:
    data = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    goal = data.get("goal")
    assert isinstance(goal, dict), "goal: block missing from defaults.yaml"
    return goal


def test_shipped_defaults_are_the_documented_values():
    goal = _goal_block()
    assert goal["enabled"] is True
    assert goal["max_rounds"] == 12
    assert goal["blocked_after_rounds"] == 3
    assert goal["tool_policy"] == "model"


def test_goal_block_is_not_missions_and_not_verifier():
    # Namespace hygiene: session goals must not silently ride another block.
    data = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    assert "goal" in data
    assert data["goal"] is not data.get("missions")


def test_enabled_read_survives_the_string_false_trap(isolated_config):
    # Exactly what `navig config set goal.enabled false` writes: the string "false".
    (isolated_config / "config.yaml").write_text(
        'goal:\n  enabled: "false"\n', encoding="utf-8"
    )
    from navig import config as cfg
    from navig.core.coerce import coerce_bool, coerce_int

    assert coerce_bool(cfg.get("goal.enabled"), default=True) is False
    # Unset knob falls back to the shipped default, not to 0/None.
    assert coerce_int(cfg.get("goal.max_rounds"), default=12, minimum=1) == 12
    assert cfg.get("goal.blocked_after_rounds") is None


def test_defaults_read_when_config_file_absent(isolated_config, tmp_path, monkeypatch):
    # Point at a directory with no config.yaml at all — the degraded read path.
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "does-not-exist"))
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    reset_config_manager()
    ConfigSingleton._instance = None
    from navig import config as cfg
    from navig.core.coerce import coerce_bool

    # Degraded read must fail to the shipped default ON (loop works standalone).
    assert coerce_bool(cfg.get("goal.enabled"), default=True) is True
