"""Compatibility shim — the `navig.voice` implementation moved to the navig-audio
plugin (`navig_audio.voice`). Core keeps this thin re-export layer so existing
consumers work unchanged when navig-audio is installed, and degrade gracefully
(ImportError) when it isn't. Heavy deps (edge-tts / faster-whisper / openai) live
in navig-audio, not core.
"""
import navig_audio.voice as _m  # noqa: E402

globals().update({k: getattr(_m, k) for k in dir(_m) if not k.startswith("__")})  # keep private helpers (compat)
del _m
