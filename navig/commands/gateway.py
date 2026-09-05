"""
NAVIG Gateway CLI Commands

Commands for managing the autonomous agent gateway server.
"""

from typing import Any

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")
try:
    from loguru import logger as _logger
except ImportError:
    import logging as _log

    _logger = _log.getLogger(__name__)

_GW_REQUEST_TIMEOUT: int = 5  # Default timeout for all gateway HTTP/subprocess calls


def _gw_base_url() -> str:
    """Return the local gateway base URL from config (gateway.port / gateway.host)."""
    from navig.gateway_client import gateway_base_url

    return gateway_base_url()


def _gateway_request_headers() -> dict[str, str]:
    """Return auth headers for gateway admin requests when configured."""
    from navig.gateway_client import gateway_request_headers

    return gateway_request_headers()



def _unwrap(body):
    """Payload out of the gateway's ``json_ok`` envelope ({"ok":…,"data":…,"error":…}).

    Reading a field straight off ``response.json()`` always misses — the payload is one
    level down — and a miss looks exactly like "the daemon has nothing to report". That
    emptied the whole flux (#713) and cron (#714) surfaces. Lazily imported to keep CLI
    startup fast.

    NOT for error bodies: ``envelope_error`` puts the message at the TOP level ``error``
    key with ``data: None``, so an error-extraction helper must read the raw body.
    """
    from navig.gateway_client import unwrap_envelope

    return unwrap_envelope(body)


def _gw_request(method: str, path: str, **kwargs):
    """Send an authenticated request to the local gateway."""
    from navig.gateway_client import gateway_request

    return gateway_request(method, path, **kwargs)


