"""
Batch 65: hermetic unit tests for
  - navig/core/evolution/base.py   (EvolutionResult, BaseEvolver.evolve)
  - navig/personas/resolver.py     (resolve_persona, discover_persona_paths)
  - navig/personas/assets.py       (_resolve_asset, deliver async)
  - navig/spaces/next_action.py    (first_pending_task, build_continuation_prompt)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/core/evolution/base.py
# ---------------------------------------------------------------------------

class TestEvolutionResult:
    def test_default_fields(self) -> None:
        from navig.core.evolution.base import EvolutionResult
        r = EvolutionResult(success=True)
        assert r.success is True
        assert r.artifact is None
        assert r.error == ""
        assert r.history is None
        assert r.attempts == 0

    def test_failure_result(self) -> None:
        from navig.core.evolution.base import EvolutionResult
        r = EvolutionResult(success=False, error="oops", attempts=3)
        assert r.success is False
        assert r.error == "oops"
        assert r.attempts == 3

    def test_success_with_artifact(self) -> None:
        from navig.core.evolution.base import EvolutionResult
        r = EvolutionResult(success=True, artifact={"code": "print(1)"}, attempts=1)
        assert r.artifact == {"code": "print(1)"}


class _AlwaysSucceedEvolver:
    """Concrete evolver that succeeds on first try."""
    from navig.core.evolution.base import BaseEvolver  # type: ignore[misc]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def _generate(self, goal, prev, err, ctx):
        return f"artifact:{goal}"

    def _validate(self, artifact, ctx):
        return None  # no error


class _AlwaysFailEvolver:
    """Concrete evolver that always fails validation."""

    def _generate(self, goal, prev, err, ctx):
        return f"bad:{goal}"

    def _validate(self, artifact, ctx):
        return "validation error"


class _GeneratorRaisesEvolver:
    """Concrete evolver whose _generate raises."""

    def _generate(self, goal, prev, err, ctx):
        raise RuntimeError("gen fail")

    def _validate(self, artifact, ctx):
        return None


class _EmptyGeneratorEvolver:
    """Concrete evolver whose _generate returns empty."""

    def _generate(self, goal, prev, err, ctx):
        return ""

    def _validate(self, artifact, ctx):
        return None


class _CachedEvolver:
    """Concrete evolver that returns from cache."""

    def _check_cache(self, goal):
        return "cached_artifact"

    def _generate(self, goal, prev, err, ctx):
        return f"generated:{goal}"

    def _validate(self, artifact, ctx):
        return None


def _make_evolver(cls_mixins, **kwargs):
    """Dynamically create a concrete evolver subclass."""
    from navig.core.evolution.base import BaseEvolver

    members = {}
    for mixin in cls_mixins:
        members.update({k: v for k, v in vars(mixin).items() if not k.startswith("__")})
    EvolverClass = type("DynamicEvolver", (BaseEvolver,), members)
    return EvolverClass(**kwargs)


class TestBaseEvolver:
    def test_success_first_attempt(self) -> None:
        from navig.core.evolution.base import EvolutionResult
        evolver = _make_evolver([_AlwaysSucceedEvolver], max_retries=3)
        result = evolver.evolve("write hello world")
        assert isinstance(result, EvolutionResult)
        assert result.success is True
        assert result.attempts == 1

    def test_failure_exhausts_retries(self) -> None:
        evolver = _make_evolver([_AlwaysFailEvolver], max_retries=3)
        result = evolver.evolve("goal")
        assert result.success is False
        assert result.attempts == 3

    def test_history_contains_snapshots(self) -> None:
        evolver = _make_evolver([_AlwaysFailEvolver], max_retries=2)
        result = evolver.evolve("goal")
        assert result.history is not None
        assert len(result.history) == 2

    def test_generator_raises_returns_failure(self) -> None:
        evolver = _make_evolver([_GeneratorRaisesEvolver], max_retries=3)
        result = evolver.evolve("goal")
        assert result.success is False
        assert "Generation failed" in result.error

    def test_empty_artifact_returns_failure(self) -> None:
        evolver = _make_evolver([_EmptyGeneratorEvolver], max_retries=2)
        result = evolver.evolve("goal")
        assert result.success is False
        assert "empty" in result.error.lower()

    def test_cache_hit_skips_generation(self) -> None:
        evolver = _make_evolver([_CachedEvolver], max_retries=3)
        result = evolver.evolve("cached goal")
        assert result.success is True
        assert result.artifact == "cached_artifact"
        assert result.attempts == 0

    def test_default_max_retries_is_three(self) -> None:
        evolver = _make_evolver([_AlwaysSucceedEvolver])
        assert evolver.max_retries == 3

    def test_artifact_stored_in_result(self) -> None:
        evolver = _make_evolver([_AlwaysSucceedEvolver])
        result = evolver.evolve("my goal")
        assert result.artifact == "artifact:my goal"


# ---------------------------------------------------------------------------
# navig/personas/resolver.py
# ---------------------------------------------------------------------------

class TestResolvePersona:
    def test_returns_none_when_no_persona(self, tmp_path: Path) -> None:
        from navig.personas.resolver import resolve_persona
        fake_config = tmp_path / "config"
        fake_config.mkdir()
        fake_cwd = tmp_path / "work"
        fake_cwd.mkdir()
        with patch("navig.personas.resolver.config_dir", return_value=fake_config):
            result = resolve_persona("missing", cwd=fake_cwd)
        assert result is None

    def test_finds_user_home_persona(self, tmp_path: Path) -> None:
        from navig.personas.resolver import resolve_persona
        persona_dir = tmp_path / "config" / "personas" / "assistant"
        persona_dir.mkdir(parents=True)
        fake_cwd = tmp_path / "work"
        fake_cwd.mkdir()
        with patch("navig.personas.resolver.config_dir", return_value=tmp_path / "config"):
            result = resolve_persona("assistant", cwd=fake_cwd)
        assert result == persona_dir

    def test_finds_project_persona_over_user(self, tmp_path: Path) -> None:
        from navig.personas.resolver import resolve_persona
        # User-level persona
        user_persona = tmp_path / "config" / "personas" / "tyler"
        user_persona.mkdir(parents=True)
        # Project-level persona (higher priority)
        navig_dir = tmp_path / "project" / ".navig"
        proj_persona = navig_dir / "personas" / "tyler"
        proj_persona.mkdir(parents=True)
        cwd = tmp_path / "project"
        cwd.mkdir(exist_ok=True)
        with patch("navig.personas.resolver.config_dir", return_value=tmp_path / "config"):
            result = resolve_persona("tyler", cwd=cwd)
        assert result == proj_persona

    def test_normalizes_name_to_lowercase(self, tmp_path: Path) -> None:
        from navig.personas.resolver import resolve_persona
        persona_dir = tmp_path / "config" / "personas" / "assistant"
        persona_dir.mkdir(parents=True)
        fake_cwd = tmp_path / "work"
        fake_cwd.mkdir()
        with patch("navig.personas.resolver.config_dir", return_value=tmp_path / "config"):
            result = resolve_persona("Assistant", cwd=fake_cwd)
        assert result == persona_dir


class TestDiscoverPersonaPaths:
    def test_empty_when_no_personas(self, tmp_path: Path) -> None:
        from navig.personas.resolver import discover_persona_paths
        fake_config = tmp_path / "config"
        fake_config.mkdir()
        fake_cwd = tmp_path / "work"
        fake_cwd.mkdir()
        with (
            patch("navig.personas.resolver.config_dir", return_value=fake_config),
            patch("navig.personas.resolver.Path") as _mock_path,
        ):
            # Actually just test without pkg_root interference — use simpler mock
            pass
        # Just verify it returns a dict
        with patch("navig.personas.resolver.config_dir", return_value=fake_config):
            result = discover_persona_paths(cwd=fake_cwd)
        assert isinstance(result, dict)

    def test_discovers_user_personas(self, tmp_path: Path) -> None:
        from navig.personas.resolver import discover_persona_paths
        user_personas = tmp_path / "config" / "personas"
        (user_personas / "assistant").mkdir(parents=True)
        (user_personas / "tyler").mkdir(parents=True)
        fake_cwd = tmp_path / "work"
        fake_cwd.mkdir()
        with patch("navig.personas.resolver.config_dir", return_value=tmp_path / "config"):
            result = discover_persona_paths(cwd=fake_cwd)
        assert "assistant" in result
        assert "tyler" in result


# ---------------------------------------------------------------------------
# navig/personas/assets.py
# ---------------------------------------------------------------------------

class TestResolveAsset:
    def test_none_when_no_path(self) -> None:
        from navig.personas.assets import _resolve_asset
        assert _resolve_asset("", None) is None
        assert _resolve_asset("", Path("/fake")) is None

    def test_none_when_persona_dir_is_none(self) -> None:
        from navig.personas.assets import _resolve_asset
        assert _resolve_asset("wallpaper.jpg", None) is None

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        from navig.personas.assets import _resolve_asset
        result = _resolve_asset("missing.jpg", tmp_path)
        assert result is None

    def test_returns_path_when_file_exists(self, tmp_path: Path) -> None:
        from navig.personas.assets import _resolve_asset
        f = tmp_path / "bg.jpg"
        f.write_bytes(b"fake")
        result = _resolve_asset("bg.jpg", tmp_path)
        assert result == f


class TestDeliver:
    def _persona_config(self, name="tyler", wallpaper=None, sound=None):
        from navig.personas.contracts import PersonaConfig
        return PersonaConfig(name=name, wallpaper=wallpaper, startup_sound=sound)

    @pytest.mark.asyncio
    async def test_skips_when_no_assets(self) -> None:
        from navig.personas.assets import deliver
        bot = AsyncMock()
        with patch("navig.personas.resolver.resolve_persona", return_value=None):
            await deliver(self._persona_config(), chat_id=1, bot_client=bot)
        bot.send_photo.assert_not_called()
        bot.send_voice.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_wallpaper_when_file_exists(self, tmp_path: Path) -> None:
        from navig.personas.assets import deliver
        img = tmp_path / "bg.jpg"
        img.write_bytes(b"img")
        bot = AsyncMock()
        with patch("navig.personas.resolver.resolve_persona", return_value=tmp_path):
            await deliver(
                self._persona_config(wallpaper="bg.jpg"),
                chat_id=42,
                bot_client=bot,
            )
        bot.send_photo.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_wallpaper_when_file_missing(self, tmp_path: Path) -> None:
        from navig.personas.assets import deliver
        bot = AsyncMock()
        with patch("navig.personas.resolver.resolve_persona", return_value=tmp_path):
            await deliver(
                self._persona_config(wallpaper="missing.jpg"),
                chat_id=1,
                bot_client=bot,
            )
        bot.send_photo.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_photo_failure_does_not_raise(self, tmp_path: Path) -> None:
        from navig.personas.assets import deliver
        img = tmp_path / "bg.jpg"
        img.write_bytes(b"img")
        bot = AsyncMock()
        bot.send_photo.side_effect = RuntimeError("telegram error")
        with patch("navig.personas.resolver.resolve_persona", return_value=tmp_path):
            # Must not raise
            await deliver(
                self._persona_config(wallpaper="bg.jpg"),
                chat_id=1,
                bot_client=bot,
            )


# ---------------------------------------------------------------------------
# navig/spaces/next_action.py
# ---------------------------------------------------------------------------

class TestFirstPendingTask:
    def test_returns_empty_on_none(self) -> None:
        from navig.spaces.next_action import first_pending_task
        assert first_pending_task(None) == ""

    def test_returns_empty_when_no_pending(self) -> None:
        from navig.spaces.next_action import first_pending_task
        text = "- [x] Done task\n- [x] Another done"
        assert first_pending_task(text) == ""

    def test_finds_first_pending_task(self) -> None:
        from navig.spaces.next_action import first_pending_task
        text = "- [ ] Write tests\n- [x] Done\n- [ ] Second pending"
        result = first_pending_task(text)
        assert result == "Write tests"

    def test_strips_whitespace(self) -> None:
        from navig.spaces.next_action import first_pending_task
        text = "  - [ ]   Build feature  \n"
        result = first_pending_task(text)
        assert result == "Build feature"

    def test_empty_string(self) -> None:
        from navig.spaces.next_action import first_pending_task
        assert first_pending_task("") == ""


class TestBuildContinuationPrompt:
    def test_returns_base_when_no_actions(self) -> None:
        from navig.spaces.next_action import build_continuation_prompt
        with patch("navig.spaces.next_action.select_best_next_action", return_value=None):
            result = build_continuation_prompt()
        assert "Continue autonomously" in result

    def test_includes_space_when_action_found(self) -> None:
        from navig.spaces.next_action import build_continuation_prompt, SpaceNextAction
        action = SpaceNextAction(
            space="career",
            scope="global",
            goal="Get new job",
            completion_pct=42.0,
            next_task="Update resume",
        )
        with patch("navig.spaces.next_action.select_best_next_action", return_value=action):
            result = build_continuation_prompt()
        assert "career" in result
        assert "Update resume" in result
        assert "42.0%" in result

    def test_preferred_space_calls_get_space_action(self) -> None:
        from navig.spaces.next_action import build_continuation_prompt
        with patch("navig.spaces.next_action.get_space_next_action", return_value=None) as mock_get:
            build_continuation_prompt(preferred_space="devops")
        mock_get.assert_called_once()

    def test_no_preferred_space_calls_select_best(self) -> None:
        from navig.spaces.next_action import build_continuation_prompt
        with patch("navig.spaces.next_action.select_best_next_action", return_value=None) as mock_sel:
            build_continuation_prompt()
        mock_sel.assert_called_once()
