"""
Remote Operations

Execute commands through secure encrypted channels.
"""

import base64
import hashlib
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from navig.core.coerce import coerce_bool
from navig.core.connection import _resolve_scp_bin, _resolve_ssh_bin
from navig.core.proc_text import decode_console_result


def _resolve_ssh_timeout_seconds(default: int = 30) -> int:
    """Resolve SSH timeout from env with safe fallback.

    Any invalid or non-positive value falls back to ``default`` to avoid
    import-time crashes from malformed environment configuration.
    """
    raw = os.environ.get("NAVIG_SSH_TIMEOUT", str(default)).strip()
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        return default
    return timeout if timeout > 0 else default

# Default SSH/SCP timeout in seconds.  Override via NAVIG_SSH_TIMEOUT env var.
_SSH_TIMEOUT = _resolve_ssh_timeout_seconds()

# Per-host Windows-target memo for the exec transfer path (`ver` probe).
# Keyed by (host, port, user); a bool value is a completed probe result.
_WIN_PROBE_CACHE: dict[tuple[Any, ...], bool] = {}

# Per-host OS memo for callers that need the target's OS, not just "is it Windows".
# Keyed the same way; values are ``windows``, ``macos``, ``linux`` or ``unknown``.
_TARGET_OS_CACHE: dict[tuple[Any, ...], str] = {}


def _require_server_identity(server_config: dict[str, Any]) -> tuple[str, str]:
    """Return validated ``(user, host)`` identity for SSH/SCP operations."""
    user = str(server_config.get("user", "")).strip()
    host = str(server_config.get("host", "")).strip()
    if not user or not host:
        raise ValueError("Server configuration must include non-empty 'user' and 'host'.")
    return user, host


def is_local_host(server_config: dict) -> bool:
    """Return True when *server_config* refers to the local machine."""
    return (
        bool(server_config.get("is_local"))
        or str(server_config.get("type", "")).lower() == "local"
        or server_config.get("host", "") in ("localhost", "127.0.0.1", "::1")
    )


