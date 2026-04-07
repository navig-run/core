"""
NavigMatrixBot -- optional Matrix channel backend for navig.comms.

Uses `matrix-nio` (lazy-imported) so the package is only required when
``comms.matrix.enabled`` is true in config.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navig.comms.matrix_store import MatrixStore

logger = logging.getLogger(__name__)

_bot: NavigMatrixBot | None = None

# E2EE capability detection
HAS_OLM = False
try:
    import olm  # noqa: F401

    HAS_OLM = True
except ImportError:
    pass  # optional dependency not installed; feature disabled


def get_matrix_bot() -> NavigMatrixBot | None:
    """Return the singleton NavigMatrixBot or None."""
    return _bot


def is_e2ee_available() -> bool:
    """Check if E2EE is available (libolm installed)."""
    return HAS_OLM


@dataclass
class MatrixConfig:
    homeserver_url: str = "http://localhost:6167"
    user_id: str = ""
    password: str = ""
    access_token: str = ""
    default_room_id: str = ""
    auto_join: bool = True
    e2ee: bool = False
    device_name: str = "NAVIG"
    store_path: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MatrixConfig:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class NavigMatrixBot:
    """Thin wrapper around matrix-nio AsyncClient."""

    def __init__(self, config: dict[str, Any] | MatrixConfig):
        if isinstance(config, dict):
            config = MatrixConfig.from_dict(config)
        self.cfg = config
        self._client = None
        self._sync_task: asyncio.Task | None = None
        self._running = False
        self._message_callbacks: list[Callable] = []
        self._verification_callbacks: list[Callable] = []
        self._e2ee_enabled = False
        self._store: MatrixStore | None = None

    async def start(self) -> None:
        """Login and begin background sync."""
        global _bot
        try:
            from nio import (
                AsyncClient,
                AsyncClientConfig,
                InviteMemberEvent,
                LoginResponse,
                RoomMessageText,
            )
        except ImportError as _exc:
            logger.error("matrix-nio is not installed. pip install matrix-nio[e2e]")
            raise ImportError(
                "matrix-nio required for Matrix support. pip install matrix-nio[e2e]"
            ) from _exc

        # Resolve E2EE: enabled only if config says so AND libolm is available
        want_e2ee = self.cfg.e2ee and HAS_OLM
        if self.cfg.e2ee and not HAS_OLM:
            logger.warning("E2EE requested but libolm not installed. pip install matrix-nio[e2e]")

        # Resolve store path for crypto persistence
        store_dir = self.cfg.store_path or None
        if want_e2ee and not store_dir:
            import os

            store_dir = os.path.expanduser("~/.navig/matrix-store")
            os.makedirs(store_dir, exist_ok=True)
            logger.info("Matrix: crypto store at %s", store_dir)

        client_config = AsyncClientConfig(
            encryption_enabled=want_e2ee,
            store_sync_tokens=True,
        )
        self._e2ee_enabled = want_e2ee

        self._client = AsyncClient(
            self.cfg.homeserver_url,
            self.cfg.user_id,
            store_path=store_dir,
            device_id=self.cfg.device_name,
            config=client_config,
        )

        if self.cfg.access_token:
            self._client.access_token = self.cfg.access_token
            self._client.user_id = self.cfg.user_id
            logger.info("Matrix: using access token for %s", self.cfg.user_id)
        else:
            resp = await self._client.login(self.cfg.password, device_name=self.cfg.device_name)
            if not isinstance(resp, LoginResponse):
                raise RuntimeError(f"Matrix login failed: {resp}")
            logger.info("Matrix: logged in as %s", self.cfg.user_id)

        if self.cfg.auto_join:
            self._client.add_event_callback(self._on_invite, InviteMemberEvent)
        self._client.add_event_callback(self._on_room_message, RoomMessageText)

        # E2EE: register key verification callbacks and upload keys
        if self._e2ee_enabled:
            try:
                from nio import KeyVerificationEvent

                self._client.add_to_device_callback(
                    self._on_key_verification,
                    KeyVerificationEvent,
                )
                logger.info("Matrix: E2EE enabled, key verification callbacks registered")
            except (ImportError, Exception) as exc:
                logger.warning("Matrix: could not register E2EE callbacks: %s", exc)

        # Initialise persistent store
        try:
            import os

            from navig.comms.matrix_store import MatrixStore

            store_db = os.path.expanduser("~/.navig/matrix.db")
            self._store = MatrixStore(store_db)
            self._store.prune_events()  # keep DB tidy
            logger.info("Matrix: persistent store at %s", store_db)
        except Exception:
            logger.warning("Matrix: could not initialise persistent store (non-fatal)")
            self._store = None

        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())
        _bot = self

        # Wait for initial sync to complete so we have a valid next_batch token
        try:
            await self._client.sync(timeout=10000, full_state=True)
            logger.info("Matrix bot started, initial sync done")
        except Exception:
            logger.warning("Matrix: initial sync incomplete, continuing anyway")
            logger.info("Matrix bot started, syncing...")

        # E2EE: upload device keys after initial sync
        if self._e2ee_enabled and self._client:
            try:
                if self._client.should_upload_keys:
                    await self._client.keys_upload()
                    logger.info("Matrix: device keys uploaded")
            except Exception:
                logger.warning("Matrix: key upload failed (non-fatal)")

    async def stop(self) -> None:
        """Stop sync and close."""
        global _bot
        self._running = False
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown
        if self._client:
            await self._client.close()
            self._client = None
        if self._store:
            self._store.close()
            self._store = None
        _bot = None
        logger.info("Matrix bot stopped")

    @property
    def is_running(self) -> bool:
        return self._running and self._client is not None

    @property
    def store(self) -> MatrixStore | None:
        """Expose the persistent store (or None if not initialised)."""
        return self._store

    async def send_message(self, room_id: str, message: str) -> str | None:
        """Send a text message. Returns event_id or None."""
        if not self._client:
            logger.warning("Matrix client not initialised")
            return None
        try:
            from nio import RoomSendResponse

            resp = await self._client.room_send(
                room_id=room_id or self.cfg.default_room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.text",
                    "body": message,
                    "format": "org.matrix.custom.html",
                    "formatted_body": message,
                },
            )
            if isinstance(resp, RoomSendResponse):
                return resp.event_id
            logger.warning("Matrix send unexpected response: %s", resp)
            return None
        except Exception:
            logger.exception("Matrix send_message failed")
            return None

    async def send_notice(self, room_id: str, message: str) -> str | None:
        """Send a notice (non-highlighted). Returns event_id or None."""
        if not self._client:
            return None
        try:
            from nio import RoomSendResponse

            resp = await self._client.room_send(
                room_id=room_id or self.cfg.default_room_id,
                message_type="m.room.message",
                content={"msgtype": "m.notice", "body": message},
            )
            if isinstance(resp, RoomSendResponse):
                return resp.event_id
            return None
        except Exception:
            logger.exception("Matrix send_notice failed")
            return None

    async def create_room(
        self,
        name: str,
        *,
        topic: str = "",
        is_public: bool = False,
        invite_user_ids: list[str] | None = None,
    ) -> str | None:
        """Create a room. Returns room_id or None."""
        if not self._client:
            return None
        try:
            from nio import RoomCreateResponse, RoomVisibility

            resp = await self._client.room_create(
                name=name,
                topic=topic,
                visibility=(RoomVisibility.public if is_public else RoomVisibility.private),
                invite=invite_user_ids or [],
            )
            if isinstance(resp, RoomCreateResponse):
                # Persist room in store
                if self._store:
                    try:
                        from navig.comms.matrix_store import MatrixRoom as _MR

                        self._store.upsert_room(
                            _MR(
                                room_id=resp.room_id,
                                name=name,
                                topic=topic,
                                purpose="general",
                                encrypted=False,
                            )
                        )
                    except Exception:
                        logger.debug("Matrix: could not persist room to store")
                return resp.room_id
            return None
        except Exception:
            logger.exception("Matrix create_room failed")
            return None

    async def invite_user(self, room_id: str, user_id: str) -> bool:
        """Invite a user to a room."""
        if not self._client:
            return False
        try:
            await self._client.room_invite(room_id, user_id)
            return True
        except Exception:
            logger.exception("Matrix invite_user failed")
            return False

    def on_message(self, callback: Callable) -> None:
        """Register incoming-message callback: async fn(room_id, sender, body)."""
        self._message_callbacks.append(callback)

    # ── File sharing ──

    async def upload_file(
        self,
        room_id: str,
        file_path: str,
        *,
        body: str | None = None,
        mime_type: str | None = None,
    ) -> str | None:
        """
        Upload a file to a Matrix room. Returns event_id or None.

        file_path : local path to the file
        body      : display name (defaults to filename)
        mime_type : MIME type (auto-detected if omitted)
        """
        if not self._client:
            logger.warning("Matrix client not initialised")
            return None
        import mimetypes
        from pathlib import Path as _P

        fp = _P(file_path)
        if not fp.exists():
            logger.error("File not found: %s", file_path)
            return None

        if body is None:
            body = fp.name
        if mime_type is None:
            mime_type = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"

        file_size = fp.stat().st_size
        if file_size == 0:
            logger.warning("Empty file, skipping upload: %s", file_path)
            return None

        try:
            from nio import RoomSendResponse, UploadResponse

            with open(fp, "rb") as f:
                resp, _keys = await self._client.upload(
                    f,
                    content_type=mime_type,
                    filename=body,
                    filesize=file_size,
                )
            if not isinstance(resp, UploadResponse):
                logger.warning("Matrix upload unexpected response: %s", resp)
                return None

            content_uri = resp.content_uri

            # Determine msgtype
            if mime_type.startswith("image/"):
                msgtype = "m.image"
            elif mime_type.startswith("audio/"):
                msgtype = "m.audio"
            elif mime_type.startswith("video/"):
                msgtype = "m.video"
            else:
                msgtype = "m.file"

            content = {
                "msgtype": msgtype,
                "body": body,
                "url": content_uri,
                "info": {
                    "mimetype": mime_type,
                    "size": file_size,
                },
            }

            target_room = room_id or self.cfg.default_room_id
            send_resp = await self._client.room_send(
                room_id=target_room,
                message_type="m.room.message",
                content=content,
            )
            if isinstance(send_resp, RoomSendResponse):
                logger.info("Matrix: uploaded %s to %s", body, target_room)
                return send_resp.event_id
            return None
        except Exception:
            logger.exception("Matrix upload_file failed")
            return None

    async def download_file(
        self,
        mxc_uri: str,
        dest_path: str,
    ) -> bool:
        """
        Download a file from a Matrix content URI (mxc://...).

        Returns True on success.
        """
        if not self._client:
            logger.warning("Matrix client not initialised")
            return False
        from pathlib import Path as _P

        try:
            from nio import DownloadResponse

            resp = await self._client.download(mxc_uri)
            if not isinstance(resp, DownloadResponse):
                logger.warning("Matrix download unexpected response: %s", resp)
                return False

            dest = _P(dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.body)
            logger.info("Matrix: downloaded %s -> %s", mxc_uri, dest_path)
            return True
        except Exception:
            logger.exception("Matrix download_file failed")
            return False

    # ── Query helpers (used by CLI / channel adapter) ──

    async def get_rooms(self) -> list[dict[str, Any]]:
        """List joined rooms with basic metadata."""
        if not self._client:
            return []
        rooms = []
        try:
            for room_id, room in self._client.rooms.items():
                rooms.append(
                    {
                        "room_id": room_id,
                        "name": getattr(room, "name", "")
                        or getattr(room, "display_name", "")
                        or "",
                        "topic": getattr(room, "topic", "") or "",
                        "member_count": getattr(room, "member_count", 0)
                        or len(getattr(room, "users", {})),
                    }
                )
        except Exception:
            logger.exception("get_rooms failed")

        # Sync joined rooms to persistent store
        if self._store and rooms:
            try:
                from navig.comms.matrix_store import MatrixRoom as _MR

                for r in rooms:
                    self._store.upsert_room(
                        _MR(
                            room_id=r["room_id"],
                            name=r["name"],
                            topic=r["topic"],
                        )
                    )
            except Exception:
                logger.debug("Matrix: room sync to store failed (non-fatal)")

        return rooms

    async def get_room_messages(self, room_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent messages from a room via /messages endpoint."""
        if not self._client:
            return []
        messages = []
        try:
            from nio import RoomMessagesResponse

            resp = await self._client.room_messages(
                room_id,
                start="",
                limit=limit,
            )
            if isinstance(resp, RoomMessagesResponse):
                for evt in reversed(resp.chunk):
                    if hasattr(evt, "body"):
                        messages.append(
                            {
                                "event_id": evt.event_id,
                                "sender": evt.sender,
                                "body": evt.body,
                                "timestamp": evt.server_timestamp,
                            }
                        )
        except Exception:
            logger.exception("get_room_messages failed")
        return messages

    async def get_room_members(self, room_id: str) -> list[dict[str, Any]]:
        """List members of a room."""
        if not self._client:
            return []
        members = []
        try:
            from nio import RoomMembersResponse

            resp = await self._client.room_members(room_id)
            if isinstance(resp, RoomMembersResponse):
                # Collect unique members
                seen = set()
                for evt in resp.chunk:
                    uid = evt.state_key
                    if uid in seen:
                        continue
                    seen.add(uid)
                    content = evt.content or {}
                    members.append(
                        {
                            "user_id": uid,
                            "display_name": content.get("displayname", ""),
                            "membership": content.get("membership", ""),
                            "power_level": 0,  # Enriched below if possible
                        }
                    )

            # Enrich with power levels
            room = self._client.rooms.get(room_id)
            if room and hasattr(room, "power_levels") and room.power_levels:
                pl = room.power_levels.users or {}
                for m in members:
                    m["power_level"] = pl.get(m["user_id"], 0)
        except Exception:
            logger.exception("get_room_members failed")
        return members

    async def _sync_loop(self) -> None:
        while self._running:
            try:
                await self._client.sync(timeout=30000, full_state=False)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Matrix sync error, retrying in 5s")
                await asyncio.sleep(5)

    async def _on_room_message(self, room, event) -> None:
        if event.sender == self.cfg.user_id:
            return

        # Persist event to store
        if self._store:
            try:
                from navig.comms.matrix_store import MatrixEvent as _ME

                self._store.add_event(
                    _ME(
                        event_id=event.event_id,
                        room_id=room.room_id,
                        sender=event.sender,
                        event_type="m.room.message",
                        content={"body": getattr(event, "body", "")},
                        origin_ts=getattr(event, "server_timestamp", 0) or int(time.time() * 1000),
                    )
                )
            except Exception:
                logger.debug("Matrix: event store failed (non-fatal)")

        for cb in self._message_callbacks:
            try:
                await cb(room.room_id, event.sender, event.body)
            except Exception:
                logger.exception("Matrix message callback error")

    async def _on_invite(self, room, event) -> None:
        if not self.cfg.auto_join:
            return
        try:
            await self._client.join(room.room_id)
            logger.info("Matrix: auto-joined %s", room.room_id)
        except Exception:
            logger.exception("Matrix auto-join failed for %s", room.room_id)

    # ── E2EE: key verification ──

    @property
    def e2ee_enabled(self) -> bool:
        """Whether E2EE is active for this session."""
        return self._e2ee_enabled

    def on_verification(self, callback: Callable) -> None:
        """Register a key-verification event callback:
        ``async fn(event_type: str, transaction_id: str, data: dict)``."""
        self._verification_callbacks.append(callback)

    async def _on_key_verification(self, event) -> None:
        """Handle incoming key verification events (SAS flow)."""
        etype = type(event).__name__
        txn_id = getattr(event, "transaction_id", "?")
        logger.info("Matrix: verification event %s (txn=%s)", etype, txn_id)

        for cb in self._verification_callbacks:
            try:
                data = {
                    "sender": getattr(event, "sender", ""),
                    "transaction_id": txn_id,
                }
                if inspect.iscoroutinefunction(cb):
                    await cb(etype, txn_id, data)
                else:
                    cb(etype, txn_id, data)
            except Exception:
                logger.exception("Matrix verification callback error")

    async def start_verification(self, user_id: str, device_id: str) -> str | None:
        """Start a SAS key verification with a specific device.

        Returns the transaction_id or None on failure.
        """
        if not self._client or not self._e2ee_enabled:
            logger.warning("Cannot start verification: E2EE not enabled")
            return None
        try:
            device = self._client.device_store.get(user_id, device_id)
            if not device:
                logger.warning("Unknown device %s/%s", user_id, device_id)
                return None
            resp = await self._client.start_key_verification(device)
            txn_id = getattr(resp, "transaction_id", None) if resp else None
            logger.info(
                "Matrix: started verification txn=%s with %s/%s",
                txn_id,
                user_id,
                device_id,
            )
            return txn_id
        except Exception:
            logger.exception("start_verification failed")
            return None

    async def accept_verification(self, transaction_id: str) -> bool:
        """Accept an incoming key verification request."""
        if not self._client or not self._e2ee_enabled:
            return False
        try:
            resp = await self._client.accept_key_verification(transaction_id)
            return resp is not None
        except Exception:
            logger.exception("accept_verification failed")
            return False

    async def confirm_verification(self, transaction_id: str) -> bool:
        """Confirm the SAS match (emoji or decimal)."""
        if not self._client or not self._e2ee_enabled:
            return False
        try:
            resp = await self._client.confirm_short_auth_string(transaction_id)
            return resp is not None
        except Exception:
            logger.exception("confirm_verification failed")
            return False

    async def cancel_verification(self, transaction_id: str) -> bool:
        """Cancel a verification session."""
        if not self._client or not self._e2ee_enabled:
            return False
        try:
            resp = await self._client.cancel_key_verification(transaction_id)
            return resp is not None
        except Exception:
            logger.exception("cancel_verification failed")
            return False

    async def get_verification_emoji(self, transaction_id: str) -> list | None:
        """Get the SAS emoji for the current verification.

        Returns list of (emoji, description) tuples, or None.
        """
        if not self._client or not self._e2ee_enabled:
            return None
        try:
            sas = self._client.key_verifications.get(transaction_id)
            if sas and hasattr(sas, "get_emoji"):
                return sas.get_emoji()
            return None
        except Exception:
            logger.exception("get_verification_emoji failed")
            return None

    async def trust_device(self, user_id: str, device_id: str) -> bool:
        """Manually mark a device as trusted (no SAS)."""
        if not self._client or not self._e2ee_enabled:
            logger.warning("Cannot trust device: E2EE not enabled")
            return False
        try:
            device = self._client.device_store.get(user_id, device_id)
            if not device:
                logger.warning("Unknown device %s/%s", user_id, device_id)
                return False
            self._client.verify_device(device)
            logger.info("Matrix: trusted device %s/%s", user_id, device_id)
            return True
        except Exception:
            logger.exception("trust_device failed")
            return False

    async def blacklist_device(self, user_id: str, device_id: str) -> bool:
        """Mark a device as blacklisted (do not send keys to it)."""
        if not self._client or not self._e2ee_enabled:
            return False
        try:
            device = self._client.device_store.get(user_id, device_id)
            if not device:
                return False
            self._client.blacklist_device(device)
            logger.info("Matrix: blacklisted device %s/%s", user_id, device_id)
            return True
        except Exception:
            logger.exception("blacklist_device failed")
            return False

    async def unverify_device(self, user_id: str, device_id: str) -> bool:
        """Remove trust from a device (set to unset)."""
        if not self._client or not self._e2ee_enabled:
            return False
        try:
            device = self._client.device_store.get(user_id, device_id)
            if not device:
                return False
            self._client.unverify_device(device)
            logger.info("Matrix: unverified device %s/%s", user_id, device_id)
            return True
        except Exception:
            logger.exception("unverify_device failed")
            return False

    async def get_devices(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """List known devices + trust state.

        If user_id is None, lists OWN devices from the server.
        If user_id is given, lists locally-known devices from the device store.
        """
        if not self._client:
            return []
        devices = []
        try:
            if user_id is None:
                # Own devices from server
                from nio import DevicesResponse

                resp = await self._client.devices()
                if isinstance(resp, DevicesResponse):
                    for d in resp.devices:
                        devices.append(
                            {
                                "device_id": d.id,
                                "display_name": d.display_name or "",
                                "last_seen_ip": getattr(d, "last_seen_ip", ""),
                                "last_seen_ts": getattr(d, "last_seen_date", ""),
                                "trust": "self",
                            }
                        )
            elif self._e2ee_enabled:
                # Other user's devices from local store
                user_devices = self._client.device_store.active_user_devices(user_id)
                for d in user_devices:
                    trust = "unset"
                    if hasattr(d, "trust_state"):
                        ts = d.trust_state
                        trust = ts.name if hasattr(ts, "name") else str(ts)
                    devices.append(
                        {
                            "device_id": d.id if hasattr(d, "id") else d.device_id,
                            "display_name": getattr(d, "display_name", ""),
                            "user_id": user_id,
                            "ed25519_key": (
                                getattr(d, "ed25519", "")[:20] + "..."
                                if getattr(d, "ed25519", "")
                                else ""
                            ),
                            "trust": trust,
                        }
                    )
        except Exception:
            logger.exception("get_devices failed")
        return devices
