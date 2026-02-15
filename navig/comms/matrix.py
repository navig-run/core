"""
NavigMatrixBot -- optional Matrix channel backend for navig.comms.

Uses `matrix-nio` (lazy-imported) so the package is only required when
``comms.matrix.enabled`` is true in config.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)

_bot: Optional["NavigMatrixBot"] = None


def get_matrix_bot() -> Optional["NavigMatrixBot"]:
    """Return the singleton NavigMatrixBot or None."""
    return _bot


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
    def from_dict(cls, d: Dict[str, Any]) -> "MatrixConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class NavigMatrixBot:
    """Thin wrapper around matrix-nio AsyncClient."""

    def __init__(self, config: Dict[str, Any] | MatrixConfig):
        if isinstance(config, dict):
            config = MatrixConfig.from_dict(config)
        self.cfg = config
        self._client = None
        self._sync_task: Optional[asyncio.Task] = None
        self._running = False
        self._message_callbacks: List[Callable] = []

    async def start(self) -> None:
        """Login and begin background sync."""
        global _bot
        try:
            from nio import AsyncClient, LoginResponse, RoomMessageText, InviteMemberEvent
        except ImportError:
            logger.error("matrix-nio is not installed. pip install matrix-nio[e2e]")
            raise ImportError("matrix-nio required for Matrix support. pip install matrix-nio[e2e]")

        self._client = AsyncClient(
            self.cfg.homeserver_url,
            self.cfg.user_id,
            store_path=self.cfg.store_path or None,
            device_id=self.cfg.device_name,
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

    async def stop(self) -> None:
        """Stop sync and close."""
        global _bot
        self._running = False
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.close()
            self._client = None
        _bot = None
        logger.info("Matrix bot stopped")

    @property
    def is_running(self) -> bool:
        return self._running and self._client is not None

    async def send_message(self, room_id: str, message: str) -> Optional[str]:
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

    async def send_notice(self, room_id: str, message: str) -> Optional[str]:
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
        self, name: str, *, topic: str = "",
        is_public: bool = False, invite_user_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Create a room. Returns room_id or None."""
        if not self._client:
            return None
        try:
            from nio import RoomCreateResponse, RoomVisibility
            resp = await self._client.room_create(
                name=name, topic=topic,
                visibility=RoomVisibility.public if is_public else RoomVisibility.private,
                invite=invite_user_ids or [],
            )
            if isinstance(resp, RoomCreateResponse):
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
        body: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Optional[str]:
        """
        Upload a file to a Matrix room. Returns event_id or None.

        file_path : local path to the file
        body      : display name (defaults to filename)
        mime_type : MIME type (auto-detected if omitted)
        """
        if not self._client:
            logger.warning("Matrix client not initialised")
            return None
        import os
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
            from nio import UploadResponse, RoomSendResponse

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

    async def get_rooms(self) -> List[Dict[str, Any]]:
        """List joined rooms with basic metadata."""
        if not self._client:
            return []
        rooms = []
        try:
            for room_id, room in self._client.rooms.items():
                rooms.append({
                    "room_id": room_id,
                    "name": getattr(room, "name", "") or getattr(room, "display_name", "") or "",
                    "topic": getattr(room, "topic", "") or "",
                    "member_count": getattr(room, "member_count", 0) or len(getattr(room, "users", {})),
                })
        except Exception:
            logger.exception("get_rooms failed")
        return rooms

    async def get_room_messages(
        self, room_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Fetch recent messages from a room via /messages endpoint."""
        if not self._client:
            return []
        messages = []
        try:
            from nio import RoomMessagesResponse

            resp = await self._client.room_messages(
                room_id, start="", limit=limit,
            )
            if isinstance(resp, RoomMessagesResponse):
                for evt in reversed(resp.chunk):
                    if hasattr(evt, "body"):
                        messages.append({
                            "event_id": evt.event_id,
                            "sender": evt.sender,
                            "body": evt.body,
                            "timestamp": evt.server_timestamp,
                        })
        except Exception:
            logger.exception("get_room_messages failed")
        return messages

    async def get_room_members(self, room_id: str) -> List[Dict[str, Any]]:
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
                    members.append({
                        "user_id": uid,
                        "display_name": content.get("displayname", ""),
                        "membership": content.get("membership", ""),
                        "power_level": 0,  # Enriched below if possible
                    })

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
