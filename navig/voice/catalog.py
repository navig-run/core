"""Compatibility shim — re-exports `navig_audio.voice.catalog` (the speech capability view).

Raises ImportError when the navig-audio plugin is not installed; callers degrade to a
"plugin required" answer.
"""
import navig_audio.voice.catalog as _m  # noqa: E402

globals().update({k: getattr(_m, k) for k in dir(_m) if not k.startswith("__")})
del _m
