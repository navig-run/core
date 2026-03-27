"""navig.selfheal.ssh_healer — Async SSH problem remediation.

Handles three well-known SSH failure classes without ever blocking the event
loop or auto-modifying SSH config files:

    SSH_HOSTKEY_UNKNOWN  — keyscan the host and append to known_hosts
    SSH_AUTH_FAIL        — ensure an ED25519 keypair exists on disk
    SSH_TRANSPORT_FAIL   — TCP-probe the port, retry with verbose flags

Every public method returns a :class:`HealResult` — callers never need to do
their own error handling.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loguru import logger

# ---------------------------------------------------------------------------
# Types (shared with telegram_autoheal — import that module for the canonical
# FailureClass/FailureContext; HealResult is re-defined here to keep this
# module self-contained and cleanly testable without importing the whole
# Telegram layer)
# ---------------------------------------------------------------------------

HealStatus = Literal["resolved", "partial", "failed"]


@dataclass
class HealResult:
    """Outcome of a single auto-fix attempt."""

    status: HealStatus
    message: str  # sanitized, safe to display to end users
    should_retry: bool = False  # True iff the original command can now be retried
    detail: str = ""  # developer-facing extra info (not shown to user)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOCALHOST_ALIASES = {"127.0.0.1", "::1", "localhost"}
_DEFAULT_SSH_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
_KNOWN_HOSTS_PATH = Path.home() / ".ssh" / "known_hosts"
_KEYSCAN_TIMEOUT = 10  # seconds


class SSHHealer:
    """
    Stateless helper that resolves common SSH failure scenarios.

    Design constraints:
    - *Never* modifies ~/.ssh/config files — only known_hosts and key files
    - *Never* retries more than once per call (dedup is the caller's job)
    - *Never* blocks — all I/O is asyncio subprocess based
    - *Always* returns a HealResult; exceptions are caught internally
    """

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    async def keyscan_and_trust(self, host: str) -> HealResult:
        """Resolve SSH_HOSTKEY_UNKNOWN by appending scanned key to known_hosts.

        Special case: if the target is localhost / 127.0.0.1, this returns a
        *partial* result with a warning rather than auto-trusting.  Connecting
        to 127.0.0.1 via SSH is an unusual pattern that deserves human review.

        Args:
            host: Hostname or IP to scan.

        Returns:
            HealResult with status "resolved" on success.
        """
        if host in _LOCALHOST_ALIASES:
            return HealResult(
                status="partial",
                message=(
                    "⚠️ Target is *localhost* (`127.0.0.1`). "
                    "Auto-trusting loopback host keys is unusual — please verify "
                    "your host configuration before proceeding.\n\n"
                    "If you're sure, manually run:\n"
                    "`ssh-keyscan -H 127.0.0.1 >> ~/.ssh/known_hosts`"
                ),
                should_retry=False,
                detail="localhost guard triggered",
            )

        # Ensure ~/.ssh/ directory exists (first-run safety)
        _KNOWN_HOSTS_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        logger.info("ssh_healer: keyscan %s", host)
        try:
            # Write scanned keys to a temp file first so we can inspect
            # before appending — avoids corrupting known_hosts on error.
            with tempfile.NamedTemporaryFile(mode="w", suffix=".keyscan", delete=False) as tmp:
                tmp_path = tmp.name

            proc = await asyncio.create_subprocess_exec(
                "ssh-keyscan",
                "-H",
                "-T",
                str(_KEYSCAN_TIMEOUT),
                host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_KEYSCAN_TIMEOUT + 5
            )

            if proc.returncode != 0 or not stdout.strip():
                detail = stderr.decode(errors="replace").strip()
                logger.warning("ssh_healer: keyscan failed for %s: %s", host, detail)
                return HealResult(
                    status="failed",
                    message=(
                        f"❌ Could not scan SSH host keys for `{host}`.\n"
                        "The host may be unreachable or SSH is not running on port 22."
                    ),
                    detail=detail,
                )

            # Append scanned keys to known_hosts
            with open(_KNOWN_HOSTS_PATH, "ab") as kh:
                kh.write(stdout)

            logger.info("ssh_healer: added host key for %s", host)
            return HealResult(
                status="resolved",
                message=f"✅ SSH host key for `{host}` added to `known_hosts`.",
                should_retry=True,
            )

        except asyncio.TimeoutError:
            return HealResult(
                status="failed",
                message=f"❌ `ssh-keyscan {host}` timed out after {_KEYSCAN_TIMEOUT}s.",
                detail="keyscan timeout",
            )
        except FileNotFoundError:
            # ssh-keyscan not on PATH
            return HealResult(
                status="failed",
                message=(
                    "❌ `ssh-keyscan` not found on this system.\n"
                    "Please install OpenSSH client tools."
                ),
                detail="ssh-keyscan binary missing",
            )
        except OSError as exc:
            logger.exception("ssh_healer: keyscan OS error")
            return HealResult(
                status="failed",
                message="❌ Could not write to `known_hosts` — check file permissions.",
                detail=str(exc),
            )

    async def ensure_ssh_key(self, host: str) -> HealResult:
        """Resolve SSH_AUTH_FAIL by generating a missing ED25519 keypair.

        If the key already exists, returns a *partial* result prompting the user
        to verify their key is added to the server's authorized_keys (we cannot
        do that automatically without knowing server credentials).

        Args:
            host: Target host (informational only — key is generated locally).

        Returns:
            HealResult. ``should_retry`` is always False here because the user
            must add the public key to the remote server before retrying.
        """
        if _DEFAULT_SSH_KEY_PATH.exists():
            # Key present but rejected → auth misconfiguration on the remote side
            pub_key = self._read_public_key()
            pub_preview = pub_key[:120] + "…" if len(pub_key) > 120 else pub_key
            return HealResult(
                status="partial",
                message=(
                    f"🔑 An SSH key already exists at `{_DEFAULT_SSH_KEY_PATH}`.\n\n"
                    "The server rejected it. Possible causes:\n"
                    "• Your public key is not in `~/.ssh/authorized_keys` on the server\n"
                    "• Wrong user or key mismatch\n\n"
                    f"Your public key:\n```\n{pub_preview}\n```\n"
                    f"Add it to the server with:\n"
                    f"`ssh-copy-id -i ~/.ssh/id_ed25519 user@{host}`"
                ),
                should_retry=False,
            )

        # No key at all — generate one
        _DEFAULT_SSH_KEY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        logger.info("ssh_healer: generating ED25519 key pair")
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh-keygen",
                "-t",
                "ed25519",
                "-N",
                "",  # empty passphrase — automated use
                "-f",
                str(_DEFAULT_SSH_KEY_PATH),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

            if proc.returncode != 0:
                detail = stderr.decode(errors="replace").strip()
                return HealResult(
                    status="failed",
                    message="❌ Failed to generate SSH key pair.",
                    detail=detail,
                )

            pub_key = self._read_public_key()
            pub_preview = pub_key[:120] + "…" if len(pub_key) > 120 else pub_key
            return HealResult(
                status="partial",
                message=(
                    "🔑 New ED25519 SSH key generated.\n\n"
                    "Add the public key to your server's `authorized_keys`:\n"
                    f"```\n{pub_preview}\n```\n"
                    f"`ssh-copy-id -i ~/.ssh/id_ed25519 user@{host}`\n\n"
                    "Then retry your command."
                ),
                should_retry=False,  # user action required first
                detail="new keypair generated",
            )

        except asyncio.TimeoutError:
            return HealResult(
                status="failed",
                message="❌ `ssh-keygen` timed out.",
                detail="keygen timeout",
            )
        except FileNotFoundError:
            return HealResult(
                status="failed",
                message=("❌ `ssh-keygen` not found. Please install OpenSSH client tools."),
                detail="ssh-keygen binary missing",
            )
        except OSError as exc:
            logger.exception("ssh_healer: key generation OS error")
            return HealResult(
                status="failed",
                message="❌ Could not write SSH key — check file permissions.",
                detail=str(exc),
            )

    async def probe_ssh_transport(self, host: str, port: int = 22) -> HealResult:
        """Resolve SSH_TRANSPORT_FAIL by probing connectivity and retrying.

        Step 1: TCP probe the SSH port.
        Step 2: If reachable, re-attempt one SSH connection with verbose flags
                and a conservative timeout to capture a cleaner error.

        Args:
            host: Remote hostname / IP.
            port: SSH port (default 22).

        Returns:
            HealResult with diagnostic output in ``message``.
        """
        logger.info("ssh_healer: probing %s:%d", host, port)

        # ── Step 1: TCP probe ──────────────────────────────────────────────
        reachable = await self._tcp_probe(host, port)
        if not reachable:
            return HealResult(
                status="failed",
                message=(
                    f"❌ TCP port {port} on `{host}` is unreachable.\n\n"
                    "Possible causes:\n"
                    "• SSH daemon is not running (`systemctl start sshd`)\n"
                    "• Firewall blocking port 22\n"
                    "• Wrong hostname or IP address"
                ),
                detail=f"tcp_probe failed for {host}:{port}",
            )

        # ── Step 2: SSH verbose probe ──────────────────────────────────────
        # `-o BatchMode=yes` prevents interactive prompts in the subprocess.
        # `-v` adds one level of verbosity to capture handshake details.
        # `-o ConnectTimeout=8` is generous but bounded.
        probe_cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=1",
            "-v",
            "-p",
            str(port),
            f"probe@{host}",  # user doesn't matter for transport diagnosis
            "exit 0",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *probe_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
            verbose_output = stderr.decode(errors="replace").strip()

            # Exit code 255 is expected here (auth will fail with probe@host),
            # but if the transport succeeded we see "Authenticated" or "Permission" in output.
            transport_ok = any(
                sig in verbose_output
                for sig in (
                    "debug1: Authentications that can continue",
                    "Permission denied",
                    "Authenticated",
                )
            )

            if transport_ok:
                # SSH transport is fine — the failure was auth-level
                return HealResult(
                    status="partial",
                    message=(
                        f"✅ SSH transport to `{host}:{port}` is working.\n\n"
                        "The original failure was likely an *authentication* issue "
                        "(wrong key or credential), not a network problem.\n"
                        "Run `/autoheal` after checking your SSH key setup."
                    ),
                    should_retry=False,
                    detail=verbose_output[:500],
                )

            # Cannot determine exact cause — return verbose output sanitized
            sanitized = _sanitize_ssh_verbose(verbose_output)
            return HealResult(
                status="partial",
                message=(
                    f"⚠️ SSH transport to `{host}:{port}` is intermittent.\n\n"
                    f"Diagnostic:\n```\n{sanitized}\n```"
                ),
                should_retry=True,  # worth retrying the original command once
                detail=verbose_output[:500],
            )

        except asyncio.TimeoutError:
            return HealResult(
                status="failed",
                message=(
                    f"❌ SSH probe to `{host}:{port}` timed out (20s).\n"
                    "The host is reachable on TCP but SSH is not responding."
                ),
                detail="ssh verbose probe timeout",
            )
        except FileNotFoundError:
            return HealResult(
                status="failed",
                message="❌ `ssh` binary not found. Please install OpenSSH client.",
                detail="ssh binary missing",
            )

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _tcp_probe(self, host: str, port: int, timeout: float = 3.0) -> bool:
        """Attempt a TCP connection and return True if successful."""
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
            writer.close()
            return True
        except (OSError, asyncio.TimeoutError, ConnectionRefusedError):
            return False

    @staticmethod
    def _read_public_key() -> str:
        """Read the public key corresponding to the default ED25519 private key."""
        pub_path = _DEFAULT_SSH_KEY_PATH.with_suffix(".pub")
        if pub_path.exists():
            return pub_path.read_text(encoding="utf-8").strip()
        return "(public key file not found)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_ssh_verbose(output: str) -> str:
    """Strip verbose debug1 lines, keep only the substantive lines."""
    lines = output.splitlines()
    key_lines = [
        line
        for line in lines
        if not line.startswith("debug1: ")
        or any(
            kw in line
            for kw in (
                "Connecting",
                "connect",
                "cipher",
                "Authentications",
                "Permission",
                "Error",
                "Warning",
                "failed",
            )
        )
    ]
    return "\n".join(key_lines[-15:])  # last 15 significant lines
