from navig.providers.auth import AuthProfileManager
import pytest

pytestmark = pytest.mark.integration


def test_get_api_key_profile_id_must_match_provider(tmp_path):
    manager = AuthProfileManager(config_dir=tmp_path)
    manager.add_api_key(provider="anthropic", api_key="anth-key", profile_id="shared")

    result = manager.get_api_key(provider="openai", profile_id="shared")

    assert result is None


def test_get_api_key_profile_id_matching_provider_returns_key(tmp_path):
    manager = AuthProfileManager(config_dir=tmp_path)
    manager.add_api_key(provider="openai", api_key="openai-key", profile_id="shared")

    result = manager.get_api_key(provider="openai", profile_id="shared")

    assert result == "openai-key"
