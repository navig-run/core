"""Compatibility shim — `navig.voice.stt` moved to the **navig-audio** plugin.

Re-exports `navig_audio.voice.stt` so existing `from navig.voice.stt import …`
keeps working when navig-audio is installed; raises ImportError when it isn't, which
the (guarded / lazy) callers already handle by degrading voice features.
"""
import navig_audio.voice.stt as _m  # noqa: E402

globals().update({k: getattr(_m, k) for k in dir(_m) if not k.startswith("__")})  # keep private helpers (compat)
del _m
