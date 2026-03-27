"""
Tests for PROVIDER_RESOURCE_URLS centralization (Phase 2).

Verifies that:
- PROVIDER_RESOURCE_URLS is importable and well-structured
- Each expected provider/key pair resolves to a real URL
- Consumer modules import PROVIDER_RESOURCE_URLS (canonical source of truth)
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]


# ---------------------------------------------------------------------------
# Constant availability
# ---------------------------------------------------------------------------


def test_provider_resource_urls_importable():
    from navig.llm_router import PROVIDER_RESOURCE_URLS

    assert isinstance(PROVIDER_RESOURCE_URLS, dict)
    assert len(PROVIDER_RESOURCE_URLS) > 0


def test_provider_resource_urls_expected_keys():
    from navig.llm_router import PROVIDER_RESOURCE_URLS

    expected = {
        "openai",
        "deepgram",
        "elevenlabs",
        "google_tts",
        "spotify",
        "lastfm",
        "audd",
        "serpapi",
    }
    missing = expected - PROVIDER_RESOURCE_URLS.keys()
    assert not missing, f"Missing top-level keys: {missing}"


@pytest.mark.parametrize(
    "provider,key,fragment",
    [
        ("openai", "transcriptions", "audio/transcriptions"),
        ("openai", "speech", "audio/speech"),
        ("openai", "chat", "chat/completions"),
        ("deepgram", "listen", "deepgram.com"),
        ("deepgram", "speak", "deepgram.com"),
        ("elevenlabs", "tts_base", "elevenlabs.io"),
        ("google_tts", "synthesize", "texttospeech.googleapis.com"),
        ("spotify", "token", "accounts.spotify.com"),
        ("spotify", "search", "api.spotify.com"),
        ("lastfm", "base", "audioscrobbler.com"),
        ("audd", "base", "audd.io"),
        ("serpapi", "search", "serpapi.com"),
    ],
)
def test_resource_url_contains_expected_fragment(provider, key, fragment):
    from navig.llm_router import PROVIDER_RESOURCE_URLS

    url = PROVIDER_RESOURCE_URLS[provider][key]
    assert fragment in url, (
        f"PROVIDER_RESOURCE_URLS[{provider!r}][{key!r}] = {url!r} "
        f"does not contain expected fragment {fragment!r}"
    )


def test_all_resource_urls_are_https_or_http():
    from navig.llm_router import PROVIDER_RESOURCE_URLS

    for provider, sub in PROVIDER_RESOURCE_URLS.items():
        for key, url in sub.items():
            assert url.startswith(("https://", "http://")), (
                f"PROVIDER_RESOURCE_URLS[{provider!r}][{key!r}] "
                f"does not start with https:// or http://: {url!r}"
            )


# ---------------------------------------------------------------------------
# Consumer modules import PROVIDER_RESOURCE_URLS (canonical source of truth)
# ---------------------------------------------------------------------------

_CONSUMER_FILES = [
    REPO_ROOT / "navig/gateway/channels/media_engine/audio.py",
    REPO_ROOT / "navig/gateway/channels/media_engine/image.py",
    REPO_ROOT / "navig/voice/tts.py",
    REPO_ROOT / "navig/voice/stt.py",
]

_IMPORT_SOURCE = "navig.llm_router"
_IMPORT_NAME = "PROVIDER_RESOURCE_URLS"


def _source_imports_prul(path: Path) -> bool:
    """Return True if the file imports PROVIDER_RESOURCE_URLS from navig.llm_router."""
    source = path.read_text(encoding="utf-8-sig")  # strips BOM if present
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == _IMPORT_SOURCE:
                for alias in node.names:
                    if alias.name == _IMPORT_NAME:
                        return True
    return False


@pytest.mark.parametrize(
    "consumer", _CONSUMER_FILES, ids=[p.name for p in _CONSUMER_FILES]
)
def test_consumer_imports_provider_resource_urls(consumer):
    """Each consumer file must import PROVIDER_RESOURCE_URLS from navig.llm_router."""
    if not consumer.exists():
        pytest.skip(f"{consumer} not found")
    assert _source_imports_prul(consumer), (
        f"{consumer.name} does not import PROVIDER_RESOURCE_URLS from {_IMPORT_SOURCE}. "
        "Add: from navig.llm_router import PROVIDER_RESOURCE_URLS as _PRUL"
    )
