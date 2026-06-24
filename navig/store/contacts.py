"""
ContactStore — Alias-based contact store with multi-network routes.

Stores contacts as alias → routes mappings.  Backed by :class:`BaseStore` for
WAL, schema versioning, and write serialisation.

Schema:
- ``contacts`` — one row per alias (display_name, default_network, fallbacks)
- ``contact_routes`` — junction table: one row per (alias, network, address)

Hot-reloadable from ``contacts.yaml`` via :meth:`load_yaml`.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from navig.messaging.adapter import Contact, Route
from navig.store.base import BaseStore, _utcnow

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────


def normalize_phone(phone: str | None) -> str:
    """Strip non-digits, preserve leading ``+``."""
    raw = str(phone or "").strip()
    if not raw:
        return ""
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    return f"+{digits}" if has_plus else digits


def _parse_route_string(route_str: str) -> tuple[str, str]:
    """Parse ``network:address`` → ``(network, address)``."""
    if ":" not in route_str:
        raise ValueError(f"Invalid route format (expected 'network:address'): {route_str!r}")
    network, _, address = route_str.partition(":")
    network = network.strip().lower()
    address = address.strip()
    if not network or not address:
        raise ValueError(f"Invalid route format (empty network or address): {route_str!r}")
    return network, address


# ── Store ─────────────────────────────────────────────────────


class ContactStore(BaseStore):
    """
    Alias-based contact store with multi-network route resolution.

    Usage::

        store = ContactStore()
        store.add_contact("alice", "Alice Dupont",
                          routes=["whatsapp:+33612345678", "discord:123456789"],
                          default_network="whatsapp")
        contact = store.resolve_alias("alice")
        assert contact.routes[0].network == "whatsapp"
    """

    SCHEMA_VERSION = 1
    PRAGMAS = {"cache_size": -2000}  # 2 MB — small dataset

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    # ── Schema ────────────────────────────────────────────────

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS contacts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                alias           TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name    TEXT NOT NULL DEFAULT '',
                default_network TEXT,
                fallbacks_json  TEXT DEFAULT '[]',
                created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE TABLE IF NOT EXISTS contact_routes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                network     TEXT NOT NULL COLLATE NOCASE,
                address     TEXT NOT NULL,
                priority    INTEGER NOT NULL DEFAULT 0,
                meta_json   TEXT DEFAULT '{}',
                UNIQUE(contact_id, network, address)
            );

            CREATE INDEX IF NOT EXISTS idx_contact_alias
                ON contacts (alias COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_route_contact
                ON contact_routes (contact_id, priority ASC);
        """)

    def _migrate(self, conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
        pass  # v1 is initial

    # ── Resolve ───────────────────────────────────────────────

    def resolve_alias(self, alias: str) -> Contact | None:
        """Resolve an alias to a full :class:`Contact` with routes."""
        alias = alias.lstrip("@").strip()
        row = self._read_one("SELECT * FROM contacts WHERE alias = ? COLLATE NOCASE", (alias,))
        if not row:
            return None
        return self._row_to_contact(row)

    def _row_to_contact(self, row: sqlite3.Row) -> Contact:
        contact_id = row["id"]
        routes_rows = self._read_all(
            "SELECT network, address, priority, meta_json "
            "FROM contact_routes WHERE contact_id = ? ORDER BY priority ASC",
            (contact_id,),
        )
        routes = [
            Route(
                network=r["network"],
                address=r["address"],
                priority=r["priority"],
                meta=json.loads(r["meta_json"] or "{}"),
            )
            for r in routes_rows
        ]
        fallbacks_raw = json.loads(row["fallbacks_json"] or "[]")
        return Contact(
            alias=row["alias"],
            display_name=row["display_name"],
            default_network=row["default_network"],
            routes=routes,
            fallbacks=fallbacks_raw if isinstance(fallbacks_raw, list) else [],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    # ── CRUD ──────────────────────────────────────────────────

    def add_contact(
        self,
        alias: str,
        display_name: str = "",
        *,
        routes: list[str] | None = None,
        default_network: str | None = None,
        fallbacks: list[str] | None = None,
    ) -> Contact:
        """Add a new contact. ``routes`` are ``"network:address"`` strings."""
        alias = alias.lstrip("@").strip()
        now = _utcnow()
        fallbacks_json = json.dumps(fallbacks or [])

        cursor = self._write(
            "INSERT INTO contacts (alias, display_name, default_network, "
            "fallbacks_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (alias, display_name, default_network, fallbacks_json, now, now),
        )
        contact_id = cursor.lastrowid

        if routes:
            route_params = []
            for idx, route_str in enumerate(routes):
                network, address = _parse_route_string(route_str)
                route_params.append((contact_id, network, address, idx, "{}"))
            self._write_many(
                "INSERT INTO contact_routes "
                "(contact_id, network, address, priority, meta_json) "
                "VALUES (?, ?, ?, ?, ?)",
                route_params,
            )

        return self.resolve_alias(alias)  # type: ignore[return-value]

    def update_contact(
        self,
        alias: str,
        *,
        display_name: str | None = None,
        default_network: str | None = ...,  # type: ignore[assignment]
    ) -> bool:
        """Update mutable fields. Returns ``True`` if updated."""
        alias = alias.lstrip("@").strip()
        sets: list[str] = []
        params: list[Any] = []
        if display_name is not None:
            sets.append("display_name = ?")
            params.append(display_name)
        if default_network is not ...:
            sets.append("default_network = ?")
            params.append(default_network)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(_utcnow())
        params.append(alias)
        cursor = self._write(
            f"UPDATE contacts SET {', '.join(sets)} WHERE alias = ? COLLATE NOCASE",
            tuple(params),
        )
        return cursor.rowcount > 0

    def remove_contact(self, alias: str) -> bool:
        """Delete a contact and all its routes."""
        alias = alias.lstrip("@").strip()
        cursor = self._write("DELETE FROM contacts WHERE alias = ? COLLATE NOCASE", (alias,))
        return cursor.rowcount > 0

    def list_contacts(self, limit: int = 200) -> list[Contact]:
        """List all contacts ordered by alias."""
        rows = self._read_all("SELECT * FROM contacts ORDER BY alias ASC LIMIT ?", (limit,))
        return [self._row_to_contact(r) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[Contact]:
        """Search contacts by alias or display_name prefix."""
        pattern = f"%{query}%"
        rows = self._read_all(
            "SELECT * FROM contacts WHERE alias LIKE ? OR display_name LIKE ? "
            "ORDER BY alias ASC LIMIT ?",
            (pattern, pattern, limit),
        )
        return [self._row_to_contact(r) for r in rows]

    # ── Route manipulation ────────────────────────────────────

    def add_route(self, alias: str, route_str: str, priority: int | None = None) -> bool:
        """Add a route to an existing contact."""
        alias = alias.lstrip("@").strip()
        row = self._read_one("SELECT id FROM contacts WHERE alias = ? COLLATE NOCASE", (alias,))
        if not row:
            return False
        contact_id = row["id"]
        network, address = _parse_route_string(route_str)
        if priority is None:
            max_row = self._read_one(
                "SELECT COALESCE(MAX(priority), -1) AS mp FROM contact_routes WHERE contact_id = ?",
                (contact_id,),
            )
            priority = (max_row["mp"] + 1) if max_row else 0
        self._write(
            "INSERT OR IGNORE INTO contact_routes "
            "(contact_id, network, address, priority, meta_json) "
            "VALUES (?, ?, ?, ?, '{}')",
            (contact_id, network, address, priority),
        )
        self._write(
            "UPDATE contacts SET updated_at = ? WHERE id = ?",
            (_utcnow(), contact_id),
        )
        return True

    def remove_route(self, alias: str, route_str: str) -> bool:
        """Remove a specific route from a contact."""
        alias = alias.lstrip("@").strip()
        row = self._read_one("SELECT id FROM contacts WHERE alias = ? COLLATE NOCASE", (alias,))
        if not row:
            return False
        contact_id = row["id"]
        network, address = _parse_route_string(route_str)
        cursor = self._write(
            "DELETE FROM contact_routes WHERE contact_id = ? AND network = ? AND address = ?",
            (contact_id, network, address),
        )
        return cursor.rowcount > 0

    def set_default_network(self, alias: str, network: str) -> bool:
        """Set the default_network for a contact."""
        return self.update_contact(alias, default_network=network)

    def set_fallbacks(self, alias: str, fallbacks: list[str]) -> bool:
        """Set fallback routes for a contact."""
        alias = alias.lstrip("@").strip()
        cursor = self._write(
            "UPDATE contacts SET fallbacks_json = ?, updated_at = ? WHERE alias = ? COLLATE NOCASE",
            (json.dumps(fallbacks), _utcnow(), alias),
        )
        return cursor.rowcount > 0

    # ── YAML bulk load ────────────────────────────────────────

    def load_yaml(self, yaml_path: Path) -> int:
        """
        Load contacts from a YAML file (additive / upsert).

        Expected format::

            alice:
              display_name: Alice Dupont
              default_network: whatsapp
              routes:
                - whatsapp:+33612345678
                - discord:123456789
              fallbacks:
                - sms:+33612345678

        Returns the number of contacts processed.
        """
        import yaml  # lazy — yaml is already a project dep

        text = yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            return 0

        count = 0
        for alias, info in data.items():
            if not isinstance(info, dict):
                continue
            existing = self.resolve_alias(alias)
            if existing:
                # Update existing
                self.update_contact(
                    alias,
                    display_name=info.get("display_name", existing.display_name),
                    default_network=info.get("default_network", existing.default_network),
                )
                if "fallbacks" in info:
                    self.set_fallbacks(alias, info["fallbacks"])
                if "routes" in info:
                    for route_str in info["routes"]:
                        self.add_route(alias, route_str)
            else:
                self.add_contact(
                    alias,
                    display_name=info.get("display_name", ""),
                    routes=info.get("routes", []),
                    default_network=info.get("default_network"),
                    fallbacks=info.get("fallbacks", []),
                )
            count += 1
        return count


# ── Singleton ─────────────────────────────────────────────────

_store: ContactStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.data_dir() / "contacts.db"


def get_contact_store() -> ContactStore:
    """Return the global :class:`ContactStore` singleton."""
    global _store
    if _store is None:
        _store = ContactStore()
    return _store
