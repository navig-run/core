"""NAVIG Blackbox Bundle — create, inspect, and write .navbox archives.

A .navbox file is a standard ZIP archive containing:
  manifest.json   — metadata + SHA-256 content hash
  events.jsonl    — recent events
  crashes/        — crash report JSON files
  logs/           — tail snapshots of key log files
"""

from __future__ import annotations

import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .types import BlackboxEvent, Bundle

__all__ = ["create_bundle", "inspect_bundle", "write_bundle"]

_LOG_TAIL_LINES = 200
_BUNDLE_EXT = ".navbox"


def create_bundle(
    since_hours: float = 24.0,
    blackbox_dir: Path | None = None,
    log_files: list[Path] | None = None,
) -> Bundle:
    """Create a Bundle from recent events + log tails.

    Parameters
    ----------
    since_hours   : Include events from the last N hours.
    blackbox_dir  : Override storage directory.
    log_files     : Additional log files to include tails of.
    """
    if blackbox_dir is None:
        from navig.platform.paths import blackbox_dir as _bbdir

        blackbox_dir = _bbdir()

    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    # Events
    from .recorder import get_recorder

    events = get_recorder(blackbox_dir).read_events(since=since, limit=2000)

    # Crash reports
    from .crash import list_crashes

    crash_reports = [r.to_dict() for r in list_crashes(blackbox_dir)]

    # Log tails
    log_tails: dict[str, str] = {}
    candidate_logs = log_files or _default_log_files()
    for lp in candidate_logs:
        if lp.exists():
            try:
                lines = lp.read_text(encoding="utf-8", errors="replace").splitlines()
                log_tails[lp.name] = "\n".join(lines[-_LOG_TAIL_LINES:])
            except OSError:
                pass  # best-effort cleanup

    # NAVIG version
    try:
        from navig import __version__  # type: ignore[attr-defined]

        navig_version = __version__
    except Exception:
        navig_version = "unknown"

    # Compute hash over event + crash content
    content_bytes = json.dumps(
        {
            "events": [json.loads(e.to_json()) for e in events],
            "crashes": crash_reports,
        },
        separators=(",", ":"),
    ).encode()
    manifest_hash = Bundle.compute_hash(content_bytes)

    return Bundle(
        id=str(uuid.uuid4())[:8],
        created_at=datetime.now(timezone.utc),
        navig_version=navig_version,
        events=events,
        crash_reports=crash_reports,
        log_tails=log_tails,
        manifest_hash=manifest_hash,
        sealed=False,
    )


def inspect_bundle(path: Path) -> Bundle:
    """Load and return a Bundle from a .navbox ZIP file."""
    events: list[BlackboxEvent] = []
    crash_reports: list[dict] = []
    log_tails: dict[str, str] = {}
    manifest: dict = {}

    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())

        if "manifest.json" in names:
            manifest = json.loads(zf.read("manifest.json").decode())

        if "events.jsonl" in names:
            for line in zf.read("events.jsonl").decode().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(BlackboxEvent.from_dict(json.loads(line)))
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

        for name in names:
            if name.startswith("crashes/") and name.endswith(".json"):
                try:
                    crash_reports.append(json.loads(zf.read(name).decode()))
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            if name.startswith("logs/"):
                log_name = name.removeprefix("logs/")
                log_tails[log_name] = zf.read(name).decode(errors="replace")

    return Bundle(
        id=manifest.get("bundle_id", "unknown"),
        created_at=datetime.fromisoformat(
            manifest.get("created_at", "2000-01-01T00:00:00+00:00")
        ),
        navig_version=manifest.get("navig_version", "unknown"),
        events=events,
        crash_reports=crash_reports,
        log_tails=log_tails,
        manifest_hash=manifest.get("manifest_hash", ""),
        sealed=manifest.get("sealed", False),
    )


def write_bundle(bundle: Bundle, output: Path) -> Path:
    """Write a Bundle to a .navbox ZIP file.

    Returns the output path (adds .navbox extension if missing).
    """
    if not output.suffix == _BUNDLE_EXT:
        output = output.with_suffix(_BUNDLE_EXT)

    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Manifest
        manifest = {
            "bundle_id": bundle.id,
            "created_at": bundle.created_at.isoformat(),
            "navig_version": bundle.navig_version,
            "event_count": bundle.event_count(),
            "crash_count": bundle.crash_count(),
            "manifest_hash": bundle.manifest_hash,
            "sealed": bundle.sealed,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        # Events
        events_jsonl = "\n".join(e.to_json() for e in bundle.events) + "\n"
        zf.writestr("events.jsonl", events_jsonl)

        # Crash reports
        for i, cr in enumerate(bundle.crash_reports):
            ts = cr.get("timestamp", str(i)).replace(":", "-").replace("+", "")[:19]
            zf.writestr(f"crashes/crash-{ts}.json", json.dumps(cr, indent=2))

        # Log tails
        for log_name, content in bundle.log_tails.items():
            zf.writestr(f"logs/{log_name}", content)

    return output


def _default_log_files() -> list[Path]:
    """Default log files to include in bundles."""
    from navig.platform.paths import local_log_dir

    log_dir = local_log_dir()
    return [
        log_dir / "debug.log",
        log_dir / "navig.log",
        log_dir / "daemon.log",
    ]
