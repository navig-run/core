"""Compatibility shim — `navig.voice.tts` moved to the **navig-audio** plugin.

Re-exports `navig_audio.voice.tts` so existing `from navig.voice.tts import …`
keeps working when navig-audio is installed; raises ImportError when it isn't, which
the (guarded / lazy) callers already handle by degrading voice features.
"""
import navig_audio.voice.tts as _m  # noqa: E402

globals().update({k: getattr(_m, k) for k in dir(_m) if not k.startswith("__")})  # keep private helpers (compat)
del _m