class RemoteOperations:
    """
    Handles remote SSH command execution.

    All operations are surgical. Clean. Traceable only in logs.
    """

    def __init__(self, config_manager):
        self.config = config_manager

    def execute_command(
        self,
        command: str,
        server_config: dict[str, Any],
        capture_output: bool = True,
        trust_new_host: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        """Execute a command on the remote server via SSH.

        Args:
            command: Shell command to execute
            server_config: Server configuration dictionary
            capture_output: Whether to capture stdout/stderr
            trust_new_host: If True, accepts new SSH host keys (use cautiously!)
                           If False (default), requires host key in known_hosts
            timeout: Per-call limit in seconds. Defaults to the process-wide
                     ``NAVIG_SSH_TIMEOUT`` limit when not given.

        Security Note:
            By default, StrictHostKeyChecking=yes is used to prevent MITM attacks.
            Only set trust_new_host=True for initial server setup when you can
            verify the host key out-of-band (e.g., console access, provider dashboard).
        """
        # SECURITY: Use strict host key checking by default
        # Only accept new hosts if explicitly requested
        # void: trust no one. verify everything. MITM is always watching.
        ssh_opts = []
        if trust_new_host:
            # Accepts and adds new host keys (use only for first connection)
            ssh_opts.extend(["-o", "StrictHostKeyChecking=accept-new"])
        else:
            # Strict mode: rejects unknown hosts, prevents MITM attacks
            ssh_opts.extend(["-o", "StrictHostKeyChecking=yes"])

        # Local-host bypass: run directly without SSH so there's no dependency
        # on an SSH client being installed, and Windows-native commands work.
        if is_local_host(server_config):
            # Only forward an explicit timeout, so the default call shape is unchanged.
            extra = {} if timeout is None else {"timeout": timeout}
            return self.execute_local(command, capture_output=capture_output, **extra)

        _ssh_bin = _resolve_ssh_bin()

        ssh_args = [_ssh_bin, *ssh_opts]

        # Add other SSH options
        ssh_args.extend(["-o", "ConnectTimeout=10"])
        # BatchMode=yes prevents interactive prompts (host-key confirmation,
        # password) from blocking indefinitely when stdin is closed or
        # redirected — critical for background/agent-spawned deploys.
        ssh_args.extend(["-o", "BatchMode=yes"])

        # Add port if not default
        if server_config.get("port", 22) != 22:
            ssh_args.extend(["-p", str(server_config["port"])])

        # Add SSH key if specified
        # void: keys over passwords. always. encryption is the only privacy we have left.
        if server_config.get("ssh_key"):
            ssh_args.extend(["-i", server_config["ssh_key"]])

        user, host = _require_server_identity(server_config)

        # Add user@host
        ssh_args.append(f"{user}@{host}")

        # Add the command
        ssh_args.append(command)

        # Pre-flight: warn on mDNS .local hosts (unreliable on Windows)
        if host.endswith(".local") and os.name == "nt":
            import warnings

            warnings.warn(
                f"Host '{host}' uses mDNS (.local) which can be slow or "
                "unreliable on Windows. Consider using the IP address directly.",
                stacklevel=3,
            )

        # Execute
        # void: every command leaves a trace. in logs. in memory. in bash_history.
        limit = _SSH_TIMEOUT if timeout is None else timeout
        try:
            if capture_output:
                # UTF-8, not text=True: the bytes came from the REMOTE host, and the
                # local ANSI code page has nothing to do with them. Every server navig
                # targets is UTF-8, which is why the sibling SSH executor
                # (`core.connection.SSHConnection.run`) already names it; this one
                # decoded a Linux host's output with cp1251 on a Russian-locale Windows.
                # errors="replace" covers a legacy remote with an undeclared encoding.
                # Deliberately NOT decode_console_result: its fallback is the *local*
                # console page, which is meaningless for output produced elsewhere.
                result = subprocess.run(
                    ssh_args,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=limit,
                )
            else:
                result = subprocess.run(ssh_args, timeout=limit)
        except subprocess.TimeoutExpired as _exc:
            raise RuntimeError(
                f"SSH connection timed out after {limit}s — "
                f"'{host}' is unreachable or not responding.\n"
                f"Tip: set NAVIG_SSH_TIMEOUT=<seconds> to change the limit, "
                f"or use the IP address instead of a hostname."
            ) from _exc

        return result

    def execute_local(
        self,
        command: str,
        capture_output: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        """Execute a command on the *local* machine (no SSH).  Platform-aware.

        Uses the platform-native default shell (ComSpec on Windows, /bin/sh on
        POSIX) to avoid hard dependencies on specific shell binaries.

        ``timeout`` defaults to the process-wide ``NAVIG_SSH_TIMEOUT`` limit.

        Captured as BYTES and decoded afterwards rather than with ``text=True``: this runs
        an arbitrary user command, the shell passes its child's bytes through untouched, and
        no single codec is right for both a UTF-8-emitting tool and a console tool. See
        :func:`navig.core.proc_text.decode_console_result`.
        """
        limit = _SSH_TIMEOUT if timeout is None else timeout
        try:
            return decode_console_result(
                subprocess.run(  # noqa: S602
                    command,
                    shell=True,
                    capture_output=capture_output,
                    timeout=limit,
                )
            )
        except subprocess.TimeoutExpired as _exc:
            raise RuntimeError(f"Local command timed out after {limit}s") from _exc

    def _ssh_base_args(self, server_config: dict[str, Any]) -> list[str]:
        """SSH argv up to ``user@host`` (no command) — mirrors execute_command's
        connection posture so exec-channel transfers behave identically."""
        if coerce_bool(server_config.get("trust_new_host"), default=False):
            opts = ["-o", "StrictHostKeyChecking=accept-new"]
        else:
            opts = ["-o", "StrictHostKeyChecking=yes"]
        args = [_resolve_ssh_bin(), *opts, "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
        if server_config.get("port", 22) != 22:
            args.extend(["-p", str(server_config["port"])])
        if server_config.get("ssh_key"):
            args.extend(["-i", server_config["ssh_key"]])
        user, host = _require_server_identity(server_config)
        args.append(f"{user}@{host}")
        return args

    def upload_file(
        self, local_path: Path, remote_path: str, server_config: dict[str, Any]
    ) -> bool:
        """Upload a file to the remote host.

        Transport order (``NAVIG_TRANSFER_MODE`` = auto | scp | exec, default
        auto): try SCP, and on any failure fall back to the SSH **exec channel**
        (base64 over stdin). This keeps transfers working on servers where SCP is
        broken but ``ssh`` exec works. Local hosts copy directly.
        """
        if is_local_host(server_config):
            try:
                shutil.copyfile(str(local_path), remote_path)
                return True
            except OSError:
                return False

        mode = os.environ.get("NAVIG_TRANSFER_MODE", "auto").strip().lower()
        if mode != "exec":
            try:
                if self._scp_upload(local_path, remote_path, server_config):
                    return True
            except Exception:  # noqa: BLE001 — scp failure → fall back to exec
                pass
            if mode == "scp":
                return False
        return self._exec_upload(local_path, remote_path, server_config)

    def _scp_upload(
        self, local_path: Path, remote_path: str, server_config: dict[str, Any]
    ) -> bool:
        """Upload via the scp binary (the fast native path)."""
        scp_args = [_resolve_scp_bin()]
        if coerce_bool(server_config.get("trust_new_host"), default=False):
            scp_args.extend(["-o", "StrictHostKeyChecking=accept-new"])
        else:
            scp_args.extend(["-o", "StrictHostKeyChecking=yes"])
        if server_config.get("port", 22) != 22:
            scp_args.extend(["-P", str(server_config["port"])])
        if server_config.get("ssh_key"):
            scp_args.extend(["-i", server_config["ssh_key"]])
        user, host = _require_server_identity(server_config)
        scp_args.append(str(local_path))
        scp_args.append(f"{user}@{host}:{remote_path}")
        try:
            result = subprocess.run(scp_args, timeout=_SSH_TIMEOUT)
        except subprocess.TimeoutExpired as _exc:
            raise RuntimeError(
                f"SCP upload timed out after {_SSH_TIMEOUT}s — "
                f"host '{host}' is unreachable."
            ) from _exc
        return result.returncode == 0

    def is_windows_target(self, server_config: dict[str, Any]) -> bool:
        """Probe the remote shell once per host: ``ver`` succeeds only on Windows.

        Cached per (host, port, user) — file transfers run several round trips
        and the probe itself costs an SSH connection (~1s). Also used by the
        ``navig file`` guards to tell a real ``C:\\`` path from MSYS mangling.
        """
        key = (
            server_config.get("host"),
            server_config.get("port", 22),
            server_config.get("user"),
        )
        cached = _WIN_PROBE_CACHE.get(key)
        if cached is not None:
            return cached
        windows: bool
        try:
            probe = self.execute_command("ver", server_config)
            windows = probe.returncode == 0 and "windows" in (probe.stdout or "").lower()
        except Exception:  # noqa: BLE001 — unreachable host: assume POSIX (old behavior)
            windows = False
        _WIN_PROBE_CACHE[key] = windows
        return windows

    def target_platform(self, server_config: dict[str, Any]) -> str:
        """Return the remote host's OS family: ``windows``, ``macos``, ``linux`` or ``unknown``.

        Callers that pick a command per OS (service logs, service restarts) cannot assume
        Linux: a macOS host answers ``launchctl`` and ``log show``, and a Windows host
        answers PowerShell — none of them have ``journalctl``. The probe is one SSH round
        trip, cached per host like :meth:`is_windows_target`.

        A local host is answered without a probe, from the OS this process runs on.
        """
        if is_local_host(server_config):
            from navig.platform import capabilities  # noqa: PLC0415

            name = capabilities.os_name()
            return "windows" if name == "windows" else ("macos" if name == "macos" else "linux")

        key = (
            server_config.get("host"),
            server_config.get("port", 22),
            server_config.get("user"),
        )
        cached = _TARGET_OS_CACHE.get(key)
        if cached is not None:
            return cached

        platform_name = "unknown"
        try:
            probe = self.execute_command("uname -s", server_config)
            if probe.returncode == 0:
                kernel = (probe.stdout or "").strip().lower()
                if kernel.startswith("darwin"):
                    platform_name = "macos"
                elif kernel.startswith("linux"):
                    platform_name = "linux"
                elif kernel:
                    platform_name = "unknown"
            elif self.is_windows_target(server_config):
                platform_name = "windows"
        except Exception:  # noqa: BLE001 — unreachable host: say so instead of guessing Linux
            platform_name = "unknown"

        _TARGET_OS_CACHE[key] = platform_name
        return platform_name

    @staticmethod
    def _ps_quote(path: str) -> str:
        """Quote a path for a PowerShell single-quoted literal ('' escapes ')."""
        return path.replace("'", "''")

    def _exec_upload(
        self, local_path: Path, remote_path: str, server_config: dict[str, Any]
    ) -> bool:
        """Upload over the SSH exec channel: base64 the bytes locally, pipe them
        to a remote decoder via stdin (unbounded, so no argv length limit), then
        verify the SHA-256 round-trips.

        POSIX targets decode with coreutils ``base64 -d``; Windows targets with
        .NET ``[Convert]::FromBase64String`` — Windows OpenSSH's default shell
        has no base64, and the one-liner deliberately contains NO ``$`` so it
        parses identically under cmd.exe and PowerShell as the DefaultShell.
        """
        try:
            data = Path(local_path).read_bytes()
        except OSError:
            return False
        payload = base64.b64encode(data)
        want = hashlib.sha256(data).hexdigest()

        if self.is_windows_target(server_config):
            ps_path = self._ps_quote(remote_path)
            decode_cmd = (
                "powershell -NoProfile -Command "
                f"\"[IO.File]::WriteAllBytes('{ps_path}',"
                "[Convert]::FromBase64String([Console]::In.ReadToEnd()))\""
            )
            verify_cmd = (
                "powershell -NoProfile -Command "
                f"\"(Get-FileHash -Algorithm SHA256 '{ps_path}').Hash\""
            )
        else:
            rq = shlex.quote(remote_path)
            # base64 -d (coreutils) is universal; GNU also accepts -d. BSD's
            # `base64 -D` fallback is unnecessary because of the GNU form.
            decode_cmd = f"base64 -d > {rq}"
            verify_cmd = f"sha256sum {rq} 2>/dev/null || shasum -a 256 {rq} 2>/dev/null"

        try:
            result = subprocess.run(
                self._ssh_base_args(server_config) + [decode_cmd],
                input=payload,
                capture_output=True,
                timeout=_SSH_TIMEOUT,
            )
        except subprocess.TimeoutExpired as _exc:
            raise RuntimeError(
                f"Exec-channel upload timed out after {_SSH_TIMEOUT}s."
            ) from _exc
        if result.returncode != 0:
            return False
        # Integrity check — confirm the remote bytes match (best-effort).
        verify = self.execute_command(verify_cmd, server_config)
        got = (verify.stdout or "").split()
        if got and got[0].lower() != want:
            return False
        return True

    def execute_command_parallel(
        self,
        command: str,
        host_names: list[str],
        timeout: int = 30,
        max_workers: int = 10,
    ) -> list[dict]:
        """Run command on multiple hosts concurrently.

        Returns list of dicts: {host, stdout, stderr, returncode, latency_ms, error}.
        Never raises — errors are captured per-host so one failure does not abort the rest.
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _run_one(host_name: str) -> dict:
            t0 = time.monotonic()
            try:
                host_config = self.config.load_host_config(host_name)
                result = self.execute_command(command, host_config)
                return {
                    "host": host_name,
                    "stdout": result.stdout or "",
                    "stderr": result.stderr or "",
                    "returncode": result.returncode,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                    "error": None,
                }
            except Exception as exc:
                return {
                    "host": host_name,
                    "stdout": "",
                    "stderr": "",
                    "returncode": -1,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                    "error": str(exc),
                }

        if not host_names:
            return []
        results: list[dict] = []
        effective_workers = min(max_workers, len(host_names))
        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = {pool.submit(_run_one, h): h for h in host_names}
            for fut in as_completed(futures):
                results.append(fut.result())
        return results

    def download_file(
        self, remote_path: str, local_path: Path, server_config: dict[str, Any]
    ) -> bool:
        """Download a file from the remote host. Same transport order as
        upload_file (``NAVIG_TRANSFER_MODE``): SCP, falling back to the SSH exec
        channel (base64 over stdout) when SCP fails."""
        if is_local_host(server_config):
            try:
                shutil.copyfile(remote_path, str(local_path))
                return True
            except OSError:
                return False

        mode = os.environ.get("NAVIG_TRANSFER_MODE", "auto").strip().lower()
        if mode != "exec":
            try:
                if self._scp_download(remote_path, local_path, server_config):
                    return True
            except Exception:  # noqa: BLE001 — scp failure → fall back to exec
                pass
            if mode == "scp":
                return False
        return self._exec_download(remote_path, local_path, server_config)

    def _scp_download(
        self, remote_path: str, local_path: Path, server_config: dict[str, Any]
    ) -> bool:
        scp_args = [_resolve_scp_bin()]
        if coerce_bool(server_config.get("trust_new_host"), default=False):
            scp_args.extend(["-o", "StrictHostKeyChecking=accept-new"])
        else:
            scp_args.extend(["-o", "StrictHostKeyChecking=yes"])
        if server_config.get("port", 22) != 22:
            scp_args.extend(["-P", str(server_config["port"])])
        if server_config.get("ssh_key"):
            scp_args.extend(["-i", server_config["ssh_key"]])
        user, host = _require_server_identity(server_config)
        scp_args.append(f"{user}@{host}:{remote_path}")
        scp_args.append(str(local_path))
        try:
            result = subprocess.run(scp_args, timeout=_SSH_TIMEOUT)
        except subprocess.TimeoutExpired as _exc:
            raise RuntimeError(
                f"SCP download timed out after {_SSH_TIMEOUT}s — "
                f"host '{host}' is unreachable."
            ) from _exc
        return result.returncode == 0

    def _exec_download(
        self, remote_path: str, local_path: Path, server_config: dict[str, Any]
    ) -> bool:
        """Download over the SSH exec channel: ``base64 <path>`` on the remote,
        capture stdout, decode locally. Windows targets use
        ``[Convert]::ToBase64String`` (no coreutils there); PowerShell emits the
        result as one unwrapped line, exactly like coreutils base64."""
        if self.is_windows_target(server_config):
            ps_path = self._ps_quote(remote_path)
            cmd = (
                "powershell -NoProfile -Command "
                f"\"[Convert]::ToBase64String([IO.File]::ReadAllBytes('{ps_path}'))\""
            )
        else:
            cmd = f"base64 {shlex.quote(remote_path)}"
        result = self.execute_command(cmd, server_config)
        if result.returncode != 0:
            return False
        try:
            raw = base64.b64decode((result.stdout or "").encode("ascii", "ignore"))
        except ValueError:  # binascii.Error subclasses ValueError
            return False
        try:
            Path(local_path).write_bytes(raw)
        except OSError:
            return False
        return True
