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
from navig.core.aio_subprocess import STREAM_LIMIT
from navig.core.background import spawn

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

# UplinkClient uses "connecting" where CloudManager reports "starting".
_UPLINK_STATUS_MAP: dict[str, CloudStatus] = {
    "off": "off",
    "connecting": "starting",
    "online": "online",
    "error": "error",
    "stopping": "stopping",
}


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
        broker_timeout_s: float = 15.0,
        lighthouse_url: str = "",
        telegram_handler: Any = None,
        system_events: Any = None,
        snapshot_provider: Any = None,
    ):
        self.api_key = api_key
        self.broker_url = broker_url
        self.gateway_port = gateway_port
        self.broker_timeout_s = max(5.0, float(broker_timeout_s))
        self.heartbeat_interval_s = max(10.0, float(heartbeat_interval_s))
        self.tunnel_label = tunnel_label or socket.gethostname()
        self.cloudflared_path_override = cloudflared_path or ""
        self.cloudflared_extra_args = list(cloudflared_extra_args or [])
        # VPS / direct mode: when public_url is set, skip cloudflared and
        # register THIS URL with the broker. User owns the reverse proxy.
        self.public_url = (public_url or "").strip().rstrip("/")
        # Lighthouse mode: an outbound WebSocket uplink to a self-hosted
        # Cloudflare edge. No tunnel, no broker, no inbound port — the brain
        # dials out and stays connected. Takes precedence over direct/tunnel.
        self.lighthouse_url = (lighthouse_url or "").strip().rstrip("/")
        if self.lighthouse_url:
            self.mode = "lighthouse"
        elif self.public_url:
            self.mode = "direct"
        else:
            self.mode = "tunnel"
        # Lighthouse dispatch hooks (only used in lighthouse mode).
        self._telegram_handler = telegram_handler
        self._system_events = system_events
        self._snapshot_provider = snapshot_provider

        self._proc: asyncio.subprocess.Process | None = None
        self._scraper_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._url_event: asyncio.Event = asyncio.Event()
        self._stop_requested: bool = False
        self._broker: BrokerClient | None = None
        self._uplink: Any = None  # UplinkClient in lighthouse mode
        # Telegram user ids whose broker bind failed (transient timeout); retried
        # on the heartbeat loop so the Mini App self-heals from "not bound".
        self._pending_binds: set[int] = set()
        self.state = CloudState()

    # ── Public API ───────────────────────────────────────────────────────────

    def _effective_status(self) -> CloudStatus:
        """The manager's real status.

        In lighthouse mode it comes from the uplink — NOT ``state.status``, which
        ``_start_lighthouse`` pins to ``"online"`` before the WS handshake and never
        reconciles. Trusting ``state.status`` there reports a phantom ``"online"``
        over a dead uplink, so the deck refuses to re-establish it and the boot hint
        shows green while the bot is unreachable ("green light over a dead uplink").
        """
        if self._uplink is not None:
            return _UPLINK_STATUS_MAP.get(self._uplink.status, self.state.status)
        return self.state.status

    @property
    def status(self) -> CloudStatus:
        return self._effective_status()

    @property
    def current_url(self) -> str | None:
        return self.state.tunnel_url

    def snapshot(self) -> dict[str, Any]:
        snap = {
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
        if self._uplink is not None:
            snap["lighthouse"] = self._uplink.snapshot()
        snap["status"] = self._effective_status()
        return snap

    async def start(self) -> None:
        # `_uplink` is lighthouse mode's "running" marker — it sets neither `_proc`
        # nor `_heartbeat_task`, so without it a second start() would overwrite a
        # live uplink and orphan its reconnect task + session. A FAILED start leaves
        # `_uplink` None (see `_start_lighthouse`), so a retry is still allowed.
        if (
            self._proc is not None
            or self._heartbeat_task is not None
            or self._uplink is not None
        ):
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

        if self.mode == "lighthouse":
            # No broker, no cloudflared — the uplink IS the data path.
            await self._start_lighthouse()
            return

        self._broker = BrokerClient(self.broker_url, self.api_key, timeout_s=self.broker_timeout_s)

        if self.mode == "direct":
            await self._start_direct()
        else:
            await self._start_tunnel()

    async def _start_lighthouse(self) -> None:
        """Lighthouse mode: open the outbound WebSocket uplink to the user's edge.

        The ``UplinkClient`` owns its own reconnect/backoff loop, so once
        started we simply track its state. No subprocess, no broker, no inbound
        port — and replies (bot messages, SMS) go out directly from the brain.
        """
        from navig.cloud.uplink import UplinkClient
        from navig.notify.producers.connectivity import ConnectivityReporter

        # First-party producer: announce edge offline/online (debounced), gated
        # live by monitors.connectivity.enabled so the deck toggle takes effect
        # without a reconnect.
        self._connectivity = ConnectivityReporter(enabled_check=_connectivity_enabled)
        self._uplink = UplinkClient(
            lighthouse_url=self.lighthouse_url,
            api_key=self.api_key,
            gateway_port=self.gateway_port,
            telegram_handler=self._telegram_handler,
            system_events=self._system_events,
            snapshot_provider=self._snapshot_provider,
            version=self.tunnel_label,
            connectivity_listener=self._connectivity.on_status,
        )
        try:
            await self._uplink.start()
            self.state.tunnel_url = self.lighthouse_url
            self.state.status = "online"
            self.state.last_heartbeat_at = _now()
            logger.info("Cloud online (lighthouse mode): %s", self.lighthouse_url)
        except Exception as exc:  # noqa: BLE001
            self._mark_error(str(exc))
            # Don't leave a half-started uplink referenced: a failed start must look
            # like "not running" so start() can be retried (and stop() has nothing
            # stale to tear down). Stop it best-effort in case a future change spawns
            # the reconnect task before raising.
            failed = self._uplink
            self._uplink = None
            if failed is not None:
                try:
                    await failed.stop()
                except Exception:  # noqa: BLE001
                    pass
            raise

        # Publish the STABLE lighthouse URL to the broker + bind Telegram users so
        # the hosted Mini App resolves by api_key / telegram_id to the edge.
        #
        # Without this, the broker keeps whatever was last registered — typically a
        # dead `*.trycloudflare.com` URL from a previous cloudflared session — and
        # the Mini App resolves to that, fails with HTTP 530, re-resolves to the SAME
        # stale URL, and shows "Connection Lost" forever. The lighthouse edge never
        # rotates and is always reachable (it answers brain-offline 503 when the
        # brain sleeps), so a one-shot register + bind is all that's needed — no
        # heartbeat, no watchdog. Broker failures are non-fatal: the uplink is the
        # data path; the broker is only a routing convenience for Mini App resolve.
        try:
            self._broker = BrokerClient(
                self.broker_url, self.api_key, timeout_s=self.broker_timeout_s
            )
            await self._register_current_url()
        except Exception as exc:  # noqa: BLE001
            logger.warning("lighthouse broker register/bind skipped: %r", exc)

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

        if self._uplink is not None:
            try:
                await self._uplink.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("uplink.stop errored: %r", exc)
            self._uplink = None
            # Close (but deliberately do NOT unregister) the broker: the lighthouse
            # edge stays up after the brain stops, so keeping the stable URL
            # resolvable lets the Mini App reach the edge and show "brain offline"
            # rather than a dead-end 404 "not bound".
            if self._broker is not None:
                try:
                    await self._broker.close()
                except Exception:  # noqa: BLE001
                    pass
                self._broker = None
            self.state = CloudState(status="off")
            logger.info("Cloud offline.")
            return

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
            # _scrape_loop readline()s this pipe to find the tunnel URL. Without an explicit
            # limit one long cloudflared log line (64 KiB default) would raise and kill the
            # scraper — the URL would then never be found and the tunnel never come up.
            limit=STREAM_LIMIT,
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
                            # Operator narrator: URL rotation is the moment a
                            # tab open on the old URL will start failing. Make
                            # it visible so the operator knows to reload.
                            try:
                                from navig.core import narrator
                                narrator.phase("Cloud tunnel URL rotated", icon="wave")
                                narrator.metrics([
                                    ("from", prev),
                                    ("to", new_url),
                                    ("rotation", str(self.state.rotations)),
                                ])
                                narrator.step("re-registering with broker...", icon="radio")
                            except Exception:  # noqa: BLE001
                                pass
                            # Push the rotation to the broker immediately so
                            # the open Deck re-resolves within one round-trip.
                            spawn(self._register_current_url())
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

    async def _register_current_url(self, *, attempts: int = 4) -> None:
        """Publish our address to the broker, then bind Telegram users.

        Retried with backoff, and a permanent failure is RECORDED rather than
        only logged. This used to be one-shot, and in lighthouse mode the failure
        was deliberately swallowed on the grounds that the uplink — not the broker
        — is the data path. That reasoning is right about severity and wrong about
        visibility: the Mini App resolves through the broker, so a failed register
        leaves it pointing at whatever was registered LAST, typically a dead
        cloudflared URL from a previous session. That state is invisible from the
        daemon (every light green, uplink online) and indistinguishable from a
        broken deck to the operator. It persisted for weeks on a real install.

        Note the bind is inside the same flow on purpose — it needs our row to
        exist — but a register failure must not make it look like the binds were
        merely skipped, so the two are reported separately.
        """
        if self._broker is None or self.state.tunnel_url is None:
            return

        url = self.state.tunnel_url
        delay = 1.0
        last: Exception | None = None
        for n in range(max(1, attempts)):
            try:
                await self._broker.register(url, self.tunnel_label or None)
                self.state.last_heartbeat_at = _now()
                if n:
                    logger.info("broker.register succeeded on attempt %d", n + 1)
                # Now that the daemon's broker row exists, bind allowed Telegram
                # users. Doing it HERE (right after register) eliminates the race
                # where the Telegram channel tried to bind before the tunnel was
                # registered → broker 404 → Mini App "not bound".
                await self._bind_telegram_users()
                return
            except BrokerError as exc:
                last = exc
                # A rejected URL is a verdict, not a blip — retrying cannot change it.
                if 400 <= exc.status < 500 and exc.status not in (408, 429):
                    break
            except Exception as exc:  # noqa: BLE001
                last = exc
            if n < attempts - 1:
                await asyncio.sleep(delay)
                delay *= 2

        logger.warning("broker.register failed after %d attempt(s): %r", attempts, last)
        # In lighthouse mode the OUTBOUND UPLINK is the data path, so this is not a
        # system error on an otherwise-healthy brain and does not become last_error —
        # that trained the operator to distrust a green status. It IS recorded, so
        # `navig doctor` and the config-incidents monitor can surface it.
        if self.mode != "lighthouse":
            self.state.last_error = f"register: {last}"
        try:
            from navig.core.incidents import BROKER_REGISTER_FAILED, record

            record(
                BROKER_REGISTER_FAILED,
                url=url,
                mode=self.mode,
                attempts=attempts,
                error=f"{last!r}"[:200],
            )
        except Exception:  # noqa: BLE001 — an observation must never break the daemon
            pass

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
        pending: set[int] = set()
        for uid in allowed:
            try:
                uid_int = int(uid)
            except (TypeError, ValueError):
                continue  # non-numeric id (username) — can't bind by tg id
            if not await self._try_bind_one(uid_int, attempts=3):
                pending.add(uid_int)
        # Anything still unbound is retried on the heartbeat loop.
        self._pending_binds = pending

    async def _try_bind_one(self, uid: int, *, attempts: int = 1) -> bool:
        """Bind one Telegram user with exponential backoff. Returns success."""
        if self._broker is None:
            return False
        delay = 1.0
        for n in range(attempts):
            try:
                await self._broker.bind_telegram(uid)
                logger.info("Telegram user %s bound to this daemon via broker", uid)
                return True
            except (TypeError, ValueError):
                return True  # not bindable by id — treat as resolved, don't retry
            except Exception as exc:  # noqa: BLE001
                if n < attempts - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    logger.warning(
                        "broker.bind_telegram(%s) failed after %d attempt(s): %r — "
                        "will retry on heartbeat", uid, attempts, exc,
                    )
        return False

    async def _retry_pending_binds(self) -> None:
        """Re-attempt previously-failed Telegram binds (called from heartbeat)."""
        if not self._pending_binds or self._broker is None:
            return
        still: set[int] = set()
        for uid in list(self._pending_binds):
            if not await self._try_bind_one(uid, attempts=1):
                still.add(uid)
        resolved = self._pending_binds - still
        self._pending_binds = still
        if resolved:
            logger.info("Resolved %d pending Telegram bind(s) on heartbeat", len(resolved))

    async def _heartbeat_loop(self) -> None:
        while not self._stop_requested:
            try:
                await asyncio.sleep(self.heartbeat_interval_s)
                if self._broker is None or self.state.tunnel_url is None:
                    continue
                try:
                    await self._broker.heartbeat(self.state.tunnel_url)
                    self.state.last_heartbeat_at = _now()
                    # Self-heal any Telegram binds that timed out earlier.
                    await self._retry_pending_binds()
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
        """Restart cloudflared if it exits unexpectedly, retrying with a capped backoff.

        A single failed restart must NOT kill the watchdog: cloudflared exits on a
        machine wake / network blip, the immediate respawn fails because connectivity
        hasn't returned yet, and the old code marked "error" and returned — so nothing
        ever restarted the tunnel again even once the network came back. Now a failed
        restart bumps the backoff and loops; a recovered restart resets it.
        """
        backoff = 5.0
        while not self._stop_requested:
            try:
                proc = self._proc
                if proc is not None:
                    # No proc means the previous restart failed and was cleaned up —
                    # skip straight to respawning instead of giving up.
                    exit_code = await proc.wait()
                    if self._stop_requested:
                        return
                    logger.warning("cloudflared exited unexpectedly (code=%s)", exit_code)
                self.state.status = "starting"
                self.state.tunnel_url = None
                await asyncio.sleep(backoff)
                try:
                    await self._spawn_cloudflared()
                    await self._wait_for_url(timeout=_URL_TIMEOUT_S)
                    await self._register_current_url()
                    self.state.status = "online"
                    self.state.rotations += 1
                    backoff = 5.0  # recovered — reset the backoff
                except Exception as exc:  # noqa: BLE001 - keep retrying, never give up
                    self._mark_error(f"restart_failed: {exc}")
                    # Drop the half-spawned proc so the next iteration respawns cleanly
                    # (and doesn't leak a cloudflared process per failed attempt).
                    await self._kill_proc()
                    backoff = min(backoff * 2, 300.0)
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


def _connectivity_enabled() -> bool:
    """Live read of ``monitors.connectivity.enabled``.

    This is the gate ``ConnectivityReporter`` consults before every send, so getting
    it wrong silences the "brain offline" alert entirely. It was wrong twice:

    * **Not actually live.** ``Config()`` (and the cached ConfigManager) serve the
      snapshot loaded at process start — freshness is an opt-in, so toggling this
      monitor took a daemon restart. Read through a refreshed manager instead; this
      runs once per connectivity transition, so the ~1.5 ms is irrelevant.
    * **A hand-rolled truth table.** ``in (True, "1", "true", "yes", "True")`` missed
      ``"on"``, ``"ON"``, ``"TRUE"``, ``"y"`` and the int ``1`` — and
      ``navig config set`` stores a raw STRING, so the documented
      ``config set monitors.connectivity.enabled on`` left the monitor OFF. The deck
      (``deck/routes/notify._truthy``) and the gateway
      (``server._monitor_enabled_truthy``) both already use ``coerce_bool`` and have a
      test pinning them to each other; this was a third reader agreeing with neither.

    Default OFF when unset, unchanged — connectivity is opt-in (only
    ``config_incidents`` is in ``MONITORS_DEFAULT_ON``).
    """
    try:
        from navig.config import get_config_manager
        from navig.core.coerce import coerce_bool

        cm = get_config_manager()
        cm.refresh_global_config()
        return coerce_bool(cm.get("monitors.connectivity.enabled"), default=False)
    except Exception:  # noqa: BLE001 — a gate read must never take the uplink down
        return False
