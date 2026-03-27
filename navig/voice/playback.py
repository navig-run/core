"""
Audio Playback Module for NAVIG

Cross-platform audio playback for notifications, alerts, and voice output.
Uses Chappy firmware audio assets and supports custom sound files.

Usage:
    from navig.voice.playback import play_sound, play_notification

    # Play a built-in notification sound
    await play_notification("wake")

    # Play a sound file
    await play_sound("path/to/sound.mp3")

    # Sync variant
    play_notification_sync("alarm")
"""

from __future__ import annotations

import asyncio
import platform
from enum import Enum
from pathlib import Path

# =============================================================================
# Sound catalog — maps names to bundled asset files
# =============================================================================


class NotificationSound(str, Enum):
    """Built-in notification sounds from Chappy firmware assets."""

    ALARM = "alarm-default.mp3"
    ALARM_SHORT = "alarm-di.wav"
    ANALYZING = "Analyzing.mp3"
    HELLO = "Hi.mp3"
    NETWORK_ERROR = "networkError.mp3"
    PUSH_TO_TALK = "pushToTalk.mp3"
    TTS_FAILED = "tts_failed.mp3"
    WAIT = "waitPlease.mp3"
    WAKE = "echo_en_wake.wav"
    OK = "echo_en_ok.wav"
    END = "echo_en_end.wav"
    ONBOARD_SCROLL = "Onboarding_Scrollwheel.mp3"
    ONBOARD_TOUCH = "Onboarding_Touch.mp3"


ASSETS_DIR = Path(__file__).parent / "assets"


def _resolve_asset(name: str) -> Path | None:
    """Resolve an asset name to a full path."""
    # Try enum lookup first
    try:
        sound = NotificationSound(name)
        path = ASSETS_DIR / sound.value
        if path.exists():
            return path
    except ValueError:
        pass  # malformed value; skip

    # Try by enum name (case-insensitive)
    for s in NotificationSound:
        if s.name.lower() == name.lower():
            path = ASSETS_DIR / s.value
            if path.exists():
                return path

    # Try direct filename
    path = ASSETS_DIR / name
    if path.exists():
        return path

    # Try as absolute/relative path
    path = Path(name)
    if path.exists():
        return path

    return None


# =============================================================================
# Cross-platform playback backends
# =============================================================================


async def _play_windows(path: Path) -> bool:
    """Play audio on Windows using built-in winsound + PowerShell fallback."""
    ext = path.suffix.lower()

    # .wav → use winsound (built-in, no deps)
    if ext == ".wav":
        try:
            import winsound

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: winsound.PlaySound(str(path), winsound.SND_FILENAME)
            )
            return True
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    # .mp3/.wav fallback → PowerShell MediaPlayer
    try:
        cmd = (
            f'powershell -NoProfile -Command "'
            f"Add-Type -AssemblyName presentationCore; "
            f"$p = New-Object System.Windows.Media.MediaPlayer; "
            f"$p.Open([uri]'{path}'); "
            f"$p.Play(); "
            f"Start-Sleep -Milliseconds 3000; "
            f'$p.Close()"'
        )
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        return proc.returncode == 0
    except Exception:
        return False


async def _play_macos(path: Path) -> bool:
    """Play audio on macOS using afplay."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "afplay",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=15)
        return proc.returncode == 0
    except Exception:
        return False


async def _play_linux(path: Path) -> bool:
    """Play audio on Linux, trying multiple players."""
    players = [
        ["paplay", str(path)],
        ["aplay", str(path)],
        ["mpv", "--no-video", "--really-quiet", str(path)],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
    ]
    for cmd in players:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=15)
            if proc.returncode == 0:
                return True
        except (FileNotFoundError, asyncio.TimeoutError):
            continue
    return False


# =============================================================================
# Public API
# =============================================================================


async def play_sound(path_or_name: str | Path) -> bool:
    """
    Play an audio file (MP3 or WAV).

    Args:
        path_or_name: File path, asset filename, or NotificationSound name.

    Returns:
        True if playback succeeded.
    """
    path = _resolve_asset(str(path_or_name))
    if path is None:
        return False

    system = platform.system()
    if system == "Windows":
        return await _play_windows(path)
    elif system == "Darwin":
        return await _play_macos(path)
    else:
        return await _play_linux(path)


async def play_notification(name: str) -> bool:
    """
    Play a built-in notification sound by name.

    Args:
        name: NotificationSound enum name (e.g. "wake", "alarm", "ok").

    Returns:
        True if playback succeeded.
    """
    return await play_sound(name)


def play_sound_sync(path_or_name: str | Path) -> bool:
    """Synchronous wrapper for play_sound()."""
    return asyncio.run(play_sound(path_or_name))


def play_notification_sync(name: str) -> bool:
    """Synchronous wrapper for play_notification()."""
    return asyncio.run(play_notification(name))


def list_sounds() -> list[str]:
    """List all available built-in notification sounds."""
    return [s.name.lower() for s in NotificationSound]
