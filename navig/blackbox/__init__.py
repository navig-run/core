"""NAVIG Blackbox — postmortem event recorder, crash bundler, and investigation toolkit.

Architecture
------------
recorder  : Append-only JSONL event stream (~/.navig/blackbox/events.jsonl)
crash     : CrashReport dataclass + sys.excepthook installer
bundle    : Create / inspect .navbox ZIP archives
timeline  : Rich table renderer for event lists
seal      : Immutable SEALED marker for incident preservation
export    : Write (optionally encrypted) .navbox files

Quick start
-----------
    from navig.blackbox import get_recorder, EventType

    rec = get_recorder()
    rec.record(EventType.COMMAND, {"command": "vault", "args": "list"})

    from navig.blackbox import create_bundle, write_bundle
    bundle = create_bundle(since_hours=4)
    write_bundle(bundle, Path("~/incident.navbox").expanduser())
"""

from .types    import BlackboxEvent, Bundle, EventType
from .recorder import BlackboxRecorder, get_recorder
from .crash    import CrashReport, install_crash_handler, list_crashes, record_crash
from .bundle   import create_bundle, inspect_bundle, write_bundle
from .timeline import render_timeline, format_event_summary
from .seal     import is_sealed, seal_bundle, unseal
from .export   import export_bundle

__all__ = [
    # Types
    "BlackboxEvent",
    "Bundle",
    "EventType",
    # Recorder
    "BlackboxRecorder",
    "get_recorder",
    # Crash
    "CrashReport",
    "install_crash_handler",
    "list_crashes",
    "record_crash",
    # Bundle
    "create_bundle",
    "inspect_bundle",
    "write_bundle",
    # Timeline
    "render_timeline",
    "format_event_summary",
    # Seal
    "is_sealed",
    "seal_bundle",
    "unseal",
    # Export
    "export_bundle",
]
