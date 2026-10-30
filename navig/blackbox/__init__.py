"""Compat shim — the NAVIG Blackbox engine now lives in the standalone ``navig-blackbox``
package (the ``voice/`` → navig-audio pattern). Install with ``pip install navig[blackbox]``.
Both ``navig blackbox`` and the standalone ``navig-blackbox`` CLI drive this same engine.
"""
from __future__ import annotations

try:
    from navig_blackbox import (  # noqa: F401
        BlackboxEvent,
        BlackboxRecorder,
        Bundle,
        CrashReport,
        EventType,
        create_bundle,
        export_bundle,
        format_event_summary,
        get_recorder,
        inspect_bundle,
        install_crash_handler,
        is_sealed,
        list_crashes,
        record_crash,
        render_timeline,
        seal_bundle,
        unseal,
        write_bundle,
    )
except ImportError as exc:  # pragma: no cover - exercised only on a bare install
    raise ImportError(
        "NAVIG Blackbox requires the navig-blackbox engine. "
        "Install it with: pip install navig[blackbox]"
    ) from exc

#: The submodule shims (``navig.blackbox.recorder`` …). Loaded lazily on attribute
#: access, so ``import navig.blackbox`` then ``navig.blackbox.recorder.get_recorder()``
#: keeps working as it did before the engine moved out, without importing all of them.
_SUBMODULES = frozenset({"bundle", "crash", "export", "recorder", "seal", "timeline", "types"})


def __getattr__(name: str):
    if name in _SUBMODULES:
        import importlib

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
