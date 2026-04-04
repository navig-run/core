from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_detect_provider_sources_reads_env_and_config(tmp_path: Path, monkeypatch):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)
    (navig_dir / "config.yaml").write_text("openrouter_api_key: sk-or-test\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    from navig.providers.source_scan import detect_provider_sources

    openai_sources = detect_provider_sources("openai", navig_dir=navig_dir)
    openrouter_sources = detect_provider_sources("openrouter", navig_dir=navig_dir)

    assert "env" in openai_sources
    assert "config" in openrouter_sources


def test_scan_enabled_provider_sources_uses_registry(monkeypatch, tmp_path: Path):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    providers = [SimpleNamespace(id="openai"), SimpleNamespace(id="llamacpp")]
    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: providers)

    from navig.providers.source_scan import scan_enabled_provider_sources

    detected = scan_enabled_provider_sources(navig_dir=navig_dir, cfg={})

    assert "openai" in detected
    assert "env" in detected["openai"]
