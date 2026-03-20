"""
Memory Snapshot Writer — Persists normalized API tool outputs to disk.

Provides append-only JSONL storage for API state snapshots, with:
  - Configurable per-tool storage policies (store/discard, retention)
  - Automatic sensitive-field redaction before write
  - Staleness detection for ContextBuilder
  - Retention-based pruning

Storage location: .navig/memory/api_state/<workspace>.jsonl
Each line is a self-contained JSON object with:
  { "tool", "normalized", "source", "timestamp", "workspace", "lane" }

Usage:
    from navig.memory.snapshot import SnapshotWriter, should_store, load_snapshots

    writer = SnapshotWriter()
    writer.write(api_tool_result, workspace="myproject")

    recent = load_snapshots(tool="infra.metrics.node_status", max_age_minutes=60)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("navig.memory.snapshot")


# ─────────────────────────────────────────────────────────────
# Snapshot Policy
# ─────────────────────────────────────────────────────────────

@dataclass
class SnapshotPolicy:
    """Per-tool storage policy."""
    store: bool = False
    retention: str = "7d"  # e.g. "24h", "7d", "30d"

    def retention_delta(self) -> timedelta:
        """Parse retention string into a timedelta."""
        return _parse_retention(self.retention)


# Default policies (fallback: store=False)
_DEFAULT_POLICIES: Dict[str, SnapshotPolicy] = {}


def _parse_retention(s: str) -> timedelta:
    """Parse a retention string like '24h', '7d', '30d' into timedelta."""
    s = s.strip().lower()
    match = re.match(r"^(\d+)\s*(h|d|m|w)$", s)
    if not match:
        logger.warning("Invalid retention format %r, defaulting to 7d", s)
        return timedelta(days=7)
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        return timedelta(hours=value)
    if unit == "d":
        return timedelta(days=value)
    if unit == "w":
        return timedelta(weeks=value)
    if unit == "m":
        return timedelta(days=value * 30)
    return timedelta(days=7)


def load_snapshot_policies(
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, SnapshotPolicy]:
    """
    Load per-tool snapshot policies from config.

    Config shape (from config.yaml memory.api_snapshot_policies):
        {
          "trading.fetch.ohlc":        {"store": true, "retention": "7d"},
          "web.api.get_json":          {"store": false},
          "infra.metrics.node_status": {"store": true, "retention": "24h"},
        }

    Returns dict of tool_name → SnapshotPolicy.
    """
    if config is None:
        config = _load_policies_from_yaml()
    policies: Dict[str, SnapshotPolicy] = {}
    for tool_name, spec in config.items():
        if isinstance(spec, dict):
            policies[tool_name] = SnapshotPolicy(
                store=spec.get("store", False),
                retention=spec.get("retention", "7d"),
            )
    return policies


def _load_policies_from_yaml() -> Dict[str, Any]:
    """Read memory.api_snapshot_policies from the NAVIG global config."""
    try:
        from navig.core.config_loader import load_config
        cfg = load_config()
        mem = getattr(cfg, "memory", None)
        if mem and hasattr(mem, "api_snapshot_policies"):
            return mem.api_snapshot_policies or {}
        # Also try raw dict access
        raw = cfg if isinstance(cfg, dict) else {}
        return raw.get("memory", {}).get("api_snapshot_policies", {})
    except Exception:
        return {}


def should_store(
    tool_name: str,
    policies: Optional[Dict[str, SnapshotPolicy]] = None,
) -> tuple[bool, Optional[timedelta]]:
    """
    Decision helper: should we persist a snapshot for this tool?

    Returns:
        (store: bool, retention: timedelta | None)
        Fallback: (False, None) for tools without an explicit policy.
    """
    if policies is None:
        policies = load_snapshot_policies()
    policy = policies.get(tool_name)
    if policy is None:
        return False, None
    if not policy.store:
        return False, None
    return True, policy.retention_delta()


# ─────────────────────────────────────────────────────────────
# Snapshot Writer
# ─────────────────────────────────────────────────────────────

def _get_snapshot_dir() -> Path:
    """Resolve the api_state snapshot directory."""
    # Check project-local .navig first, then global
    local = Path.cwd() / ".navig" / "memory" / "api_state"
    if (Path.cwd() / ".navig").is_dir():
        return local
    global_dir = Path.home() / ".navig" / "memory" / "api_state"
    return global_dir


@dataclass
class SnapshotEntry:
    """A single snapshot record stored in JSONL."""
    tool: str
    normalized: Any
    source_endpoint: str = ""
    timestamp: str = ""
    workspace: str = "default"
    lane: str = ""
    host: str = ""

    def to_line(self) -> str:
        """Serialize to a single JSON line."""
        return json.dumps({
            "tool": self.tool,
            "normalized": self.normalized,
            "source_endpoint": self.source_endpoint,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
            "workspace": self.workspace,
            "lane": self.lane,
            "host": self.host,
        }, default=str, ensure_ascii=False)

    @classmethod
    def from_line(cls, line: str) -> "SnapshotEntry":
        """Deserialize from a JSON line."""
        d = json.loads(line)
        return cls(
            tool=d.get("tool", ""),
            normalized=d.get("normalized", {}),
            source_endpoint=d.get("source_endpoint", ""),
            timestamp=d.get("timestamp", ""),
            workspace=d.get("workspace", "default"),
            lane=d.get("lane", ""),
            host=d.get("host", ""),
        )


class SnapshotWriter:
    """
    Append-only writer for API state snapshots.

    Each workspace gets its own JSONL file: ``<workspace>.jsonl``.

    Supports two invocation modes:
      - Automatic: called by ToolRouter post-execution hook (if policy says store)
      - Manual: called explicitly by agent or user
    """

    def __init__(
        self,
        snapshot_dir: Optional[Path] = None,
        policies: Optional[Dict[str, SnapshotPolicy]] = None,
    ):
        self._dir = snapshot_dir or _get_snapshot_dir()
        self._policies = policies or load_snapshot_policies()

    def write(
        self,
        tool_result: Dict[str, Any],
        workspace: str = "default",
        lane: str = "",
        host: str = "",
        force: bool = False,
    ) -> bool:
        """
        Write a snapshot if the tool's policy permits it.

        Args:
            tool_result: ApiToolResult.to_dict() or compatible dict.
            workspace: Workspace/project identifier.
            lane: Optional workflow lane.
            host: Optional host context.
            force: Write even if policy says store=False.

        Returns:
            True if written, False if skipped.
        """
        source = tool_result.get("source", {})
        tool_name = source.get("tool", "")

        if not force:
            store, _ = should_store(tool_name, self._policies)
            if not store:
                logger.debug("Snapshot skipped for %s (policy: store=false)", tool_name)
                return False

        # Redact sensitive fields before writing
        from navig.tools.api_schema import redact_sensitive
        normalized = redact_sensitive(tool_result.get("normalized", {}))

        entry = SnapshotEntry(
            tool=tool_name,
            normalized=normalized,
            source_endpoint=source.get("endpoint", ""),
            timestamp=source.get("timestamp", datetime.now(timezone.utc).isoformat()),
            workspace=workspace,
            lane=lane,
            host=host,
        )

        # Ensure directory exists
        self._dir.mkdir(parents=True, exist_ok=True)

        file_path = self._dir / f"{workspace}.jsonl"
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(entry.to_line() + "\n")
            logger.debug("Snapshot written: %s → %s", tool_name, file_path)
            return True
        except Exception as e:
            logger.error("Failed to write snapshot for %s: %s", tool_name, e)
            return False

    def write_from_api_result(
        self,
        api_result: "ApiToolResult",
        workspace: str = "default",
        lane: str = "",
        host: str = "",
        force: bool = False,
    ) -> bool:
        """Write directly from an ApiToolResult object."""
        return self.write(
            tool_result=api_result.to_dict(),
            workspace=workspace, lane=lane, host=host, force=force,
        )


# ─────────────────────────────────────────────────────────────
# Snapshot Reader
# ─────────────────────────────────────────────────────────────

def load_snapshots(
    workspace: str = "default",
    tool: Optional[str] = None,
    max_age_minutes: Optional[int] = None,
    limit: int = 50,
    snapshot_dir: Optional[Path] = None,
) -> List[SnapshotEntry]:
    """
    Load snapshots from the JSONL file, optionally filtered.

    Args:
        workspace: Which workspace file to read.
        tool: Filter by tool name (exact match).
        max_age_minutes: Only return entries newer than this.
        limit: Max entries to return (most recent first).
        snapshot_dir: Override directory.

    Returns:
        List of SnapshotEntry, most recent first.
    """
    snap_dir = snapshot_dir or _get_snapshot_dir()
    file_path = snap_dir / f"{workspace}.jsonl"
    if not file_path.is_file():
        return []

    entries: List[SnapshotEntry] = []
    cutoff = None
    if max_age_minutes is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

    try:
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = SnapshotEntry.from_line(line)
            except (json.JSONDecodeError, KeyError):
                continue

            if tool and entry.tool != tool:
                continue

            if cutoff and entry.timestamp:
                try:
                    ts = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

            entries.append(entry)
    except Exception as e:
        logger.error("Failed to read snapshots from %s: %s", file_path, e)

    # Most recent first, capped
    entries.reverse()
    return entries[:limit]


def is_stale(
    workspace: str = "default",
    tool: str = "",
    max_age_minutes: int = 60,
    snapshot_dir: Optional[Path] = None,
) -> bool:
    """
    Check if the most recent snapshot for a tool is older than max_age_minutes.

    Returns True if stale (no snapshot or too old), False if fresh.
    """
    entries = load_snapshots(
        workspace=workspace,
        tool=tool,
        max_age_minutes=max_age_minutes,
        limit=1,
        snapshot_dir=snapshot_dir,
    )
    return len(entries) == 0


# ─────────────────────────────────────────────────────────────
# Retention / Pruning
# ─────────────────────────────────────────────────────────────

def prune_snapshots(
    workspace: str = "default",
    policies: Optional[Dict[str, SnapshotPolicy]] = None,
    snapshot_dir: Optional[Path] = None,
) -> int:
    """
    Remove snapshot entries that exceed their retention period.

    Returns the number of entries pruned.
    """
    snap_dir = snapshot_dir or _get_snapshot_dir()
    file_path = snap_dir / f"{workspace}.jsonl"
    if not file_path.is_file():
        return 0

    if policies is None:
        policies = load_snapshot_policies()

    now = datetime.now(timezone.utc)
    kept: List[str] = []
    pruned = 0

    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = SnapshotEntry.from_line(line)
        except (json.JSONDecodeError, KeyError):
            continue

        policy = policies.get(entry.tool)
        if policy is None:
            kept.append(entry.to_line())
            continue

        retention = policy.retention_delta()
        try:
            ts = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
            if now - ts > retention:
                pruned += 1
                continue
        except (ValueError, TypeError):
            pass

        kept.append(entry.to_line())

    # Rewrite file
    file_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    if pruned:
        logger.info("Pruned %d expired snapshots from %s", pruned, file_path.name)
    return pruned


def clear_snapshots(
    workspace: str = "default",
    tool: Optional[str] = None,
    older_than: Optional[str] = None,
    snapshot_dir: Optional[Path] = None,
) -> int:
    """
    Clear snapshots matching filters.

    Args:
        workspace: Target workspace.
        tool: Optional tool name filter.
        older_than: Optional age filter (e.g. "7d", "24h").
        snapshot_dir: Override directory.

    Returns:
        Number of entries removed.
    """
    snap_dir = snapshot_dir or _get_snapshot_dir()
    file_path = snap_dir / f"{workspace}.jsonl"
    if not file_path.is_file():
        return 0

    cutoff = None
    if older_than:
        cutoff = datetime.now(timezone.utc) - _parse_retention(older_than)

    lines = file_path.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = SnapshotEntry.from_line(line)
        except (json.JSONDecodeError, KeyError):
            continue

        remove = False
        if tool and entry.tool == tool:
            if cutoff is None:
                remove = True
            else:
                try:
                    ts = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
                    if ts < cutoff:
                        remove = True
                except (ValueError, TypeError):
                    pass
        elif tool is None and cutoff:
            try:
                ts = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
                if ts < cutoff:
                    remove = True
            except (ValueError, TypeError):
                pass

        if remove:
            removed += 1
        else:
            kept.append(entry.to_line())

    file_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

_writer: Optional[SnapshotWriter] = None


def get_snapshot_writer() -> SnapshotWriter:
    """Get or create the module-level SnapshotWriter singleton."""
    global _writer
    if _writer is None:
        _writer = SnapshotWriter()
    return _writer


def reset_snapshot_writer() -> None:
    """Reset singleton (for testing)."""
    global _writer
    _writer = None
