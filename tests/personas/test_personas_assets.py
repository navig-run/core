"""Tests for navig.personas.assets — _resolve_asset and deliver."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.personas.assets import _resolve_asset, deliver


class TestResolveAsset:
    def test_returns_none_for_empty_path(self) -> None:
        assert _resolve_asset("", Path("/some/dir")) is None

    def test_returns_none_for_none_persona_dir(self) -> None:
        assert _resolve_asset("wallpaper.jpg", None) is None

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        result = _resolve_asset("missing.jpg", tmp_path)
        assert result is None

    def test_returns_path_when_file_exists(self, tmp_path: Path) -> None:
        f = tmp_path / "wall.jpg"
        f.write_bytes(b"img")
        result = _resolve_asset("wall.jpg", tmp_path)
        assert result == f

    def test_nested_path(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "sound.ogg"
        f.write_bytes(b"audio")
        result = _resolve_asset("sub/sound.ogg", tmp_path)
        assert result == f


class TestDeliver:
    @pytest.mark.asyncio
    async def test_no_assets_does_not_raise(self) -> None:
        config = MagicMock()
        config.name = "techie"
        config.wallpaper = None
        config.startup_sound = None
        bot = AsyncMock()
        await deliver(config, chat_id=123, bot_client=bot)

    @pytest.mark.asyncio
    async def test_sends_wallpaper_when_file_exists(self, tmp_path: Path) -> None:
        img = tmp_path / "wall.jpg"
        img.write_bytes(b"img-data")

        config = MagicMock()
        config.name = "techie"
        config.wallpaper = "wall.jpg"
        config.startup_sound = None

        bot = AsyncMock()
        with patch("navig.personas.resolver.resolve_persona", return_value=tmp_path):
            await deliver(config, chat_id=42, bot_client=bot)

        bot.send_photo.assert_called_once()

    @pytest.mark.asyncio
    async def test_sends_sound_when_file_exists(self, tmp_path: Path) -> None:
        sound = tmp_path / "startup.ogg"
        sound.write_bytes(b"ogg-data")

        config = MagicMock()
        config.name = "techie"
        config.wallpaper = None
        config.startup_sound = "startup.ogg"

        bot = AsyncMock()
        with patch("navig.personas.resolver.resolve_persona", return_value=tmp_path):
            await deliver(config, chat_id=42, bot_client=bot)

        bot.send_voice.assert_called_once()

    @pytest.mark.asyncio
    async def test_swallows_send_photo_exception(self, tmp_path: Path) -> None:
        img = tmp_path / "wall.jpg"
        img.write_bytes(b"img")

        config = MagicMock()
        config.name = "techie"
        config.wallpaper = "wall.jpg"
        config.startup_sound = None

        bot = AsyncMock()
        bot.send_photo.side_effect = Exception("telegram down")
        with patch("navig.personas.resolver.resolve_persona", return_value=tmp_path):
            await deliver(config, chat_id=1, bot_client=bot)  # must not raise

    @pytest.mark.asyncio
    async def test_skips_wallpaper_when_file_missing(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.name = "techie"
        config.wallpaper = "nonexistent.jpg"
        config.startup_sound = None

        bot = AsyncMock()
        with patch("navig.personas.resolver.resolve_persona", return_value=tmp_path):
            await deliver(config, chat_id=1, bot_client=bot)

        bot.send_photo.assert_not_called()
