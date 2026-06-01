"""``CloudManager`` -- owns the cloudflared subprocess + broker heartbeat.

Lifecycle (called from the gateway lifespan in ``navig/gateway/server.py``):

  await CloudManager(...).start()    # spawn cloudflared, scrape URL, register
  ...                                # heartbeat task runs in the background
  await CloudManager(...).stop()     # kill cloudflared, cancel heartbeat,
                                     # call broker.unregister

URL rotation: every time cloudflared restarts (machine wake, network change,
crash) it gets a new ``https://*.trycloudflare.com`` URL. The manager scrapes
the new URL from stdout, calls ``broker.heartbeat(new_url)``, and the Deck's
cached URL falls through on the next failed call and re-resolves.

The manager is silent when ``cloud.enabled`` is false -- the gateway wraps the
start call in that gate, so this module never assumes it should run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Literal

from navig.cloud.broker_client import BrokerClient, BrokerError
from navig.cloud.installer import InstallerError, ensure_cloudflared

logger = logging.getLogger(__name__)

# Matches the URL cloudflared prints once the tunnel is established. We accept
# variants ("Your quick Tunnel has been created!" formats change across
# releases) by simply grepping for the trycloudflare.com hostname.
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)

# Total budget for cloudflared to print the first URL. If we don't see one in
# this window we mark the manager errored -- usually means firewall blocking
# outbound or the binary is broken.
_URL_TIMEOUT_S = 30.0

CloudStatus = Literal["off", "starting", "online", "error", "stopping"]


@dataclass
class CloudState:
    status: CloudStatus = "off"
    tunnel_url: str | None = None
    last_heartbeat_at: float | None = None
    last_error: str | None = None
    pid: int | None = None
    started_at: float | None = None
    rotations: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


class CloudManager:
    """Owns the cloudflared subprocess + broker heartbeat for this daemon."""

    def __init__(
        self,
        *,
        api_key: str,
        broker_url: str,
        gateway_port: int,
        heartbeat_interval_s: float = 60.0,
        tunnel_label: str = "",
        cloudflared_path: str = "",
        cloudflared_extra_args: list[str] | None = None,
        public_url: str = "",
    ):
        self.api_key = api_key
        self.broker_url = broker_url
        self.gateway_port = gateway_port
        self.heartbeat_interval_s = max(10.0, float(heartbeat_interval_s))
        self.tunnel_label = tunnel_label or socket.gethostname()
        self.cloudflared_path_override = cloudflared_path or ""
        self.cloudflared_extra_args = list(cloudflared_extra_args or [])
        # VPS / direct mode: when public_url is set, skip cloudflared and
        # register THIS URL with the broker. User owns the reverse proxy.
        self.public_url = (public_url or "").strip().rstrip("/")
        self.mode: str = "direct" if self.public_url else "tunnel"

        self._proc: asyncio.subprocess.Process | None = None
        self._scraper_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._url_event: asyncio.Event = asyncio.Event()
        self._stop_requested: bool = False
        self._broker: BrokerClient | None = None
        self.state = CloudState()

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def status(self) -> CloudStatus:
        return self.state.status

    @property
    def current_url(self) -> str | None:
        return self.state.tunnel_url

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.state.status,
            "tunnel_url": self.state.tunnel_url,
            "broker_url": self.broker_url,
            "last_heartbeat_at": self.state.last_heartbeat_at,
            "last_error": self.state.last_error,
            "pid": self.state.pid,
            "started_at": self.state.started_at,
            "rotations": self.state.rotations,
            "label": self.tunnel_label,
            "mode": self.mode,
        }

    async def start(self) -> None:
        if self._proc is not None or self._heartbeat_task is not None:
            logger.debug("CloudManager.start() called while already running")
            return
        if not self.api_key:
            self._mark_error("no_api_key")
            raise RuntimeError(
                "cloud.enabled=true but deck.api_key is empty. "
                "Run `navig cloud connect` or set deck.api_key in ~/.navig/config.yaml."
            )
        self._stop_requested = False
        self.state = CloudState(status="starting", started_at=_now())
        self._broker = BrokerClient(self.broker_url, self.api_key)

        if self.mode == "direct":
            await self._start_direct()
        else:
            await self._start_tunnel()

    async def _start_direct(self) -> None:
        """Direct mode: register a user-provided public URL, no cloudflared.

        The user owns a reverse proxy (nginx/Caddy/Traefik) that terminates
        TLS on this URL and forwards to gateway.host:gateway.port. We just
        publish the URL to the broker and keep heartbeating. No subprocess,
        no URL rotation, no watchdog.
        """
        url = self.public_url
        if not url.lower().startswith("https://"):
            self._mark_error(f"public_url must be https://, got {url!r}")
            raise RuntimeError(
                f"cloud.public_url must start with https:// (got {url!r}). "
                "TLS termination is required for the hosted Deck to reach you."
            )
        try:
            parsed_host = url.split("://", 1)[1].split("/", 1)[0].lower()
            if parsed_host.endswith(".trycloudflare.com"):
                logger.warning(
                    "cloud.public_url points at a *.trycloudflare.com host -- "
                    "you probably want cloudflared mode instead (clear public_url)."
                )
        except Exception:  # noqa: BLE001
            pass

        self.state.tunnel_url = url
        try:
            await self._register_current_url()
            self.state.status = "online"
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.info(
                "Cloud online (direct mode): %s -> %s",
                self.broker_url, self.state.tunnel_url,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark_error(str(exc))
            raise

    async def _start_tunnel(self) -> None:
        """Cloudflared quick-tunnel mode (the default for laptop / NAT users)."""
        try:
            await self._spawn_cloudflared()
            await self._wait_for_url(timeout=_URL_TIMEOUT_S)
            await self._register_current_url()
            self.state.status = "online"
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            logger.info("Cloud online: %s -> %s", self.broker_url, self.state.tunnel_url)
        except Exception as exc:  # noqa: BLE001
            self._mark_error(str(exc))
            await self._kill_proc()
            raise

    async def stop(self) -> None:
        if self.state.status == "off":
            return
        self.state.status = "stopping"
        self._stop_requested = True

        for task in (self._heartbeat_task, self._watchdog_task, self._scraper_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._heartbeat_task, self._watchdog_task, self._scraper_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._heartbeat_task = self._watchdog_task = self._scraper_task = None

        await self._kill_proc()

        if self._broker is not None:
            try:
                await self._broker.unregister()
            except BrokerError as exc:
                logger.debug("broker.unregister failed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.debug("broker.unregister errored: %r", exc)
            try:
                await self._broker.close()
            except Exception:  # noqa: BLE001
                pass
            self._broker = None

        self.state = CloudState(status="off")
        logger.info("Cloud offline.")

    # ── Internals ────────────────────────────────────────────────────────────

    def _mark_error(self, reason: str) -> None:
        self.state.status = "error"
        self.state.last_error = reason

    async def _spawn_cloudflared(self) -> None:
        try:
            binary = ensure_cloudflared(self.cloudflared_path_override)
        except InstallerError as exc:
            raise RuntimeError(f"cloudflared unavailable: {exc}") from exc

        args = [
            binary,
            "tunnel",
            "--no-autoupdate",
            "--url",
            f"http://127.0.0.1:{self.gateway_port}",
            *self.cloudflared_extra_args,
        ]
        env = os.environ.copy()
        # cloudflared prints its banner + URL on stderr, not stdout. We merge
        # both so the scraper only needs one stream.
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        self.state.pid = self._proc.pid
        self._url_event.clear()
        self._scraper_task = asyncio.create_task(self._scrape_loop())

    async def _scrape_loop(self) -> None:
        """Continuously read cloudflared output for URL lines."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip()
                # Trim sensitive values from logs; tunnel URLs themselves are
                # safe to log because they're the routing identifier.
                logger.debug("[cloudflared] %s", text)
                match = _URL_RE.search(text)
                if match:
                    new_url = match.group(0)
                    prev = self.state.tunnel_url
                    if new_url != prev:
                        self.state.tunnel_url = new_url
                        if prev is not None:
                            self.state.rotations += 1
                            logger.info("Cloud URL rotated: %s -> %s", prev, new_url)
                            # Push the rotation to the broker immediately so
                            # the open Deck re-resolves within one round-trip.
                            asyncio.create_task(self._register_current_url())
                        self._url_event.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("cloudflared scrape loop ended: %r", exc)

    async def _wait_for_url(self, *, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._url_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"cloudflared did not print a trycloudflare.com URL within {timeout:.0f}s"
            ) from exc

    async def _register_current_url(self) -> None:
        if self._broker is None or self.state.tunnel_url is None:
            return
        try:
            await self._broker.register(self.state.tunnel_url, self.tunnel_label or None)
            self.state.last_heartbeat_at = _now()
            # Now that the daemon's broker row exists, bind allowed Telegram
            # users. Doing it HERE (right after register) eliminates the race
            # where the Telegram channel tried to bind before the tunnel was
            # registered → broker 404 → Mini App "not bound".
            await self._bind_telegram_users()
        except BrokerError as exc:
            logger.warning("broker.register failed: %s", exc)
            self.state.last_error = f"register: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("broker.register errored: %r", exc)
            self.state.last_error = f"register: {exc!r}"

    async def _bind_telegram_users(self) -> None:
        """Bind configured Telegram allowed_users to this daemon on the broker.

        Runs after a successful register so the hosted Mini App can resolve
        this daemon by telegram_id. Idempotent (INSERT OR REPLACE broker-side);
        per-user failures are logged but never abort the cloud flow.
        """
        if self._broker is None:
            return
        try:
            from navig.config import get_config_manager

            allowed = get_config_manager().global_config.get("telegram", {}).get("allowed_users") or []
        except Exception:
            return
        for uid in allowed:
            try:
                await self._broker.bind_telegram(int(uid))
                logger.info("Telegram user %s bound to this daemon via broker", uid)
            except (TypeError, ValueError):
                continue  # non-numeric id (username) — can't bind by tg id
            except Exception as exc:  # noqa: BLE001
                logger.warning("broker.bind_telegram(%s) failed: %r", uid, exc)

    async def _heartbeat_loop(self) -> None:
        while not self._stop_requested:
            try:
                await asyncio.sleep(self.heartbeat_interval_s)
                if self._broker is None or self.state.tunnel_url is None:
                    continue
                try:
                    await self._broker.heartbeat(self.state.tunnel_url)
                    self.state.last_heartbeat_at = _now()
                except BrokerError as exc:
                    if exc.status == 404:
                        # Row missing -- broker forgot us (D1 reset?) -- re-register.
                        logger.info("broker heartbeat -> 404, re-registering")
                        await self._register_current_url()
                    else:
                        logger.debug("broker.heartbeat failed: %s", exc)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("broker.heartbeat errored: %r", exc)
            except asyncio.CancelledError:
                return

    async def _watchdog_loop(self) -> None:
        """Restart cloudflared if it exits unexpectedly."""
        while not self._stop_requested:
            try:
                proc = self._proc
                if proc is None:
                    return
                exit_code = await proc.wait()
                if self._stop_requested:
                    return
                logger.warning(
                    "cloudflared exited unexpectedly (code=%s); restarting in 5s", exit_code
                )
                self.state.status = "starting"
                self.state.tunnel_url = None
                await asyncio.sleep(5.0)
                try:
                    await self._spawn_cloudflared()
                    await self._wait_for_url(timeout=_URL_TIMEOUT_S)
                    await self._register_current_url()
                    self.state.status = "online"
                    self.state.rotations += 1
                except Exception as exc:  # noqa: BLE001
                    self._mark_error(f"restart_failed: {exc}")
                    return
            except asyncio.CancelledError:
                return

    async def _kill_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    return
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass


def _now() -> float:
    import time
    return time.time()
