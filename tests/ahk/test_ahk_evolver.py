from unittest.mock import MagicMock, patch

import pytest

from navig.adapters.automation.evolution.evolver import Evolver

pytestmark = pytest.mark.integration


@pytest.fixture
def evolver():
    with (
        patch("navig.adapters.automation.evolution.evolver.AHKAdapter") as mock_adapter,
        patch("navig.adapters.automation.evolution.evolver.AHKAIGenerator") as mock_gen,
        patch("navig.adapters.automation.evolution.evolver.ScriptLibrary") as mock_lib,
    ):
        ev = Evolver()
        return ev


def test_evolve_existing_script_dry_run(evolver):
    mock_script = MagicMock()
    mock_script.success_count = 5
    mock_script.script = "MsgBox, Test"
    mock_script.id = "123"

    evolver.library.find_script.return_value = mock_script

    res = evolver.evolve("test goal", dry_run=True)
    assert res.success is True
    assert res.script_id == "123"


def test_evolve_existing_script_exec_success(evolver):
    mock_script = MagicMock()
    mock_script.success_count = 5
    mock_script.script = "MsgBox, Test"
    mock_script.id = "123"

    evolver.library.find_script.return_value = mock_script
    evolver.adapter.execute.return_value = MagicMock(success=True)

    res = evolver.evolve("test goal")
    assert res.success is True
    assert res.script_id == "123"
    evolver.library.record_usage.assert_called_once_with("123", True)


def test_evolve_existing_script_exec_fail_fallback(evolver):
    mock_script = MagicMock()
    mock_script.script = "MsgBox, Test"
    evolver.library.find_script.return_value = mock_script

    # First execution from library fails, triggers regeneration
    # Then regen succeeds
    exec_res1 = MagicMock(success=False, stderr="Lib failed")
    exec_res2 = MagicMock(success=True)
    evolver.adapter.execute.side_effect = [exec_res1, exec_res2]

    evolver.adapter.get_all_windows.return_value = []
    evolver.adapter.get_screen_size.return_value = (1920, 1080)

    gen_res = MagicMock(success=True, script="new script")
    evolver.generator.generate.return_value = gen_res
    evolver.library.save_script.return_value = "new_id"

    res = evolver.evolve("test goal")
    assert res.success is True
    assert res.script_id == "new_id"
    assert "new script" in res.history


def test_evolve_generation_failure(evolver):
    evolver.library.find_script.return_value = None
    evolver.adapter.get_all_windows.return_value = []
    evolver.adapter.get_screen_size.return_value = (1920, 1080)

    evolver.generator.generate.return_value = MagicMock(success=False, error="Gen fail")

    res = evolver.evolve("test goal")
    assert res.success is False


def test_evolve_dry_run_new_script(evolver):
    evolver.library.find_script.return_value = None
    evolver.adapter.get_all_windows.return_value = []
    evolver.adapter.get_screen_size.return_value = (1920, 1080)

    evolver.generator.generate.return_value = MagicMock(success=True, script="new script")

    res = evolver.evolve("test goal", dry_run=True)
    assert res.success is True
    assert res.final_script == "new script"


def test_evolve_retry_logic(evolver):
    evolver.library.find_script.return_value = None
    evolver.adapter.get_all_windows.return_value = []
    evolver.adapter.get_screen_size.return_value = (1920, 1080)
    evolver.max_retries = 2

    evolver.generator.generate.side_effect = [
        MagicMock(success=True, script="script1"),
        MagicMock(success=True, script="script2"),
    ]

    # First attempt fails execution, second hits max retries
    evolver.adapter.execute.side_effect = [
        MagicMock(success=False, stderr="err1"),
        MagicMock(success=False, stderr="err2"),
    ]

    res = evolver.evolve("test goal")
    assert res.success is False
    assert res.attempts == 2
    assert "script1" in res.history
    assert "script2" in res.history


def test_evolve_retry_success_on_second(evolver):
    evolver.library.find_script.return_value = None
    evolver.adapter.get_all_windows.return_value = []
    evolver.adapter.get_screen_size.return_value = (1920, 1080)

    evolver.generator.generate.side_effect = [
        MagicMock(success=True, script="script1"),
        MagicMock(success=True, script="script2"),
    ]

    evolver.adapter.execute.side_effect = [
        MagicMock(success=False, stderr="err1"),
        MagicMock(success=True),
    ]
    evolver.library.save_script.return_value = "789"

    res = evolver.evolve("test goal")
    assert res.success is True
    assert res.script_id == "789"
    assert res.attempts == 2
