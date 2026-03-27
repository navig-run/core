"""
navig.deploy.health — Health checker for deployed apps.

Health checks run ON THE REMOTE HOST via SSH curl, so the operator machine
does not need direct network access to the server's app port.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from navig.deploy.models import HealthConfig

logger = logging.getLogger(__name__)


class HealthChecker:
    """
    Check app health by running curl on the remote host.

    Using remote curl means:
    - Works even when the app port is firewalled (SSH-only server)
    - No requirement for the operator's machine to reach the service directly
    - curl is available on virtually every Linux/macOS server
    """

    def __init__(
        self,
        config: HealthConfig,
        server_config: dict[str, Any],
        remote_ops: Any,
        dry_run: bool = False,
    ):
        self._cfg = config
        self._server = server_config
        self._remote = remote_ops
        self._dry_run = dry_run

    def check(self) -> tuple[bool, str]:
        """
        Run health check with retry logic.

        Returns:
            (success, detail_message)
        """
        if self._dry_run:
            desc = self._cfg.url or self._cfg.command or "(none)"
            return True, f"[DRY RUN] would check: {desc}"

        if not self._cfg.url and not self._cfg.command:
            return True, "No health check configured — skipped"

        retries = self._cfg.retries
        interval = self._cfg.interval_seconds

        for attempt in range(1, retries + 1):
            ok, msg = self._single_check()
            if ok:
                suffix = f"attempt {attempt}/{retries}"
                return True, f"{msg} ({suffix})"
            if attempt < retries:
                logger.debug(
                    "Health check attempt %d/%d failed: %s — retrying in %ds",
                    attempt,
                    retries,
                    msg,
                    interval,
                )
                time.sleep(interval)

        return False, f"All {retries} health check attempts failed. Last: {msg}"

    def _single_check(self) -> tuple[bool, str]:
        """Execute a single health check attempt on the remote host."""
        if self._cfg.command:
            return self._run_command_check()
        return self._run_http_check()

    def _run_http_check(self) -> tuple[bool, str]:
        """Check by running curl on the remote and inspecting HTTP status code."""
        url = self._cfg.url
        method = self._cfg.method.upper()
        expect = self._cfg.expected_status
        timeout = self._cfg.timeout_seconds

        # -sf: silent + fail on HTTP error (but we capture status ourselves)
        # -o /dev/null: discard body
        # -w '%{http_code}': print only status code
        # -X: HTTP method
        # --max-time: per-request timeout
        cmd = f"curl -s -o /dev/null -w '%{{http_code}}' -X {method} --max-time {timeout} '{url}'"

        result = self._remote.execute_command(cmd, self._server)
        if result.returncode != 0:
            return False, f"curl failed (exit {result.returncode})"

        status_str = (result.stdout or "").strip().strip("'")
        try:
            status = int(status_str)
        except ValueError:
            return False, f"Unexpected curl output: {status_str!r}"

        if status == expect:
            return True, f"{method} {url} → {status}"
        return False, f"{method} {url} → {status} (expected {expect})"

    def _run_command_check(self) -> tuple[bool, str]:
        """Run an arbitrary remote command as health check (exit 0 = healthy)."""
        result = self._remote.execute_command(self._cfg.command, self._server)
        if result.returncode == 0:
            return True, "command exit 0"
        detail = (result.stderr or result.stdout or "").strip()[:200]
        return False, f"command exit {result.returncode}: {detail}"
