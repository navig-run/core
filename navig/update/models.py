"""Data models for the navig update system."""

from __future__ import annotations

from dataclasses import dataclass, field


def _version_lt(a: str, b: str) -> bool:
    """Return True if version *a* is strictly less than *b* (semver-ish)."""
    try:
        from packaging.version import Version  # type: ignore

        return Version(str(a)) < Version(str(b))
    except Exception:
        # Fallback: lexicographic tuple comparison on numeric parts
        def _parts(v: str):
            return tuple(int(x) for x in str(v).lstrip("v").split(".")[:3] if x.isdigit())

        try:
            return _parts(a) < _parts(b)
        except Exception:
            return str(a) < str(b)


@dataclass
class VersionInfo:
    """Version state for a single node."""

    node_id: str
    current: str = "unknown"
    latest: str | None = None
    install_type: str = "unknown"  # "git" | "pip" | "unknown"
    source_name: str = "unknown"
    error: str | None = None
    # Git checkouts only: upstream commits HEAD lacks (None = not a git install / unknown).
    commits_behind: int | None = None

    @property
    def needs_update(self) -> bool:
        if self.error or self.current == "unknown" or not self.latest:
            return False
        if self.install_type == "git" and self.commits_behind:
            return True
        return _version_lt(self.current, self.latest)

    @property
    def reachable(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "current": self.current,
            "latest": self.latest,
            "install_type": self.install_type,
            "source_name": self.source_name,
            "needs_update": self.needs_update,
            "error": self.error,
        }


@dataclass
class NodeResult:
    """Result of running an update on one node."""

    node_id: str
    ok: bool = True
    old_version: str = "unknown"
    new_version: str | None = None
    rolled_back: bool = False
    skipped: bool = False
    skip_reason: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    steps: list[str] = field(default_factory=list)
    #: The new code is on disk but the running daemon could not be reloaded, so it
    #: still executes the OLD code (e.g. an elevated daemon under a non-interactive
    #: run). The install itself succeeded — ``ok`` stays True — but the update is not
    #: live, so :attr:`UpdateResult.success` is False until the restart happens.
    restart_pending: bool = False
    restart_note: str | None = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "ok": self.ok,
            "old_version": self.old_version,
            "new_version": self.new_version,
            "rolled_back": self.rolled_back,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "error": self.error,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "steps": self.steps,
            "restart_pending": self.restart_pending,
            "restart_note": self.restart_note,
        }


@dataclass
class UpdatePlan:
    """The planned update work after a check phase."""

    targets: list = field(default_factory=list)
    version_infos: dict[str, VersionInfo] = field(default_factory=dict)
    to_update: list = field(default_factory=list)  # UpdateTarget list
    up_to_date: list = field(default_factory=list)  # UpdateTarget list
    unreachable: list = field(default_factory=list)  # UpdateTarget list
    dry_run: bool = False

    def summary(self) -> str:
        lines = [
            f"Plan: {len(self.to_update)} to update, "
            f"{len(self.up_to_date)} already up-to-date, "
            f"{len(self.unreachable)} unreachable"
        ]
        for t in self.to_update:
            vi = self.version_infos.get(t.node_id)
            if vi:
                lines.append(f"  {t.node_id}: {vi.current} → {vi.latest} ({vi.install_type})")
        return "\n".join(lines)


@dataclass
class UpdateResult:
    """Final result after executing an UpdatePlan."""

    plan: UpdatePlan = field(default_factory=UpdatePlan)
    node_results: list[NodeResult] = field(default_factory=list)
    dry_run: bool = False
    total_elapsed_seconds: float = 0.0

    @property
    def success(self) -> bool:
        # A node whose daemon still runs the old code is not a successful update:
        # automation must not treat that deployment as live.
        return all((r.ok or r.skipped) and not r.restart_pending for r in self.node_results)

    @property
    def restart_pending_nodes(self) -> list[NodeResult]:
        return [r for r in self.node_results if r.restart_pending]

    @property
    def failed_nodes(self) -> list[NodeResult]:
        return [r for r in self.node_results if not r.ok and not r.skipped]

    def node(self, node_id: str) -> NodeResult | None:
        for r in self.node_results:
            if r.node_id == node_id:
                return r
        return None

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "success": self.success,
            "total_elapsed_seconds": round(self.total_elapsed_seconds, 2),
            "nodes": [r.to_dict() for r in self.node_results],
        }
