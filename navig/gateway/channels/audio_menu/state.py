"""AudioConfig dataclass and JSON persistence for the /audio deep menu."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from navig.platform.paths import audio_configs_dir

_STORE_DIR = audio_configs_dir()

# In-memory cache: user_id → AudioConfig
_cache: dict[int, "AudioConfig"] = {}


@dataclass
class AudioConfig:
    """Per-user audio/TTS configuration persisted across restarts."""

    provider: str = "openai"
    model: str = "tts-1-hd"
    voice: str = "nova"
    speed: float = 1.0
    format: str = "mp3"
    auto: bool = False
    active: bool = False


def _store_path(user_id: int) -> Path:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    return _STORE_DIR / f"{user_id}.json"


def load_config(user_id: int) -> AudioConfig:
    """Load AudioConfig for user_id; return defaults if not found."""
    if user_id in _cache:
        return _cache[user_id]

    p = _store_path(user_id)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            known = set(AudioConfig.__dataclass_fields__)
            cfg = AudioConfig(**{k: v for k, v in data.items() if k in known})
            _cache[user_id] = cfg
            return cfg
        except (json.JSONDecodeError, TypeError):
            pass  # fallback to defaults

    cfg = AudioConfig()
    _cache[user_id] = cfg
    return cfg


def save_config(user_id: int, cfg: AudioConfig) -> None:
    """Persist AudioConfig to disk and update in-memory cache."""
    _cache[user_id] = cfg
    try:
        _store_path(user_id).write_text(json.dumps(asdict(cfg), indent=2))
    except OSError:
        pass  # best-effort cleanup
