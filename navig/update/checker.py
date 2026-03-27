"""Version checker for navig update.

VersionChecker probes local and SSH nodes to determine current and
latest available versions, producing VersionInfo records.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from navig.update.models import VersionInfo
from navig.update.sources import SourceError, _BaseSource


class VersionChecker:
    """Check current vs. latest version for a target node."""

    def __init__(
        self,
        source: _BaseSource,
        remote_ops: Any = None,
        cache: Optional[Dict[str, str]] = None,  # shared {source_label: latest_version}
    ):
        self._source = source
        self._remote_ops = remote_ops
        self._cache = cache if cache is not None else {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_local(self) -> VersionInfo:
        """Check local install version."""
        from pathlib import Path

        try:
            import navig as _navig

            current = str(getattr(_navig, "__version__", "unknown"))
        except Exception:
            current = "unknown"

        src_dir = Path(__file__).resolve().parent.parent.parent
        install_type = "git" if (src_dir / ".git").exists() else "pip"

        latest = self._latest_cached()

        return VersionInfo(
            node_id="local",
            current=current,
            latest=latest,
            install_type=install_type,
            source_name=self._source.label,
            error=None if latest else "Could not determine latest version",
        )

    def check_ssh(self, node_id: str, server_config: Dict) -> VersionInfo:
        """Check version on a remote SSH node."""
        if self._remote_ops is None:
            from navig.remote import RemoteOperations

            self._remote_ops = RemoteOperations()

        current = "unknown"
        install_type = "unknown"
        error: Optional[str] = None

        try:
            # Try JSON output first (navig >= 2.3)
            r = self._remote_ops.execute_command(
                "navig version --json 2>/dev/null || navig --version 2>/dev/null",
                server_config=server_config,
            )
            output = ""
            if hasattr(r, "stdout"):
                output = (r.stdout or "").strip()
            elif isinstance(r, str):
                output = r.strip()

            # Try JSON parse first
            try:
                data = json.loads(output)
                current = data.get("version", "unknown")
                install_type = data.get("install_type", "unknown")
            except Exception:
                # Plain text: "navig 2.4.16" or "2.4.16"
                m = re.search(r"\d+\.\d+\.\d+", output)
                if m:
                    current = m.group(0)

        except Exception as exc:
            error = str(exc)[:120]

        latest = self._latest_cached()

        return VersionInfo(
            node_id=node_id,
            current=current,
            latest=latest,
            install_type=install_type,
            source_name=self._source.label,
            error=error,
        )

    def latest_from_source(self) -> Optional[str]:
        """Return latest version as a string, None on failure."""
        return self._latest_cached()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _latest_cached(self) -> Optional[str]:
        key = self._source.label
        if key not in self._cache:
            try:
                self._cache[key] = self._source.latest_version()
            except SourceError:
                self._cache[key] = None  # type: ignore[assignment]
        return self._cache.get(key)
