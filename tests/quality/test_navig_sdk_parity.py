"""Guard: navig-sdk's standalone AI answers the same way navig would.

``navig_sdk`` (registry/sdk/python) is what a plugin uses to reach an AI when navig is NOT
installed. It cannot import navig, so it carries two copies of navig facts, and both rot
silently because they only run standalone:

* the provider → environment-variable table. A name navig honours that the SDK does not
  means "works inside navig, 'No AI is configured' on its own" for the same user;
* the default model per provider. A model catalog rots wholesale (groq lost all eleven ids
  in one audit); a retired default makes every standalone AI call fail with HTTP 400/404.
  navig's retired-model denylist is the offline record of that, so it is checked here.

Loaded from the source tree, not imported from an install: navig-sdk is not a core
dependency, so an import-based guard would skip in exactly the environment it must run in.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from navig.llm.liveness import is_retired
from navig.providers.types import PROVIDER_ENV_VARS
from tests.fixtures.module_eviction import evicted_modules

SDK = Path(__file__).resolve().parents[3] / "registry" / "sdk" / "python"


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch):
    assert (SDK / "navig_sdk" / "ai.py").is_file(), f"{SDK} moved — update this guard"
    monkeypatch.syspath_prepend(str(SDK))
    # This tree's SDK, not an installed copy — through the shared eviction helper, which
    # restores the originals and drops what was imported inside (a raw delitem leaks it).
    with evicted_modules(monkeypatch, ("navig_sdk",)):
        yield importlib.import_module("navig_sdk.ai"), importlib.import_module("navig_sdk.keys")


def test_env_var_names_match_navig(sdk) -> None:
    _, keys = sdk
    assert keys.PROVIDER_ENV_VARS, "the SDK key table is empty — this guard would watch nothing"
    drift = {
        provider: (names, PROVIDER_ENV_VARS.get(provider))
        for provider, names in keys.PROVIDER_ENV_VARS.items()
        if names != PROVIDER_ENV_VARS.get(provider)
    }
    assert not drift, (
        "navig_sdk.keys.PROVIDER_ENV_VARS disagrees with navig.providers.types "
        f"(sdk, navig): {drift}"
    )


def test_every_sdk_provider_has_a_key_table_entry(sdk) -> None:
    ai, keys = sdk
    missing = [name for name, _url, _model in ai.PROVIDERS if name not in keys.PROVIDER_ENV_VARS]
    assert not missing, f"navig_sdk.ai.PROVIDERS names providers with no env-var entry: {missing}"


def test_default_models_are_not_retired(sdk) -> None:
    ai, _ = sdk
    assert ai.PROVIDERS, "navig_sdk.ai.PROVIDERS is empty — this guard would watch nothing"
    retired = [f"{name}:{model}" for name, _url, model in ai.PROVIDERS if is_retired(model, name)]
    assert not retired, (
        f"navig_sdk.ai default models are on navig's retired list: {retired}. "
        "Replace them with a live id (check with `navig ai models --check -p <provider>`)."
    )


def test_coerce_bool_truth_table_matches_navig(monkeypatch: pytest.MonkeyPatch) -> None:
    """navig-sdk's standalone coerce_bool must answer exactly like navig's canonical one."""
    from navig.core.coerce import coerce_bool as navig_coerce

    monkeypatch.syspath_prepend(str(SDK))
    with evicted_modules(monkeypatch, ("navig_sdk",)):
        host = importlib.import_module("navig_sdk.host")
        samples = [True, False, None, 0, 1, 2, 0.0, 1.5, "", " ", "1", "0", "true", "False",
                   "YES", "no", "on", "off", "t", "f", "y", "n", "maybe", "enabled", " On "]
        drift = [
            (v, d, host._coerce_bool_fallback(v, d), navig_coerce(v, default=d))
            for v in samples
            for d in (True, False)
            if host._coerce_bool_fallback(v, d) != navig_coerce(v, default=d)
        ]
    assert not drift, f"(value, default, sdk, navig): {drift}"


def test_modules_navig_sdk_owns_are_aliases_not_copies(monkeypatch: pytest.MonkeyPatch) -> None:
    """navig.net.ssrf and navig.agent.proactive.providers ARE navig-sdk's modules.

    An alias, not a re-export: dotted-path patches (tests patch navig.net.ssrf.resolve_host)
    must reach the one real guard, and there must be exactly one CalendarEvent class.
    """
    monkeypatch.syspath_prepend(str(SDK))
    prefixes = ("navig_sdk", "navig.net.ssrf", "navig.agent.proactive")
    with evicted_modules(monkeypatch, prefixes):
        pairs = [
            ("navig.net.ssrf", "navig_sdk.ssrf"),
            ("navig.agent.proactive.providers", "navig_sdk.pim"),
        ]
        for alias, owner in pairs:
            assert importlib.import_module(alias) is importlib.import_module(owner), alias
        assert importlib.import_module("navig_sdk.ssrf").__file__.startswith(str(SDK))


def test_contact_store_is_the_plugins_module_and_types_are_the_sdks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """navig.store.contacts IS navig_contacts.store; Contact/Route are navig-sdk's classes.

    The store moved into navig-contacts so the address book runs without navig. Messaging,
    the deck and the Telegram channel still import ``navig.store.contacts``: an alias keeps
    the ``get_contact_store()`` singleton shared and dotted-path patches real, and a single
    ``Contact`` class means a contact read standalone is the object routing resolves.
    """
    plugin = SDK.parents[2] / "plugins" / "navig-contacts"
    monkeypatch.syspath_prepend(str(SDK))
    monkeypatch.syspath_prepend(str(plugin))
    prefixes = ("navig_sdk", "navig_contacts", "navig.store.contacts", "navig.messaging.adapter")
    with evicted_modules(monkeypatch, prefixes):
        store = importlib.import_module("navig.store.contacts")
        assert store is importlib.import_module("navig_contacts.store")
        assert store.__file__.startswith(str(plugin))
        adapter = importlib.import_module("navig.messaging.adapter")
        pim = importlib.import_module("navig_sdk.pim")
        assert (adapter.Contact, adapter.Route) == (pim.Contact, pim.Route)
        assert store.Contact is pim.Contact


def test_language_code_matches_navig(monkeypatch: pytest.MonkeyPatch) -> None:
    """navig-sdk's standalone language_code maps every name and code exactly like navig's.

    Speech-to-text on its own (navig-audio) reads it: a name the SDK lacks would send a
    language NAME to a model that accepts only codes, which fails the whole transcription.
    """
    from navig.core import language as navig_language

    monkeypatch.syspath_prepend(str(SDK))
    with evicted_modules(monkeypatch, ("navig_sdk",)):
        lang = importlib.import_module("navig_sdk.lang")
        assert lang._NAME_TO_CODE == navig_language._NAME_TO_CODE
        assert lang._AUTO == navig_language._AUTO
        samples = [*navig_language._NAME_TO_CODE, *navig_language._NAME_TO_CODE.values(),
                   "", " ", "auto", "AUTO", "detect", None, "Russian", " russian ", "RU",
                   "Brazilian Portuguese", "klingon", 7]
        drift = [
            (v, lang._language_code_fallback(v), navig_language.language_code(v))
            for v in samples
            if lang._language_code_fallback(v) != navig_language.language_code(v)
        ]
    assert not drift, f"(value, sdk, navig): {drift}"


def test_settings_by_path_written_standalone_is_what_navig_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A page id navig-social saves on its own is the one navig reads after install, and back.

    Both directions go through the real config.yaml in an isolated config dir: the SDK's
    standalone branch (navig's ConfigManager hidden from it) and navig's own ConfigManager.
    """
    from navig.config import ConfigManager

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.syspath_prepend(str(SDK))
    with evicted_modules(monkeypatch, ("navig_sdk",)):
        settings = importlib.import_module("navig_sdk.settings")
        monkeypatch.setattr(settings, "_manager", lambda: None)
        settings.set_path("adapters.social.facebook.page_id", "p-123")
        cm = ConfigManager(config_dir=tmp_path)
        assert cm.get("adapters.social.facebook.page_id") == "p-123"
        cm.set_global("adapters.social.reddit.subreddit", "r-navig")
        assert settings.get_path("adapters.social.reddit.subreddit") == "r-navig"
        assert settings.get_path("adapters.social.facebook.page_id") == "p-123"
