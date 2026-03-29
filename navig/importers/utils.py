from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value)


def chrome_default_path() -> str | None:
    if not is_windows():
        return None
    base = env_path("LOCALAPPDATA")
    if base is None:
        return None
    return str(base / "Google" / "Chrome" / "User Data" / "Default" / "Bookmarks")


def edge_default_path() -> str | None:
    if not is_windows():
        return None
    base = env_path("LOCALAPPDATA")
    if base is None:
        return None
    return str(base / "Microsoft" / "Edge" / "User Data" / "Default" / "Bookmarks")


def winscp_default_path() -> str | None:
    if not is_windows():
        return None
    app_data = env_path("APPDATA")
    if app_data is None:
        return None
    return str(app_data / "WinSCP.ini")


def safari_default_path() -> str | None:
    if not is_macos():
        return None
    return str(Path.home() / "Library" / "Safari" / "Bookmarks.plist")


def firefox_places_default_path() -> str | None:
    if is_windows():
        base = env_path("APPDATA")
        if base is None:
            return None
        profiles_ini = base / "Mozilla" / "Firefox" / "profiles.ini"
    elif is_macos():
        profiles_ini = Path.home() / "Library" / "Application Support" / "Firefox" / "profiles.ini"
    else:
        profiles_ini = Path.home() / ".mozilla" / "firefox" / "profiles.ini"

    if not profiles_ini.exists():
        return None

    profile_path = _discover_firefox_profile(profiles_ini)
    if profile_path is None:
        return None
    places = profile_path / "places.sqlite"
    return str(places) if places.exists() else None


def _discover_firefox_profile(profiles_ini: Path) -> Path | None:
    lines = profiles_ini.read_text(encoding="utf-8", errors="ignore").splitlines()
    current: dict[str, str] = {}
    profiles: list[dict[str, str]] = []

    def flush() -> None:
        if current:
            profiles.append(current.copy())
            current.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            flush()
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        current[key.strip()] = value.strip()
    flush()

    if not profiles:
        return None

    selected = None
    for profile in profiles:
        if profile.get("Default") == "1":
            selected = profile
            break
    if selected is None:
        selected = profiles[0]

    rel = selected.get("IsRelative", "1") == "1"
    p = selected.get("Path")
    if not p:
        return None

    if rel:
        base = profiles_ini.parent
        return base / p
    return Path(p)
