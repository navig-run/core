"""Compatibility shim — `navig.voice.pipeline` moved to the **navig-audio** plugin.

Re-exports `navig_audio.voice.pipeline` so existing `from navig.voice.pipeline import …`
keeps working when navig-audio is installed; raises ImportError when it isn't, which
the (guarded / lazy) callers already handle by degrading voice features.
"""
import navig_audio.voice.pipeline as _m  # noqa: E402

globals().update({k: getattr(_m, k) for k in dir(_m) if not k.startswith("__")})  # keep private helpers (compat)
del _m
