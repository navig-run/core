"""Compatibility shim — `navig.voice.playback` moved to the **navig-audio** plugin.

Re-exports `navig_audio.voice.playback` so existing `from navig.voice.playback import …`
keeps working when navig-audio is installed; raises ImportError when it isn't, which
the (guarded / lazy) callers already handle by degrading voice features.
"""
import navig_audio.voice.playback as _m  # noqa: E402

# Re-export everything except dunders — including single-underscore private
# helpers (e.g. `_resolve_asset`), so pre-move `from navig.voice.playback import
# _helper` call sites and tests keep working through the shim.
globals().update({k: getattr(_m, k) for k in dir(_m) if not k.startswith("__")})
del _m