def _gw_api(
    method: str,
    path: str,
    *,
    action: str,
    json_body: dict[str, Any] | None = None,
    timeout: float = _GW_REQUEST_TIMEOUT,
    expect_payload: bool = True,
    not_found: str | None = None,
    unavailable: str | None = None,
) -> dict[str, Any]:
    """Call the gateway API, or exit non-zero saying why.

    Sixteen commands in this module carried their own copy of this block — import
    requests, call, check for 200, branch on 404/503, catch ConnectionError — and
    **not one failure branch exited non-zero**. 42 such paths. The worst was the
    approval gate: ``navig approve yes <id>`` for a request that does not exist
    printed "✗ Request … not found" and exited **0**, so
    ``navig approve yes bogus && <proceed>`` proceeded having approved nothing.

    Three inconsistencies the copies had drifted into are settled here:

    * **"Gateway is not running" was a ``ch.warning`` at exit 0** in 11 copies. For
      anything but a ``*_status`` command that is the doctor-honesty bug in CLI form:
      "I could not look" rendered as an answer. ``queue list`` against a dead gateway
      printed a warning and exited 0, indistinguishable from a genuinely empty queue.
      The ``*_status`` commands keep their warning + exit 0 — there, "not running" IS
      the verified answer, and they do not use this helper.
    * ``gateway_session`` sniffed ``"ConnectionError" in str(type(e).__name__)``, which
      also matches any unrelated class whose name merely contains the word and misses a
      subclass that does not. Matched on the exception TYPE here.
    * The 404 message was hand-written per command; pass ``not_found`` and it becomes
      an ``Exit(2)`` (the usage class — the caller named something nonexistent).

    Returns the unwrapped payload; ``{}`` when *expect_payload* is False.
    """
    try:
        import requests  # noqa: PLC0415 — lazy: keeps `navig --help` fast
    except ImportError as exc:
        ch.error(f"Cannot {action}: the 'requests' package is not installed")
        ch.info("Install with: pip install requests")
        raise typer.Exit(1) from exc

    kwargs: dict[str, Any] = {"timeout": timeout}
    if json_body is not None:
        kwargs["json"] = json_body
    try:
        response = _gw_request(method, path, **kwargs)
    except requests.exceptions.ConnectionError as exc:
        ch.error(f"Cannot {action}: the gateway is not running")
        ch.info("Start it with: navig gateway start")
        raise typer.Exit(1) from exc
    except requests.exceptions.Timeout as exc:
        ch.error(f"Cannot {action}: the gateway did not answer within {timeout:g}s")
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001 — reported with its type, then exits
        ch.error(f"Cannot {action}: {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc

    if response.status_code == 404 and not_found:
        ch.error(not_found)
        raise typer.Exit(2)
    if response.status_code == 503:
        ch.error(unavailable or f"Cannot {action}: that gateway module is not available")
        raise typer.Exit(1)
    if response.status_code != 200:
        ch.error(f"Failed to {action}: HTTP {response.status_code}")
        detail = (response.text or "").strip()
        if detail:
            ch.info(f"  {detail[:300]}")
        raise typer.Exit(1)

    if not expect_payload:
        return {}

    try:
        payload = _unwrap(response.json())
    except Exception as exc:  # noqa: BLE001 — a 200 we cannot read is not a success
        # Returning {} would render as "No pending requests" for a gateway that
        # actually answered with something unreadable.
        ch.error(f"Cannot {action}: the gateway sent a response that could not be read")
        raise typer.Exit(1) from exc
    return payload if isinstance(payload, dict) else {"data": payload}


def _load_gateway_cli_defaults() -> tuple[int, str]:
    """Return gateway port/host from config with stable CLI fallbacks."""
    from navig.gateway_client import gateway_cli_defaults

    return gateway_cli_defaults()


def _port_holders(port: int) -> list[int]:
    """PIDs listening on *port* (excluding us). Best-effort; empty on any failure."""
    import os
    import subprocess
    import sys

    from navig.core.proc_text import console_encoding

    pids: list[int] = []
    try:
        import psutil  # type: ignore[import-untyped]

        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port == port and conn.status in ("LISTEN", "ESTABLISHED"):
                if conn.pid and conn.pid != os.getpid():
                    pids.append(conn.pid)
    except Exception:
        # Fallback: platform-specific subprocess
        try:
            if sys.platform == "win32":
                out = subprocess.check_output(
                    ["netstat", "-ano"],
                    encoding=console_encoding(),
                    errors="replace",
                    stderr=subprocess.DEVNULL,
                )
                # Match on COLUMNS, not on the word "LISTENING": that word is localized
                # (Russian Windows prints ПРОСЛУШИВАНИЕ), so a substring test finds nothing
                # on a non-English install and the port holder silently becomes "nobody".
                # Columns are positional and locale-independent:
                #   TCP  0.0.0.0:135  0.0.0.0:0  <state>  2300
                # Testing the LOCAL address column is also stricter: the old
                # `f":{port} " in line` matched the FOREIGN column too, and was saved from
                # that only by the "LISTENING" conjunct being false for such rows.
                # Accepting any state matches the psutil branch above, which takes LISTEN
                # and ESTABLISHED alike — both are processes occupying the port.
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) < 4 or not parts[0].upper().startswith("TCP"):
                        continue
                    if parts[1].rsplit(":", 1)[-1] != str(port):
                        continue
                    try:
                        pid = int(parts[-1])
                    except ValueError:
                        continue
                    if pid != os.getpid():
                        pids.append(pid)
            else:
                out = subprocess.check_output(
                    ["lsof", "-ti", f"tcp:{port}"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                for p in out.split():
                    try:
                        pid = int(p.strip())
                        if pid != os.getpid():
                            pids.append(pid)
                    except ValueError:
                        pass
        except Exception:
            pass

    return sorted(set(pids))


def _free_port(
    port: int,
    *,
    holders_reader=None,
    config_dir_reader=None,
    killer=None,
) -> list[int]:
    """Free *port* by killing ONLY our own stale gateway. Returns the PIDs killed.

    This is the SECOND kill path in the start sequence, and #173 only scoped the first
    one. It used to ``taskkill /F`` **whatever** was listening on the port, with no
    identity check at all — so a gateway started from any other navig (a test, a smoke
    run, a second venv) that happened to resolve to the same port force-killed the
    operator's **live production daemon**, exactly the bug ``_supersede_other_gateways``
    was scoped to prevent. It also meant an unrelated app holding port 8789 would simply
    be executed.

    The rule is the one ``config_dir_of`` already states: a process whose config dir we
    cannot read is **not ours**, and you must never kill what you cannot identify. So:

      * same config dir  → our own stale instance; kill it (this is the whole point).
      * other config dir → a different brain. Leave it.
      * unreadable       → unknown. Leave it.
      * not a navig proc → someone else's port. Leave it.

    Refusing to kill is safe: the server's bind self-heals onto a free port and writes
    the real one to ``gateway.json``, which every client resolves through. A port we
    could not free costs one ephemeral port; a port we freed by shooting the operator's
    brain costs them their bot.
    """
    from navig.daemon.single_instance import config_dir_of
    from navig.platform import paths

    holders = (holders_reader or _port_holders)(port)
    if not holders:
        return []

    read_dir = config_dir_reader or config_dir_of
    kill = killer or _kill_pid
    try:
        ours = paths.config_dir().resolve()
    except Exception:  # noqa: BLE001 — cannot identify ourselves → kill nothing
        return []

    killed: list[int] = []
    for pid in holders:
        theirs = read_dir(pid)
        if theirs is None:
            _logger.warning(
                "Port %d is held by PID %d, which we cannot identify — NOT killing it. "
                "The gateway will bind to a free port instead.",
                port, pid,
            )
            continue
        if theirs != ours:
            _logger.info(
                "Port %d is held by PID %d from a DIFFERENT config dir (%s) — that is "
                "another brain, leaving it alone. Binding elsewhere.",
                port, pid, theirs,
            )
            continue
        kill(pid)
        killed.append(pid)
        _logger.info("Freed port %d — superseded our own stale gateway (PID %d)", port, pid)

    return killed


def _kill_pid(pid: int) -> None:
    """Force-kill *pid* (best-effort, cross-platform)."""
    import os
    import signal
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            subprocess.call(
                ["taskkill", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass


def _supersede_other_gateways() -> None:
    """Guarantee this is the ONLY gateway **for this config dir**: kill every other
    gateway process bound to the same brain (a stale instance keeps serving OLD cached
    code even after a code change).

    Scoped by config dir, and that scoping is load-bearing. Unscoped, this sweep matched
    on cmdline alone and force-killed matching processes machine-wide — so a gateway
    started from ANY other navig (a second venv, a CI job, a temp-config smoke test) took
    down the operator's live production daemon. "Never boot a second gateway locally"
    became a standing rule precisely because of this. A gateway on a *different* config
    dir is a different brain; leave it alone.

    Never touches the current process or its ancestors (the supervisor that spawned us,
    NSSM, the launching shell), so it is safe even when the gateway runs as a supervised
    child. Complements ``_free_port`` (which only catches an instance already bound to
    the port — not one mid-startup or on another port).
    """
    try:
        from navig.daemon.single_instance import GATEWAY_PATTERNS, kill_other_instances
        from navig.platform import paths

        killed = kill_other_instances(GATEWAY_PATTERNS, config_dir=paths.config_dir())
        if killed:
            _logger.info("Superseded %d stale gateway process(es): %s", len(killed), killed)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("gateway supersede sweep skipped: %s", exc)


def _write_gateway_pid() -> None:
    """Record this gateway's PID so ``navig gateway stop`` can find it."""
    try:
        import os

        from navig.platform import paths

        pid_file = paths.config_dir() / "gateway.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _logger.debug("could not write gateway.pid: %s", exc)


gateway_app = typer.Typer(
    name="gateway",
    help="Manage the autonomous agent gateway",
    no_args_is_help=True,
)


@gateway_app.command("start")
def gateway_start(
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to run gateway on (default: gateway.port from config, fallback 8789)",
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        help="Host to bind to (default: gateway.host from config, fallback 127.0.0.1)",
    ),
    background: bool = typer.Option(False, "--background", "-b", help="Run in background"),
    logs: bool = typer.Option(False, "--logs", "-l", help="Stream gateway logs to stdout"),
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Raw verbose log stream (no formatted boot story). DEBUG level.",
    ),
):
    """
    Start the autonomous agent gateway server.

    The gateway provides:
    - HTTP/WebSocket API for agent communication
    - Session persistence across restarts
    - Heartbeat-based health monitoring
    - Cron job scheduling
    - Multi-channel message routing

    By default boot is shown as a clean, formatted "story" (one styled line per
    subsystem) and the raw INFO log chatter is kept off the console (it still
    goes to ~/.navig/navig.log). Pass --debug for the old behaviour: the full,
    unformatted DEBUG log stream with no narrator.

    Examples:
        navig gateway start
        navig gateway start --port 9000
        navig gateway start --background
        navig gateway start --debug      # raw verbose logs, like before
    """
    import asyncio

    # Fill port/host from config if not explicitly passed
    default_port, default_host = _load_gateway_cli_defaults()
    if port is None:
        port = default_port
    if host is None:
        host = default_host

    ch.info(f"Starting NAVIG Gateway on {host}:{port}...")
    # Single-instance: free the port AND supersede any other gateway/bot process,
    # so a fresh start always wins and never leaves a stale instance serving old code.
    _free_port(port)
    _supersede_other_gateways()
    _write_gateway_pid()

    # Boot presentation: formatted narrator story by default, raw logs on --debug.
    import logging as _logging
    import os as _os

    from navig.core import narrator as _narrator
    from navig.core.logging import get_logger as _get_logger

    _get_logger("gateway")  # force root-logger config before we tweak handlers
    _navig_root = _logging.getLogger("navig")
    _console_handlers = [
        _h
        for _h in _navig_root.handlers
        if isinstance(_h, _logging.StreamHandler) and not isinstance(_h, _logging.FileHandler)
    ]
    if debug:
        # Old behaviour: narrator off, full DEBUG stream on the console.
        _os.environ["NAVIG_NO_NARRATOR"] = "1"
        _navig_root.setLevel(_logging.DEBUG)
        for _h in _console_handlers:
            _h.setLevel(_logging.DEBUG)
        ch.dim("Debug mode: narrator off, full log stream enabled.")
    elif _narrator.is_active():
        # Formatted boot on a TTY: hide the INFO preamble now so the styled
        # boot story stands alone. server.start() restores console verbosity
        # to INFO after the banner. (Piped/systemd: narrator is silent, so we
        # leave the full log stream intact.)
        for _h in _console_handlers:
            _h.setLevel(_logging.WARNING)

    if logs:
        import logging as _logging
        _stream_handler = _logging.StreamHandler()
        _stream_handler.setLevel(_logging.DEBUG)
        _stream_handler.setFormatter(
            _logging.Formatter(  # noqa: E501
                "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        _logging.root.setLevel(_logging.DEBUG)
        _logging.root.addHandler(_stream_handler)
        ch.dim("Log streaming enabled (all gateway + daemon output).")

    try:
        from navig.gateway import GatewayConfig, NavigGateway

        # Build config dict for GatewayConfig
        raw_config = {
            "gateway": {
                "enabled": True,
                "port": port,
                "host": host,
            }
        }

        gateway_config = GatewayConfig(raw_config)
        gateway = NavigGateway(config=gateway_config)

        if background:
            ch.warning("Background mode not yet implemented. Running in foreground.")

        asyncio.run(gateway.start())

    except KeyboardInterrupt:
        # Ctrl+C on a foreground gateway is how you stop it — not a failure.
        ch.info("Gateway stopped by user")
    except ImportError as e:
        ch.error(f"Cannot start the gateway, missing dependency: {e}")
        ch.info("Install with: pip install aiohttp")
        raise typer.Exit(1) from e
    except Exception as e:
        # A gateway that failed to start must not report success: `navig gateway start
        # && navig bot status` would otherwise query a daemon that never came up.
        ch.error(f"Gateway error: {type(e).__name__}: {e}")
        raise typer.Exit(1) from e


@gateway_app.command("restart")
def gateway_restart(
    admin: bool = typer.Option(
        False,
        "--admin",
        "-A",
        help="Relaunch elevated (UAC on Windows, sudo on POSIX) — needed when the "
        "daemon is running as Administrator.",
    ),
):
    """Restart the NAVIG daemon/gateway (alias for `navig service restart`).

    Use this after upgrading or changing config so the running daemon picks up
    the new code/config (the CLI already runs fresh code; the daemon does not).
    """
    from navig.commands.service import service_restart

    service_restart(admin=admin)


@gateway_app.command("stop")
def gateway_stop():
    """
    Stop the running gateway server.

    Sends a shutdown signal to the running gateway via its API.
    If the gateway is running in the foreground, use Ctrl+C instead.

    Examples:
        navig gateway stop
    """
    # Helper: try to stop a stray gateway process via PID file
    def _try_kill_by_pid() -> bool:
        """Stop the gateway recorded in ``gateway.pid`` — if that PID is still OUR gateway.

        This reached for the wrong process three separate ways:

          * It read a HARDCODED ``~/.navig/gateway.pid`` while :func:`_write_gateway_pid`
            writes ``paths.config_dir()/"gateway.pid"``. Under ``NAVIG_CONFIG_DIR`` those are
            different files, so `navig gateway stop` against a second brain read the
            OPERATOR'S pid and ``taskkill /F``'d their live daemon — the same "there is only
            one navig, at ~/.navig" assumption that #173 and the ``_free_port`` fix each had
            to close through a different door — while failing to stop the gateway it was
            actually asked to stop.
          * Two of its three candidate paths (``~/.navig/run/gateway.pid``,
            ``/tmp/navig-gateway.pid``) are written by NOTHING in this codebase. On POSIX
            ``/tmp`` is world-writable, so any local user could plant a number there and have
            us kill it.
          * It never checked identity, so a stale file whose PID had been recycled named a
            stranger — and the number went straight to ``taskkill /F``.

        Now: read the file the writer actually writes, require the PID to still belong to the
        process that wrote it, and require that process to be a navig bound to OUR config dir.
        Anything unproven returns False and the caller reports "not running", which costs
        nothing — whereas killing the wrong PID costs a live process.
        """
        import sys

        from navig.daemon.single_instance import config_dir_of, pid_from_pidfile
        from navig.platform import paths

        try:
            ours = paths.config_dir().resolve()
        except Exception:  # noqa: BLE001
            return False

        pid_file = ours / "gateway.pid"
        pid = pid_from_pidfile(pid_file)
        if pid is None:
            return False

        theirs = config_dir_of(pid)
        if theirs is None or theirs != ours:
            _logger.warning(
                "gateway stop: pid %s is not our brain (%s != %s) — leaving it alone",
                pid, theirs, ours,
            )
            return False

        try:
            if sys.platform == "win32":
                import subprocess

                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    timeout=_GW_REQUEST_TIMEOUT,
                )
                killed = result.returncode == 0
            else:
                import os
                import signal

                os.kill(pid, signal.SIGTERM)
                killed = True
        except (ProcessLookupError, OSError):
            return False  # it exited between the identity check and the signal

        if killed:
            pid_file.unlink(missing_ok=True)
        return killed

    try:
        import requests

        _base = _gw_base_url()
        # First check if gateway is running
        http_reachable = False
        try:
            health_response = requests.get(
                f"{_base}/health",
                headers=_gateway_request_headers(),
                timeout=2,
            )
            http_reachable = health_response.status_code == 200
        except Exception:  # noqa: BLE001
            pass

        if not http_reachable:
            # Try PID-based kill as fallback (handles daemon-spawned gateway)
            if _try_kill_by_pid():
                ch.success("Gateway stopped")
            else:
                ch.dim("Gateway is not running")
            return

        # Try to stop via API
        try:
            response = requests.post(
                f"{_base}/shutdown",
                headers=_gateway_request_headers(),
                timeout=_GW_REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                ch.success("Gateway shutdown signal sent")
            else:
                # The gateway is still running: `stop` did not do its job, so it must
                # not report success (a script would go on to start a second one).
                ch.error(f"Shutdown request returned HTTP {response.status_code}")
                ch.info("If running in foreground, use Ctrl+C to stop")
                raise typer.Exit(1)
        except requests.exceptions.ConnectionError:
            # Connection closed - the gateway went down. That IS success.
            ch.success("Gateway stopped")
        except typer.Exit:
            raise
        except Exception as e:
            ch.error(f"Could not send the shutdown signal: {type(e).__name__}: {e}")
            ch.info("If running in foreground, use Ctrl+C to stop")
            ch.info("Or kill the process manually: pkill -f 'navig gateway'")
            raise typer.Exit(1) from e

    except ImportError as exc:
        ch.error("Cannot stop the gateway: the 'requests' package is not installed")
        ch.info("Install with: pip install requests")
        raise typer.Exit(1) from exc


@gateway_app.command("status")
def gateway_status(
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """Show config state and liveness for every gateway channel.

    Checks: Telegram · Matrix · Discord · WhatsApp · Email
    Each row shows:  configured | token/key present | daemon/API reachable
    """
    import json as _json
    import socket as _socket

    from navig.core.coerce import coerce_bool

    # ── Load global config ────────────────────────────────────────────────────
    raw_cfg: dict = {}
    try:
        from navig.config import get_config_manager

        # `get_global_config()`, NOT `_load_global_config()`: the latter returns the
        # PYDANTIC-VALIDATED view and keeps only schema-declared fields. Measured
        # against the schema: of `telegram / discord / whatsapp / comms / email /
        # proactive / deploy`, only `telegram` survives -- the other six are dropped
        # silently, so this read saw {} for anything the operator had configured.
        raw_cfg = get_config_manager().get_global_config() or {}
    except Exception:  # noqa: BLE001
        pass

    # ── Gateway daemon check (local HTTP) ─────────────────────────────────────
    def _http_alive(url: str, timeout: float = 2.0, headers: dict[str, str] | None = None) -> bool:
        try:
            import urllib.request

            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status < 500
        except Exception:  # noqa: BLE001
            return False

    def _port_alive(host: str, port: int) -> bool:
        try:
            with _socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    # ── Telegram ──────────────────────────────────────────────────────────────
    tg_cfg = raw_cfg.get("telegram", {})
    _tg_token_raw = tg_cfg.get("bot_token") or ""
    # Also check vault (primary storage when set via `navig vault add telegram`)
    if not _tg_token_raw:
        try:
            from navig.vault.resolver import resolve_secret  # type: ignore[import]
            _tg_token_raw = resolve_secret("TELEGRAM_BOT_TOKEN") or ""
        except Exception:  # noqa: BLE001
            pass
    tg_token = bool(_tg_token_raw)
    tg_users = len(tg_cfg.get("allowed_users", []))
    tg_groups = len(tg_cfg.get("allowed_groups", []))
    tg_online = False
    if tg_token:
        try:
            import json as _j
            import urllib.request

            tok = _tg_token_raw
            with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getMe", timeout=_GW_REQUEST_TIMEOUT) as r:
                tg_online = _j.load(r).get("ok", False)
        except Exception:  # noqa: BLE001
            pass

    # ── Matrix ────────────────────────────────────────────────────────────────
    mx_cfg = (raw_cfg.get("comms") or {}).get("matrix") or raw_cfg.get("matrix") or {}
    mx_token = bool(mx_cfg.get("access_token"))
    mx_hs = mx_cfg.get("homeserver_url", "http://localhost:6167")
    mx_online = _http_alive(mx_hs.rstrip("/") + "/_matrix/client/versions")

    # ── Discord ───────────────────────────────────────────────────────────────
    dc_cfg = raw_cfg.get("discord", {})
    dc_token = bool(dc_cfg.get("bot_token") or dc_cfg.get("token"))
    dc_online = False
    if dc_token:
        dc_online = _http_alive("https://discord.com/api/v10/gateway", timeout=4)

    # ── WhatsApp (mautrix bridge) ─────────────────────────────────────────────
    wa_port = 29318
    wa_running = _port_alive("localhost", wa_port)
    wa_cfg = raw_cfg.get("whatsapp") or raw_cfg.get("bridges", {}).get("whatsapp", {})
    # coerce_bool, not bool(): bool("false") is True, so `config set whatsapp.enabled
    # false` (string) would show WhatsApp as enabled in this status view.
    wa_enabled = coerce_bool(wa_cfg.get("enabled"), default=False) or coerce_bool(
        wa_cfg.get("WHATSAPP_ENABLED"), default=False
    )

    # ── Email / SMTP ──────────────────────────────────────────────────────────
    em_cfg = raw_cfg.get("email") or raw_cfg.get("smtp") or {}
    em_configured = bool(em_cfg.get("smtp_host") or em_cfg.get("SMTP_HOST") or em_cfg.get("host"))
    em_port = int(em_cfg.get("smtp_port") or em_cfg.get("port") or 587)
    em_host = str(em_cfg.get("smtp_host") or em_cfg.get("host") or "")
    em_online = _port_alive(em_host, em_port) if em_host else False

    # ── Daemon/gateway API ────────────────────────────────────────────────────
    # Live-first: the self-healing bind may have landed the gateway off the
    # configured port — probe where it actually listens (127.0.0.1, not
    # `localhost`, to dodge the Windows IPv6-first resolution stall).
    from navig.gateway_client import gateway_live_defaults

    gw_port = gateway_live_defaults()[0]
    gw_live = _http_alive(
        f"http://127.0.0.1:{gw_port}/health",
        headers=_gateway_request_headers(),
    )

    channels = [
        {
            "channel": "Telegram",
            "configured": tg_token,
            "detail": (
                f"users={tg_users}  groups={tg_groups}" if tg_token else "bot_token missing"
            ),
            "reachable": tg_online,
        },
        {
            "channel": "Matrix",
            "configured": mx_token,
            "detail": mx_hs if mx_token else "access_token missing",
            "reachable": mx_online,
        },
        {
            "channel": "Discord",
            "configured": dc_token,
            "detail": "bot_token present" if dc_token else "bot_token missing",
            "reachable": dc_online,
        },
        {
            "channel": "WhatsApp",
            "configured": wa_enabled,
            "detail": f"bridge port {wa_port}" if wa_enabled else "not enabled",
            "reachable": wa_running,
        },
        {
            "channel": "Email/SMTP",
            "configured": em_configured,
            "detail": f"{em_host}:{em_port}" if em_configured else "smtp_host missing",
            "reachable": em_online,
        },
        {
            "channel": "Gateway API",
            "configured": True,
            "detail": f"localhost:{gw_port}",
            "reachable": gw_live,
        },
    ]

    if json_out:
        print(_json.dumps(channels, indent=2))
        return

    ch.console.print("\n[bold]Gateway Channel Status[/bold]\n")
    ch.console.print(f"  {'Channel':<14} {'Config':<10} {'Reachable':<12} {'Detail'}")
    ch.console.print("  " + "─" * 62)

    for c in channels:
        cfg_icon = "[green]✓[/green]" if c["configured"] else "[red]✗[/red]"
        live_icon = "[green]✓[/green]" if c["reachable"] else "[dim]─[/dim]"
        ch.console.print(
            f"  [cyan]{c['channel']:<14}[/cyan] {cfg_icon:<12} {live_icon:<14} [dim]{c['detail']}[/dim]"
        )

    ch.console.print()
    if not gw_live:
        ch.dim("  Gateway daemon not running — start with: navig gateway start")
    else:
        ch.dim(f"  Gateway running on port {gw_port}")


@gateway_app.command("test")
def gateway_test(
    channel: str = typer.Argument(
        "all",
        help="Channel to test (all|telegram|matrix|discord|email)",
    ),
    target: str = typer.Option(
        "",
        "--target",
        "-t",
        help="Target recipient (@username|chat_id for Telegram, room for Matrix, address for email)",
    ),
    message: str = typer.Option(
        "🟢 NAVIG gateway smoke-test — all systems go",
        "--message",
        "-m",
        help="Message text to send during smoke test",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit with code 1 when any tested channel fails",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON summary",
    ),
) -> None:
    """Send a smoke-test message through one or all configured channels.

    Examples::

        navig gateway test telegram --target @username
        navig gateway test telegram --target @username --message "custom text"
        navig gateway test matrix   --target "#alerts:navig.local"
        navig gateway test all      --target @username
    """

    selected_channel = channel.lower()
    channels_to_test = (
        ["telegram", "matrix", "discord", "email"]
        if selected_channel == "all"
        else [selected_channel]
    )

    results: list[dict] = []

    for ch_name in channels_to_test:
        if not json_output:
            ch.console.print(f"\n[bold]Testing [cyan]{ch_name}[/cyan]…[/bold]")

        if ch_name == "telegram":
            if not target:
                if not json_output:
                    ch.warning("  --target required for Telegram (e.g. --target @username)")
                results.append({"channel": "telegram", "ok": False, "reason": "no target"})
                continue
            from navig.commands.telegram import telegram_send as _tg_send

            try:
                _tg_send(
                    target=target,
                    message=message,
                    parse_mode="HTML",
                    resolve_only=False,
                    host="",
                )
                results.append({"channel": "telegram", "ok": True})
            except Exception as exc:
                if not json_output:
                    ch.warning(f"  Telegram test failed: {exc}")
                results.append({"channel": "telegram", "ok": False, "reason": str(exc)})

        elif ch_name == "matrix":
            from navig.commands.matrix import send as _mx_send

            try:
                _mx_send(room=target, message=message, stdin=False, format="text")
                results.append({"channel": "matrix", "ok": True})
            except Exception as exc:
                if not json_output:
                    ch.warning(f"  Matrix test failed: {exc}")
                results.append({"channel": "matrix", "ok": False, "reason": str(exc)})

        elif ch_name == "discord":
            if not json_output:
                ch.dim("  Discord test not yet implemented — verify via Discord Dev Portal.")
            results.append({"channel": "discord", "ok": None, "reason": "not implemented"})

        elif ch_name == "email":
            if not json_output:
                ch.dim("  Email test: run navig email send --to example@domain.com")
            results.append({"channel": "email", "ok": None, "reason": "not implemented"})

        else:
            if not json_output:
                ch.warning(
                    f"  Unknown channel '{ch_name}'. Choices: telegram | matrix | discord | email | all"
                )
            results.append({"channel": ch_name, "ok": False, "reason": "unknown"})

    failed = [r for r in results if r.get("ok") is False]
    if json_output:
        import json as _json

        payload = {
            "results": results,
            "summary": {
                "channels_tested": len(results),
                "failed": len(failed),
                "ok": len(failed) == 0,
            },
        }
        print(_json.dumps(payload, indent=2))
    else:
        ch.console.print("\n[bold]Results[/bold]")
        for r in results:
            icon = (
                "[green]✓[/green]"
                if r["ok"]
                else ("[dim]–[/dim]" if r["ok"] is None else "[red]✗[/red]")
            )
            detail = f"  [dim]{r.get('reason', '')}[/dim]" if r.get("reason") else ""
            ch.console.print(f"  {icon} {r['channel']}{detail}")
        ch.console.print()

    if strict and failed:
        raise typer.Exit(1)


@gateway_app.command("session")
def gateway_session(
    action: str = typer.Argument("list", help="Action: list, show, clear"),
    session_key: str = typer.Argument(None, help="Session key (for show/clear)"),
):
    """
    Manage gateway sessions.

    Examples:
        navig gateway session list
        navig gateway session show agent:default:telegram:123
        navig gateway session clear agent:default:telegram:123
    """
    if action == "list":
        sessions = _gw_api("GET", "/sessions", action="list sessions").get("sessions", [])
        if not sessions:
            # No sessions is a real answer — exit 0.
            ch.info("No active sessions")
            return
        ch.info(f"Active sessions ({len(sessions)}):")
        for s in sessions:
            ch.info(f"  • {s.get('key', 'unknown')}")
        return

    if action == "show" and session_key:
        # GET /sessions/{key} has NEVER existed — only the collection GET /sessions and
        # the memory routes do. The route that serves a single session's contents is
        # GET /memory/history/{session_key}.
        session = _gw_api(
            "GET",
            f"/memory/history/{session_key}",
            action=f"show session {session_key}",
            not_found=f"Session not found: {session_key}",
        )
        ch.info(f"Session: {session_key}")
        ch.console.print_json(data=session)
        return

    if action == "clear" and session_key:
        # Same dead path: DELETE /sessions/{key} does not exist.
        # DELETE /memory/sessions/{session_key} is the real one.
        _gw_api(
            "DELETE",
            f"/memory/sessions/{session_key}",
            action=f"clear session {session_key}",
            expect_payload=False,
            not_found=f"Session not found: {session_key}",
        )
        ch.success(f"Session cleared: {session_key}")
        return

    ch.error("Invalid action or missing session_key")
    ch.info("Usage: navig gateway session list|show|clear [session_key]")
    raise typer.Exit(2)


# ============================================================================
# Interactive Menu Wrapper Functions
# ============================================================================
# These functions provide a consistent interface for the interactive menu system.
# Each wrapper calls the underlying Typer command with appropriate defaults.


def status_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for gateway status command (interactive menu)."""
    gateway_status()


def start_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for gateway start command (interactive menu)."""
    # Start in foreground mode for interactive use — port/host come from config
    gateway_start(port=None, host=None, background=False)


def stop_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for gateway stop command (interactive menu)."""
    gateway_stop()


def session_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for gateway session list command (interactive menu)."""
    gateway_session(action="list", session_key=None)


# ============================================================================
# BOT - TELEGRAM BOT LAUNCHER
# ============================================================================

bot_app = typer.Typer(
    help="Telegram bot and multi-channel agent launcher",
    invoke_without_command=True,
    no_args_is_help=False,
)


@bot_app.callback()
def bot_callback(ctx: typer.Context):
    """Bot commands - run without subcommand to start bot."""
    if ctx.invoked_subcommand is None:
        # Default action: start bot in direct mode
        ctx.invoke(bot_start)


@bot_app.command("start")
def bot_start(
    gateway: bool = typer.Option(
        False, "--gateway", "-g", help="Start with gateway (session persistence)"
    ),
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Gateway port (default: gateway.port from config, fallback 8789)",
    ),
    background: bool = typer.Option(False, "--background", "-b", help="Run in background"),
):
    """
    Start the NAVIG Telegram bot.

    By default runs in direct mode (standalone).
    Use --gateway to start both gateway and bot together.

    Examples:
        navig bot                    # Start bot (direct mode)
        navig bot --gateway          # Start gateway + bot together
        navig bot -g -p 9000         # Gateway on custom port
    """
    import os
    import subprocess
    import sys

    # Check for telegram token (vault-first, env/config fallback)
    from navig.messaging.secrets import resolve_telegram_bot_token

    telegram_token = resolve_telegram_bot_token()
    if not telegram_token:
        ch.error("TELEGRAM_BOT_TOKEN not set!")
        ch.info("  Get token from @BotFather on Telegram")
        ch.info("  Add to .env file: TELEGRAM_BOT_TOKEN=your-token")
        raise typer.Exit(1)

    if gateway:
        if port is None:
            port, _host = _load_gateway_cli_defaults()
        ch.info("Starting NAVIG with Gateway + Telegram Bot...")
        ch.info(f"  Gateway: http://localhost:{port}")
        ch.info("  Bot: Telegram")
        cmd = [
            sys.executable,
            "-m",
            "navig.daemon.telegram_worker",
            "--port",
            str(port),
        ]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)
    else:
        ch.info("Starting NAVIG Telegram Bot (direct mode)...")
        ch.warning("⚠️  Conversations reset on bot restart")
        ch.info("   Use 'navig bot --gateway' for session persistence")
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--no-gateway"]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)


@bot_app.command("status")
def bot_status():
    """Check whether OUR bot/gateway (this config dir) is running.

    Scoped by ``NAVIG_CONFIG_DIR`` — a daemon/gateway/worker belonging to a
    *different* brain on the same machine is not reported as ours (nor, in
    ``bot stop``, killed). See ``single_instance`` / the gateway supersede guard.
    """
    try:
        from navig.daemon.single_instance import (
            DAEMON_PATTERNS,
            GATEWAY_PATTERNS,
            config_dir_of,
            process_table,
        )
        from navig.platform import paths

        mine = paths.config_dir().resolve()
        pats = tuple(p.lower() for p in DAEMON_PATTERNS + GATEWAY_PATTERNS)
        pids = [
            pid
            for pid, cmd in process_table()
            if any(p in cmd.lower() for p in pats) and config_dir_of(pid) == mine
        ]
        if pids:
            ch.success("Bot is running")
            ch.info(f"  PIDs: {', '.join(str(p) for p in pids)}")
        else:
            # "Not running" is a verified answer for a status command — exit 0.
            ch.warning("Bot is not running")
    except Exception as e:
        # But "I could not check" is not an answer (the navig doctor rule).
        ch.error(f"Could not check status: {type(e).__name__}: {e}")
        raise typer.Exit(1) from e


@bot_app.command("stop")
def bot_stop():
    """Stop OUR running NAVIG bot/gateway processes (this config dir only).

    Routes through the config-dir-scoped ``kill_other_instances`` so a
    ``navig bot stop`` run under a different ``NAVIG_CONFIG_DIR`` can NEVER
    force-kill the operator's live brain (the documented catastrophe — the old
    hand-rolled ``pkill -f`` / ``taskkill`` swept machine-wide, unscoped).
    """
    try:
        from navig.daemon.single_instance import (
            DAEMON_PATTERNS,
            GATEWAY_PATTERNS,
            kill_other_instances,
        )
        from navig.platform import paths

        killed = kill_other_instances(
            DAEMON_PATTERNS + GATEWAY_PATTERNS, config_dir=paths.config_dir()
        )
        if killed:
            ch.success(
                f"Stopped NAVIG bot/gateway processes: {', '.join(str(p) for p in killed)}"
            )
        else:
            # Nothing to stop is the desired end state — idempotent, exit 0.
            ch.warning("No running processes found")
    except Exception as e:
        ch.error(f"Error stopping: {type(e).__name__}: {e}")
        raise typer.Exit(1) from e


# ============================================================================
# HEARTBEAT - PERIODIC HEALTH CHECKS
# ============================================================================

heartbeat_app = typer.Typer(
    help="Periodic health check system",
    invoke_without_command=True,
    no_args_is_help=False,
)


@heartbeat_app.callback()
def heartbeat_callback(ctx: typer.Context):
    """Heartbeat commands - run without subcommand for help."""
    from navig.cli._callbacks import show_subcommand_help

    if ctx.invoked_subcommand is None:
        show_subcommand_help("heartbeat", ctx)
        raise typer.Exit()


@heartbeat_app.command("status")
def heartbeat_status():
    """Show heartbeat status."""
    from datetime import datetime

    import requests

    try:
        response = _gw_request("GET", "/status", timeout=_GW_REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = _unwrap(response.json())
            hb = data.get("heartbeat", {})
            config = data.get("config", {})

            if hb.get("running"):
                ch.success("Heartbeat is running")

                interval = config.get("heartbeat_interval", "30m")
                ch.info(f"  Interval: {interval}")

                next_run = hb.get("next_run")
                if next_run:
                    try:
                        next_dt = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
                        now = datetime.now(next_dt.tzinfo) if next_dt.tzinfo else datetime.now()
                        diff = next_dt - now
                        minutes = int(diff.total_seconds() / 60)
                        if minutes > 0:
                            ch.info(f"  Next check: in {minutes} minutes")
                        else:
                            ch.info("  Next check: imminent")
                    except Exception:
                        ch.info(f"  Next check: {next_run}")
                else:
                    ch.info("  Next check: unknown")

                last_run = hb.get("last_run")
                if last_run:
                    ch.info(f"  Last run: {last_run}")
                else:
                    ch.info("  Last run: never")
            else:
                ch.warning("Heartbeat is not running")
                ch.info("Start gateway to enable heartbeat: navig gateway start")
        else:
            # A status command may answer "not running" (below) at exit 0 — that is a
            # verified answer. An HTTP error is NOT an answer: it means we could not
            # look, which must never exit 0. Same rule as navig doctor.
            ch.error(f"Failed to get status: HTTP {response.status_code}")
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except requests.exceptions.ConnectionError:
        # Heartbeat runs INSIDE the gateway, so "gateway down" IS "heartbeat down" —
        # a real answer, exit 0.
        ch.warning("Heartbeat is not running (the gateway is not running)")
        ch.info("Start with: navig gateway start")
    except Exception as e:
        ch.error(f"Could not read heartbeat status: {type(e).__name__}: {e}")
        raise typer.Exit(1) from e


@heartbeat_app.command("trigger")
def heartbeat_trigger():
    """Trigger an immediate heartbeat check."""
    ch.info("Triggering heartbeat check...")

    # A trigger that never reached the gateway did NOT run a check — exit non-zero.
    # Issues *found* by a check that did run are its result, not a failure to run, so
    # they stay exit 0.
    result = _gw_api("POST", "/heartbeat/trigger", action="trigger a heartbeat check", timeout=300)
    if result.get("suppressed"):
        ch.success("HEARTBEAT_OK - All systems healthy")
    elif result.get("issues"):
        ch.warning(f"Issues found: {len(result['issues'])}")
        for issue in result["issues"]:
            ch.warning(f"  • {issue}")
    else:
        ch.success("Heartbeat completed")


@heartbeat_app.command("history")
def heartbeat_history(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of entries to show"),
):
    """Show heartbeat history."""
    history = _gw_api(
        "GET",
        f"/heartbeat/history?limit={limit}",
        action="read heartbeat history",
    ).get("history", [])

    if not history:
        # No history yet is a real answer — exit 0.
        ch.info("No heartbeat history")
        return

    ch.info(f"Heartbeat history (last {len(history)}):")
    for entry in history:
        status = "✓" if entry.get("success") else "✗"
        suppressed = " (OK)" if entry.get("suppressed") else ""
        issues_count = int(entry.get("issues_count") or 0)
        issue_tag = f" · {issues_count} issue(s)" if issues_count else ""
        ch.info(
            f"  {status} {entry.get('timestamp', '?')}{suppressed} - "
            f"{entry.get('duration', 0):.1f}s{issue_tag}"
        )
        # When the agent flagged something, dump the actual issue lines so the
        # operator sees what is wrong.
        for issue in entry.get("issues_found") or []:
            ch.info(f"     - {issue}")


@heartbeat_app.command("configure")
def heartbeat_configure(
    interval: int = typer.Option(None, "--interval", "-i", help="Interval in minutes"),
    enable: bool = typer.Option(None, "--enable/--disable", help="Enable/disable heartbeat"),
):
    """Configure heartbeat settings."""
    from navig.config import ConfigManager

    config_manager = ConfigManager()

    if interval is not None or enable is not None:
        # set_global() is the one safe dotted write (refresh → deep-set → save). This
        # used to mutate config_manager.global_config in place and then call
        # save_global(), which does not exist: the success lines had ALREADY printed,
        # so the command claimed to have saved a setting it then failed to persist.
        try:
            if interval is not None:
                config_manager.set_global("heartbeat.interval", interval)
                ch.success(f"Set heartbeat interval to {interval} minutes")

            if enable is not None:
                config_manager.set_global("heartbeat.enabled", enable)
                ch.success(f"Heartbeat {'enabled' if enable else 'disabled'}")
        except Exception as e:
            ch.error(f"Failed to save heartbeat settings: {e}")
            raise typer.Exit(1) from e
    else:
        config = config_manager.global_config
        hb = config.get("heartbeat", {})
        ch.info("Heartbeat configuration:")
        ch.info(f"  Enabled: {hb.get('enabled', True)}")
        ch.info(f"  Interval: {hb.get('interval', 30)} minutes")
        ch.info(f"  Timeout: {hb.get('timeout', 300)} seconds")


# ============================================================================
# APPROVAL SYSTEM (Human-in-the-loop for agent actions)
# ============================================================================

approve_app = typer.Typer(
    help="Human approval system for agent actions",
    invoke_without_command=True,
    no_args_is_help=False,
)


@approve_app.callback()
def approve_callback(ctx: typer.Context):
    """Approval management - run without subcommand to list pending."""
    if ctx.invoked_subcommand is None:
        approve_list()


@approve_app.command("list")
def approve_list():
    """List pending approval requests."""
    pending = _gw_api("GET", "/approval/pending", action="list approvals").get("pending", [])

    if not pending:
        # Nothing pending is a real, successful answer — exit 0.
        ch.info("No pending approval requests")
        return

    ch.info(f"Pending approval requests ({len(pending)}):")
    for req in pending:
        level_color = {
            "confirm": "yellow",
            "dangerous": "red",
            "never": "bright_red",
        }.get(req.get("level", ""), "white")

        ch.console.print(
            f"  \\[{req['id']}] {req['action']} ({req['level']}) - {req.get('description', '')}",
            style=level_color,
        )


@approve_app.command("yes")
def approve_yes(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option("", "--reason", "-r", help="Optional reason"),
):
    """Approve a pending request."""
    _gw_api(
        "POST",
        f"/approval/{request_id}/respond",
        action=f"approve request {request_id}",
        json_body={"approved": True, "reason": reason},
        expect_payload=False,
        not_found=f"Request {request_id} not found",
    )
    ch.success(f"Request {request_id} approved")


@approve_app.command("no")
def approve_no(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option("", "--reason", "-r", help="Optional reason"),
):
    """Deny a pending request."""
    _gw_api(
        "POST",
        f"/approval/{request_id}/respond",
        action=f"deny request {request_id}",
        json_body={"approved": False, "reason": reason},
        expect_payload=False,
        not_found=f"Request {request_id} not found",
    )
    ch.success(f"Request {request_id} denied")


@approve_app.command("policy")
def approve_policy():
    """Show approval policy (patterns and levels)."""
    try:
        from navig.approval import ApprovalPolicy

        policy = ApprovalPolicy.default()

        ch.info("Approval Policy Patterns:")
        ch.console.print("\n[bold green]SAFE (no approval needed):[/bold green]")
        for pattern in policy.patterns.get("safe", []):
            ch.console.print(f"  • {pattern}")

        ch.console.print("\n[bold yellow]CONFIRM (requires approval):[/bold yellow]")
        for pattern in policy.patterns.get("confirm", []):
            ch.console.print(f"  • {pattern}")

        ch.console.print("\n[bold red]DANGEROUS (always confirm):[/bold red]")
        for pattern in policy.patterns.get("dangerous", []):
            ch.console.print(f"  • {pattern}")

        ch.console.print("\n[bold bright_red]NEVER (always denied):[/bold bright_red]")
        for pattern in policy.patterns.get("never", []):
            ch.console.print(f"  • {pattern}")
    except ImportError as exc:
        ch.error("Approval module not available")
        raise typer.Exit(1) from exc
    except Exception as e:
        ch.error(f"Could not read the approval policy: {type(e).__name__}: {e}")
        raise typer.Exit(1) from e


# ============================================================================
# TASK QUEUE (Async operations queue)
# ============================================================================

queue_app = typer.Typer(
    help="Task queue for async operations",
    invoke_without_command=True,
    no_args_is_help=False,
)


@queue_app.callback()
def queue_callback(ctx: typer.Context):
    """Task queue - run without subcommand to list tasks."""
    if ctx.invoked_subcommand is None:
        queue_list()


@queue_app.command("list")
def queue_list(
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max tasks to show"),
):
    """List queued tasks."""
    tasks = _gw_api(
        "GET",
        "/tasks" + (f"?status={status}" if status else ""),
        action="list tasks",
        unavailable="Cannot list tasks: the gateway's tasks module is not available",
    ).get("tasks", [])

    if not tasks:
        # An empty queue is a real, successful answer — exit 0.
        ch.info("No tasks in queue")
        return

    ch.info(f"Tasks ({len(tasks)}):")
    for task in tasks:
        status_color = {
            "pending": "blue",
            "queued": "cyan",
            "running": "yellow",
            "completed": "green",
            "failed": "red",
            "cancelled": "dim",
        }.get(task.get("status", ""), "white")

        # The id is escaped: Rich read the bare "[op-…]" as a style tag and SWALLOWED
        # it, so `queue list` showed a task with no id — while `queue show`/`cancel`
        # both require that id as their argument.
        ch.console.print(
            f"  \\[{task['id']}] {task['name']} - {task['status']}",
            style=status_color,
        )


@queue_app.command("add")
def queue_add(
    name: str = typer.Argument(..., help="Task name"),
    handler: str = typer.Argument(..., help="Handler to execute"),
    params: str | None = typer.Option(None, "--params", "-p", help="JSON params"),
    priority: int = typer.Option(50, "--priority", help="Priority (lower = higher)"),
):
    """Add a task to the queue."""
    import json as json_mod

    task_params = {}
    if params:
        try:
            task_params = json_mod.loads(params)
        except json_mod.JSONDecodeError as exc:
            ch.error(f"Invalid JSON in --params: {exc}")
            raise typer.Exit(2) from exc

    # Through _gw_api so the json_ok envelope is unwrapped: reading .get("id") off the
    # raw body always missed (the payload is one level down), so a successful add
    # printed "Task added: None".
    task = _gw_api(
        "POST",
        "/tasks",
        action="add the task",
        json_body={
            "name": name,
            "handler": handler,
            "params": task_params,
            "priority": priority,
        },
        unavailable="Cannot add the task: the gateway's tasks module is not available",
    )
    ch.success(f"Task added: {task.get('id', '(no id returned)')}")


@queue_app.command("show")
def queue_show(
    task_id: str = typer.Argument(..., help="Task ID"),
):
    """Show task details."""
    # Through _gw_api: read off the raw body, every field below was the json_ok
    # envelope's missing key, so a real task rendered as "Task: unknown" with a
    # None id, handler and status.
    data = _gw_api(
        "GET",
        f"/tasks/{task_id}",
        action=f"show task {task_id}",
        not_found=f"Task {task_id} not found",
        unavailable="Cannot show the task: the gateway's tasks module is not available",
    )
    ch.info(f"Task: {data.get('name', 'unknown')}")
    ch.console.print(f"  ID: {data.get('id')}")
    ch.console.print(f"  Handler: {data.get('handler')}")
    ch.console.print(f"  Status: {data.get('status')}")
    ch.console.print(f"  Priority: {data.get('priority')}")
    if data.get("error"):
        ch.console.print(f"  Error: {data.get('error')}", style="red")
    if data.get("result"):
        ch.console.print(f"  Result: {data.get('result')}")


@queue_app.command("cancel")
def queue_cancel(
    task_id: str = typer.Argument(..., help="Task ID to cancel"),
):
    """Cancel a pending task."""
    _gw_api(
        "POST",
        f"/tasks/{task_id}/cancel",
        action=f"cancel task {task_id}",
        expect_payload=False,
        not_found=f"Task {task_id} not found",
        unavailable="Cannot cancel the task: the gateway's tasks module is not available",
    )
    ch.success(f"Task {task_id} cancelled")


@queue_app.command("stats")
def queue_stats():
    """Show queue statistics."""
    # _gw_api unwraps the json_ok envelope. Read off the raw body, every one of these
    # counters was the envelope's own missing key -> a busy queue reported all zeros.
    data = _gw_api(
        "GET",
        "/tasks/stats",
        action="read queue stats",
        unavailable="Cannot read queue stats: the gateway's tasks module is not available",
    )

    ch.info("Task Queue Statistics:")
    ch.console.print(f"  Total tasks: {data.get('total_tasks', 0)}")
    ch.console.print(f"  Heap size: {data.get('heap_size', 0)}")
    ch.console.print(f"  Completed: {data.get('completed_count', 0)}")

    counts = data.get("status_counts", {})
    if counts:
        ch.console.print("\n  Status breakdown:")
        for status, count in counts.items():
            ch.console.print(f"    {status}: {count}")

    worker = data.get("worker", {})
    if worker:
        ch.console.print("\n  Worker:")
        ch.console.print(f"    Running: {worker.get('running', False)}")
        ch.console.print(f"    Active tasks: {worker.get('active_tasks', 0)}")
        ch.console.print(f"    Completed: {worker.get('tasks_completed', 0)}")
        ch.console.print(f"    Failed: {worker.get('tasks_failed', 0)}")
