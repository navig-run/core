"""
Matrix E2EE Helper Module

Provides high-level abstractions for:
- Device listing & trust management
- SAS (Short Authentication String) verification flows
- Key import/export helpers
- E2EE session diagnostics

Requires ``matrix-nio[e2e]`` (includes libolm bindings).
All methods accept a NavigMatrixBot instance and delegate to its client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Trust State Enum ──


class DeviceTrust(str, Enum):
    """Human-readable trust state for device display."""

    verified = "verified"
    blacklisted = "blacklisted"
    ignored = "ignored"
    unset = "unset"
    unknown = "unknown"

    @classmethod
    def from_nio(cls, trust_state) -> "DeviceTrust":
        """Map matrix-nio TrustState to our enum."""
        name = getattr(trust_state, "name", str(trust_state)).lower()
        mapping = {
            "verified": cls.verified,
            "blacklisted": cls.blacklisted,
            "ignored": cls.ignored,
            "unset": cls.unset,
        }
        return mapping.get(name, cls.unknown)


# ── Data Classes ──


@dataclass
class DeviceInfo:
    """Normalized device information."""

    device_id: str
    user_id: str = ""
    display_name: str = ""
    ed25519_key: str = ""
    curve25519_key: str = ""
    trust: DeviceTrust = DeviceTrust.unset
    is_own: bool = False
    last_seen_ip: str = ""
    last_seen_ts: str = ""

    def short_key(self, length: int = 20) -> str:
        """Return abbreviated ed25519 key for display."""
        if not self.ed25519_key:
            return ""
        return self.ed25519_key[:length] + "..."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "user_id": self.user_id,
            "display_name": self.display_name,
            "ed25519_key": self.short_key(),
            "trust": self.trust.value,
            "is_own": self.is_own,
        }


@dataclass
class VerificationSession:
    """Tracks a SAS verification session."""

    transaction_id: str
    user_id: str
    device_id: str
    state: str = "initiated"  # initiated, accepted, key_received, confirmed, cancelled
    emoji: List[Tuple[str, str]] = field(default_factory=list)
    decimals: Tuple[int, ...] = ()
    canceled_by: str = ""
    cancel_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "user_id": self.user_id,
            "device_id": self.device_id,
            "state": self.state,
            "emoji": [{"emoji": e, "description": d} for e, d in self.emoji],
        }


# ── E2EE Manager ──


class MatrixE2EEManager:
    """High-level E2EE management attached to a NavigMatrixBot instance.

    Usage::

        from navig.comms.matrix import get_matrix_bot
        from navig.comms.matrix_e2ee import MatrixE2EEManager

        bot = get_matrix_bot()
        mgr = MatrixE2EEManager(bot)
        devices = await mgr.list_devices("@alice:navig.local")
    """

    def __init__(self, bot) -> None:
        from navig.comms.matrix import NavigMatrixBot

        if not isinstance(bot, NavigMatrixBot):
            raise TypeError("Expected NavigMatrixBot instance")
        self._bot = bot
        self._sessions: Dict[str, VerificationSession] = {}

        # Register our verification callback
        bot.on_verification(self._on_verification_event)

    @property
    def client(self):
        return self._bot._client

    @property
    def e2ee_ok(self) -> bool:
        return self._bot.e2ee_enabled and self.client is not None

    # ── Device Listing ──

    async def list_own_devices(self) -> List[DeviceInfo]:
        """List bot's own devices from the server."""
        raw = await self._bot.get_devices(user_id=None)
        return [
            DeviceInfo(
                device_id=d["device_id"],
                user_id=self._bot.cfg.user_id,
                display_name=d.get("display_name", ""),
                trust=DeviceTrust.verified,  # own devices
                is_own=True,
                last_seen_ip=d.get("last_seen_ip", ""),
                last_seen_ts=d.get("last_seen_ts", ""),
            )
            for d in raw
        ]

    async def list_devices(self, user_id: str) -> List[DeviceInfo]:
        """List known devices for a user from the local device store."""
        raw = await self._bot.get_devices(user_id=user_id)
        return [
            DeviceInfo(
                device_id=d["device_id"],
                user_id=d.get("user_id", user_id),
                display_name=d.get("display_name", ""),
                ed25519_key=d.get("ed25519_key", ""),
                trust=(
                    DeviceTrust(d.get("trust", "unset"))
                    if d.get("trust", "unset") in DeviceTrust.__members__
                    else DeviceTrust.unknown
                ),
            )
            for d in raw
        ]

    async def get_all_tracked_users(self) -> List[str]:
        """Get all user IDs the client tracks devices for."""
        if not self.e2ee_ok:
            return []
        try:
            store = self.client.device_store
            return list(store.users) if hasattr(store, "users") else []
        except Exception:
            logger.exception("get_all_tracked_users failed")
            return []

    # ── Trust Management ──

    async def trust_device(self, user_id: str, device_id: str) -> bool:
        """Manually verify (trust) a device."""
        return await self._bot.trust_device(user_id, device_id)

    async def blacklist_device(self, user_id: str, device_id: str) -> bool:
        """Blacklist a device (don't send keys to it)."""
        return await self._bot.blacklist_device(user_id, device_id)

    async def unverify_device(self, user_id: str, device_id: str) -> bool:
        """Remove trust from a device."""
        return await self._bot.unverify_device(user_id, device_id)

    async def trust_all_devices(self, user_id: str) -> int:
        """Trust all known devices for a user. Returns count verified."""
        devices = await self.list_devices(user_id)
        count = 0
        for d in devices:
            if d.trust not in (DeviceTrust.verified,):
                ok = await self.trust_device(user_id, d.device_id)
                if ok:
                    count += 1
        return count

    # ── SAS Verification Flow ──

    async def start_verification(
        self, user_id: str, device_id: str
    ) -> Optional[VerificationSession]:
        """Initiate SAS verification with a specific device."""
        txn_id = await self._bot.start_verification(user_id, device_id)
        if not txn_id:
            return None
        session = VerificationSession(
            transaction_id=txn_id,
            user_id=user_id,
            device_id=device_id,
            state="initiated",
        )
        self._sessions[txn_id] = session
        return session

    async def accept_verification(self, transaction_id: str) -> bool:
        """Accept an incoming verification request."""
        ok = await self._bot.accept_verification(transaction_id)
        if ok and transaction_id in self._sessions:
            self._sessions[transaction_id].state = "accepted"
        return ok

    async def get_emoji(self, transaction_id: str) -> List[Tuple[str, str]]:
        """Get the SAS emoji for confirming the match."""
        result = await self._bot.get_verification_emoji(transaction_id)
        if result and transaction_id in self._sessions:
            self._sessions[transaction_id].emoji = result
        return result or []

    async def confirm_verification(self, transaction_id: str) -> bool:
        """Confirm the SAS match (user confirmed emoji are identical)."""
        ok = await self._bot.confirm_verification(transaction_id)
        if ok and transaction_id in self._sessions:
            self._sessions[transaction_id].state = "confirmed"
        return ok

    async def cancel_verification(self, transaction_id: str) -> bool:
        """Cancel a verification session."""
        ok = await self._bot.cancel_verification(transaction_id)
        if ok and transaction_id in self._sessions:
            self._sessions[transaction_id].state = "cancelled"
        return ok

    def get_session(self, transaction_id: str) -> Optional[VerificationSession]:
        """Get a tracked verification session."""
        return self._sessions.get(transaction_id)

    def get_active_sessions(self) -> List[VerificationSession]:
        """Get all non-terminal sessions."""
        return [
            s
            for s in self._sessions.values()
            if s.state not in ("confirmed", "cancelled")
        ]

    # ── Verification Callback ──

    async def _on_verification_event(
        self, event_type: str, transaction_id: str, data: dict
    ) -> None:
        """Handle verification events dispatched by NavigMatrixBot."""
        logger.debug("E2EE verification event: %s txn=%s", event_type, transaction_id)

        session = self._sessions.get(transaction_id)

        if event_type == "KeyVerificationStart":
            if not session:
                self._sessions[transaction_id] = VerificationSession(
                    transaction_id=transaction_id,
                    user_id=data.get("sender", ""),
                    device_id="",
                    state="incoming",
                )

        elif event_type == "KeyVerificationKey":
            if session:
                session.state = "key_received"
                # Fetch emoji when key is received
                emoji = await self.get_emoji(transaction_id)
                if emoji:
                    session.emoji = emoji

        elif event_type == "KeyVerificationMac":
            if session:
                session.state = "confirmed"

        elif event_type == "KeyVerificationCancel":
            if session:
                session.state = "cancelled"
                session.canceled_by = data.get("sender", "")

    # ── Diagnostics ──

    async def e2ee_status(self) -> Dict[str, Any]:
        """Return E2EE diagnostic information."""
        from navig.comms.matrix import HAS_OLM

        status: Dict[str, Any] = {
            "olm_installed": HAS_OLM,
            "e2ee_enabled": self._bot._e2ee_enabled,
            "e2ee_config": self._bot.cfg.e2ee,
            "store_path": self._bot.cfg.store_path,
            "active_verifications": len(self.get_active_sessions()),
        }
        if self.e2ee_ok:
            try:
                status["device_id"] = self.client.device_id
                status["user_id"] = self.client.user_id
                # Key fingerprint
                if hasattr(self.client, "olm") and self.client.olm:
                    acct = self.client.olm.account
                    if hasattr(acct, "identity_keys"):
                        keys = acct.identity_keys
                        status["ed25519"] = keys.get("ed25519", "")[:20] + "..."
                        status["curve25519"] = keys.get("curve25519", "")[:20] + "..."
            except Exception:
                logger.debug("Could not gather full E2EE status")
        return status

    async def export_keys(self, filepath: str, passphrase: str) -> bool:
        """Export E2EE room keys to a file (encrypted with passphrase)."""
        if not self.e2ee_ok:
            return False
        try:
            await self.client.export_keys(filepath, passphrase)
            logger.info("Matrix: exported keys to %s", filepath)
            return True
        except Exception:
            logger.exception("export_keys failed")
            return False

    async def import_keys(self, filepath: str, passphrase: str) -> bool:
        """Import E2EE room keys from a file."""
        if not self.e2ee_ok:
            return False
        try:
            await self.client.import_keys(filepath, passphrase)
            logger.info("Matrix: imported keys from %s", filepath)
            return True
        except Exception:
            logger.exception("import_keys failed")
            return False
