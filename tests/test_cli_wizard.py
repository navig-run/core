from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.cli.wizard import SetupWizard, install_daemon, run_wizard


@pytest.fixture
def mock_print():
    with patch("builtins.print") as mk_print:
        yield mk_print


@pytest.fixture
def mock_input():
    with patch("builtins.input") as mk_input:
        yield mk_input


@pytest.fixture
def wizard():
    # Use reconfigure=True so it covers more path branches
    wiz = SetupWizard(reconfigure=True)
    wiz.navig_dir = Path("/tmp/mock_navig_dir")
    wiz.config_file = wiz.navig_dir / "config.yaml"
    return wiz


from collections import namedtuple

VersionInfo = namedtuple("VersionInfo", ["major", "minor", "micro"])


def test_wizard_check_prereqs(wizard, mock_print):
    with (
        patch("sys.version_info", VersionInfo(3, 10, 0)),
        patch.object(wizard, "_check_command", return_value=True),
    ):
        assert wizard._check_prerequisites() is True

    with (
        patch("sys.version_info", VersionInfo(3, 9, 0)),
        patch.object(wizard, "_check_command", return_value=False),
    ):
        assert wizard._check_prerequisites() is False


def test_wizard_check_command(wizard):
    with patch("subprocess.run") as mock_run:
        assert wizard._check_command("ssh") is True
        mock_run.assert_called_once()

    with patch("subprocess.run", side_effect=Exception):
        assert wizard._check_command("missing") is False


def test_wizard_setup_ai_provider_skip(wizard, mock_input, mock_print):
    # Skip
    mock_input.return_value = "5"
    with patch("navig.cli.wizard.HAS_QUESTIONARY", False):
        wizard._setup_ai_provider()

    assert "ai" not in wizard.config


def test_wizard_setup_ai_provider_openai(wizard, mock_input, mock_print):
    mock_input.side_effect = ["2", "sk-1234"]
    with (
        patch("navig.cli.wizard.HAS_QUESTIONARY", False),
        patch.object(wizard, "_confirm", return_value=True),
        patch.object(wizard, "_test_ai_connection", return_value=True),
        patch("pathlib.Path.mkdir"),
        patch("builtins.open"),
    ):
        wizard._setup_ai_provider()

    assert wizard.config["ai"]["default_provider"] == "openai"
    assert wizard.config["ai"]["openai_api_key"] == "${OPENAI_API_KEY}"


def test_wizard_setup_ai_provider_ollama(wizard, mock_input, mock_print):
    mock_input.side_effect = ["4", "http://localhost:11434"]
    with patch("navig.cli.wizard.HAS_QUESTIONARY", False):
        wizard._setup_ai_provider()

    assert wizard.config["ai"]["default_provider"] == "ollama"
    assert wizard.config["ai"]["ollama_host"] == "http://localhost:11434"


def test_wizard_test_ai_connection(wizard):
    class MockResp:
        status_code = 200

    with patch("httpx.get", return_value=MockResp()):
        assert wizard._test_ai_connection("openai", "key") is True
        assert wizard._test_ai_connection("openrouter", "key") is True

    with patch("httpx.get", side_effect=Exception):
        assert wizard._test_ai_connection("openai", "key") is False


def test_wizard_setup_ssh_existing(wizard, mock_print):
    with patch("pathlib.Path.exists", return_value=True):
        wizard._setup_ssh()
    assert "ssh" in wizard.config


def test_wizard_setup_ssh_new(wizard, mock_print):
    with (
        patch("pathlib.Path.exists", return_value=False),
        patch.object(wizard, "_confirm", return_value=True),
        patch("pathlib.Path.mkdir"),
        patch("subprocess.run"),
    ):
        wizard._setup_ssh()
    assert "ssh" in wizard.config


def test_wizard_setup_telegram(wizard, mock_input, mock_print):
    # Confirm setup, valid token, valid user ID
    mock_input.side_effect = ["123:ABC", "111,222"]
    with (
        patch.object(wizard, "_confirm", return_value=True),
        patch("navig.cli.wizard.HAS_QUESTIONARY", False),
        patch.object(wizard, "_test_telegram_token", return_value=True),
        patch("pathlib.Path.mkdir"),
        patch("builtins.open"),
    ):
        wizard._setup_telegram()

    assert wizard.config["telegram"]["allowed_users"] == [111, 222]


def test_wizard_setup_hosts(wizard, mock_input, mock_print):
    with patch.object(
        wizard, "_confirm", side_effect=[True, False]
    ):  # Yes to add, no to another
        mock_input.side_effect = ["myhost", "10.0.0.1", "root", "22"]
        wizard._setup_hosts()

    assert len(wizard.config["hosts"]) == 1
    assert wizard.config["hosts"][0]["name"] == "myhost"


def test_wizard_save_config(wizard, mock_print):
    with (
        patch("pathlib.Path.mkdir"),
        patch("yaml.safe_load", return_value={"existing": True}),
        patch("pathlib.Path.exists", return_value=True),
        patch("builtins.open"),
        patch("os.chmod"),
    ):
        wizard.config = {"new": True}
        wizard._save_config()
        # It merges because reconfigure=True
        assert wizard.config == {"existing": True, "new": True, "version": "1.0"}


def test_install_daemon_linux():
    with (
        patch("platform.system", return_value="Linux"),
        patch("os.geteuid", create=True, return_value=1),
        patch("subprocess.run") as mock_run,
    ):
        assert install_daemon("auto") is True
        mock_run.assert_called()


def test_install_daemon_darwin():
    with (
        patch("platform.system", return_value="Darwin"),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
        patch("subprocess.run") as mock_run,
    ):
        assert install_daemon("auto") is True
        mock_run.assert_called_once()


def test_install_daemon_unsupported():
    with patch("platform.system", return_value="Windows"):
        assert install_daemon("auto") is False


def test_run_wizard():
    with (
        patch("navig.cli.wizard.SetupWizard") as mock_class,
        patch("navig.cli.wizard.install_daemon") as mock_install,
    ):
        mock_instance = MagicMock()
        mock_instance.run.return_value = True
        mock_class.return_value = mock_instance

        res = run_wizard(reconfigure=False, install_daemon_flag=True)
        assert res is True
        mock_install.assert_called_once()


def test_wizard_run_flow(wizard, mock_print):
    with (
        patch.object(wizard, "_check_prerequisites", return_value=True),
        patch.object(wizard, "_setup_ai_provider"),
        patch.object(wizard, "_setup_ssh"),
        patch.object(wizard, "_setup_telegram"),
        patch.object(wizard, "_setup_hosts"),
        patch.object(wizard, "_save_config"),
    ):
        assert wizard.run() is True

    # KeyboardInterrupt propagation catch testing
    with patch.object(wizard, "_check_prerequisites", side_effect=KeyboardInterrupt):
        assert wizard.run() is False

    with patch.object(wizard, "_check_prerequisites", side_effect=Exception("Failed")):
        assert wizard.run() is False
