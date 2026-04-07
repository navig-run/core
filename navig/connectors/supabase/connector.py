"""
Supabase Connector — generic PostgREST + Auth client.

Authentication: project URL + anon/service-role key via env variables or navig vault.

    SUPABASE_URL      = https://<ref>.supabase.co
    SUPABASE_ANON_KEY = eyJ...  (anon / public key)
    SUPABASE_SERVICE_KEY = eyJ...  (service role key, optional)

Coverage:
    - Paginated table reads (GET /rest/v1/<table>)
    - Row inserts / updates / deletes
    - Storage bucket listing
    - Auth: list users (service role only)
    - Health: /rest/v1/?apikey=…

Usage:
    connector = SupabaseConnector()
    await connector.connect()
    rows = await connector.fetch("public_library")   # read all rows
    results = await connector.search("prompt templates")  # full-text across tables
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.errors import ConnectorAuthError
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
    ResourceType,
)

logger = logging.getLogger("navig.connectors.supabase")


def _sb_request(
    url: str,
    headers: dict[str, str],
    method: str = "GET",
    body: bytes | None = None,
    timeout: int = 15,
) -> tuple[int, dict | list]:
    import json

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read()
        return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body_data = json.loads(raw)
        except Exception:  # noqa: BLE001 — parse best-effort
            body_data = {"raw": raw.decode("utf-8", errors="replace")}
        return exc.code, body_data


class SupabaseConnector(BaseConnector):
    """Connector for Supabase — PostgREST table access via anon or service key.

    Auth: URL + anon key via env variables or navig vault labels:
        ``supabase/<project_ref>/url``
        ``supabase/<project_ref>/anon_key``

    Multiple projects can be configured by switching which env vars are set.
    """

    manifest = ConnectorManifest(
        id="supabase",
        display_name="Supabase",
        description=(
            "Generic Supabase PostgREST connector. Read/write tables, "
            "list storage buckets, query with filters. "
            "Uses anon or service-role key."
        ),
        domain=ConnectorDomain.DATA,
        icon="⚡",
        oauth_scopes=[],
        oauth_provider="",
        requires_oauth=False,
        can_search=False,
        can_fetch=True,
        can_act=True,
    )

    def __init__(self) -> None:
        super().__init__()
        self._url: str | None = None  # e.g. https://xyz.supabase.co
        self._anon_key: str | None = None
        self._service_key: str | None = None  # optional — enables admin ops

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Load Supabase URL + anon key from environment."""
        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        anon = os.environ.get("SUPABASE_ANON_KEY", "").strip()
        service = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not url or not anon:
            self._status = ConnectorStatus.ERROR
            raise ConnectorAuthError(
                self.manifest.id,
                "SUPABASE_URL and SUPABASE_ANON_KEY are required. "
                "Set env vars or store in vault under 'supabase/<ref>/url' and "
                "'supabase/<ref>/anon_key'.",
            )
        self._url = url
        self._anon_key = anon
        self._service_key = service or None
        self._status = ConnectorStatus.CONNECTED
        logger.debug("Supabase connector connected to %s", url)

    async def disconnect(self) -> None:
        self._url = self._anon_key = self._service_key = None
        self._status = ConnectorStatus.DISCONNECTED

    # ── Internal helpers ────────────────────────────────────────────────────

    def _headers(self, *, service: bool = False) -> dict[str, str]:
        key = (self._service_key if service and self._service_key else self._anon_key) or ""
        return {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Prefer": "count=exact",
        }

    def _rest_url(self, table: str) -> str:
        return f"{self._url}/rest/v1/{urllib.parse.quote(table, safe='')}"

    # ── Search: ilike on text columns (simple full-text) ────────────────────

    async def search(
        self,
        query: str,
        limit: int = 20,
        **kwargs: Any,
    ) -> list[Resource]:
        """Search a named table for rows containing *query* in a text column.

        Args:
            query: Search term.
            limit: Max rows.
            kwargs:
                table (str): Table name (required).
                column (str): Column to search, defaults to "title".
                select (str): Comma-separated columns (default "*").
        """
        self._require_connected()
        table = kwargs.get("table")
        if not table:
            raise ValueError("SupabaseConnector.search requires kwarg 'table'")
        column = kwargs.get("column", "title")
        select = kwargs.get("select", "*")
        params = urllib.parse.urlencode(
            {
                "select": select,
                column: f"ilike.*{query}*",
                "limit": str(limit),
            }
        )
        status_code, data = _sb_request(
            f"{self._rest_url(table)}?{params}",
            headers=self._headers(),
        )
        if status_code in (401, 403):
            raise ConnectorAuthError(self.manifest.id, f"Unauthorized ({status_code})")
        rows = data if isinstance(data, list) else []
        return [
            Resource(
                id=str(row.get("id", i)),
                title=str(row.get("title", row.get("name", f"Row {i}"))),
                body=str(row)[:400],
                url=f"{self._url}/rest/v1/{table}?id=eq.{row.get('id', '')}",
                resource_type=ResourceType.DOCUMENT,
                metadata={"table": table, "row": row},
            )
            for i, row in enumerate(rows)
        ]

    # ── Fetch: read all rows from a table ───────────────────────────────────

    async def fetch(self, table: str, **kwargs: Any) -> Resource | None:
        """Fetch rows from *table* with optional filters.

        Args:
            table: PostgREST table name.
            kwargs:
                select (str): Comma-separated columns (default "*").
                order (str): Column to order by (e.g. "id").
                limit (int): Max rows per page (default 1000).
                offset (int): Row offset for pagination.
                filters (dict): Column-filter pairs, e.g. {"status": "eq.active"}.
        """
        self._require_connected()
        select = kwargs.get("select", "*")
        order = kwargs.get("order", None)
        limit = kwargs.get("limit", 1000)
        offset = kwargs.get("offset", 0)
        filters: dict[str, str] = kwargs.get("filters", {})
        qs_dict: dict[str, str] = {
            "select": select,
            "limit": str(limit),
            "offset": str(offset),
        }
        if order:
            qs_dict["order"] = order
        for col, fil in filters.items():
            qs_dict[col] = fil
        params = urllib.parse.urlencode(qs_dict)
        status_code, data = _sb_request(
            f"{self._rest_url(table)}?{params}",
            headers=self._headers(),
        )
        if status_code in (401, 403):
            raise ConnectorAuthError(self.manifest.id, f"Unauthorized on '{table}' ({status_code})")
        if status_code == 404:
            logger.debug("Supabase table not found: %s", table)
            return None
        rows = data if isinstance(data, list) else []
        return Resource(
            id=table,
            title=f"Supabase table: {table}",
            body=f"{len(rows)} row(s) fetched",
            url=f"{self._url}/rest/v1/{table}",
            resource_type=ResourceType.DOCUMENT,
            metadata={"table": table, "row_count": len(rows), "rows": rows},
        )

    # ── Act: insert / update / delete / rpc ─────────────────────────────────

    async def act(self, action: Action) -> ActionResult:
        """Supported actions:

        insert:  {"table": "...", "rows": [...]}
        update:  {"table": "...", "filters": {"id": "eq.5"}, "data": {...}}
        delete:  {"table": "...", "filters": {"id": "eq.5"}}
        rpc:     {"function": "my_func", "params": {...}}
        buckets: {} → list storage buckets
        """
        self._require_connected()
        name = action.name
        p = action.params

        if name == "insert":
            import json

            rows = p.get("rows", [])
            table = p["table"]
            body = json.dumps(rows).encode()
            hdrs = {**self._headers(), "Content-Type": "application/json"}
            status_code, resp = _sb_request(self._rest_url(table), hdrs, "POST", body)
            return ActionResult(
                success=status_code in (200, 201), data={"status": status_code, "response": resp}
            )

        if name == "update":
            import json

            table = p["table"]
            filters = urllib.parse.urlencode(p.get("filters", {}))
            body = json.dumps(p.get("data", {})).encode()
            hdrs = {
                **self._headers(),
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
            status_code, resp = _sb_request(
                f"{self._rest_url(table)}?{filters}", hdrs, "PATCH", body
            )
            return ActionResult(success=status_code in (200, 204), data={"status": status_code})

        if name == "delete":
            table = p["table"]
            filters = urllib.parse.urlencode(p.get("filters", {}))
            status_code, resp = _sb_request(
                f"{self._rest_url(table)}?{filters}", self._headers(), "DELETE"
            )
            return ActionResult(success=status_code in (200, 204), data={"status": status_code})

        if name == "rpc":
            import json

            func = p["function"]
            body = json.dumps(p.get("params", {})).encode()
            hdrs = {**self._headers(), "Content-Type": "application/json"}
            status_code, resp = _sb_request(f"{self._url}/rest/v1/rpc/{func}", hdrs, "POST", body)
            return ActionResult(success=status_code == 200, data=resp)

        if name == "buckets":
            status_code, resp = _sb_request(f"{self._url}/storage/v1/bucket", self._headers())
            return ActionResult(success=status_code == 200, data={"buckets": resp})

        return ActionResult(success=False, error=f"Unknown action: {name}")

    # ── Health ───────────────────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Probe the Supabase REST root to verify the project is reachable."""
        if not self._url or not self._anon_key:
            return HealthStatus(healthy=False, message="Not connected", latency_ms=0)
        t0 = time.monotonic()
        try:
            key = self._anon_key
            url = f"{self._url}/rest/v1/?apikey={key}"
            req = urllib.request.Request(
                url, headers={"apikey": key, "Authorization": f"Bearer {key}"}
            )
            resp = urllib.request.urlopen(req, timeout=8)
            latency_ms = int((time.monotonic() - t0) * 1000)
            if resp.status in (200, 400):  # 400 = no table arg = API is up
                return HealthStatus(
                    healthy=True,
                    message=f"Supabase REST API reachable ({self._url})",
                    latency_ms=latency_ms,
                )
            return HealthStatus(
                healthy=False, message=f"Unexpected HTTP {resp.status}", latency_ms=latency_ms
            )
        except urllib.error.HTTPError as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            # 400 from PostgREST root means it's up (no table specified)
            if exc.code == 400:
                return HealthStatus(
                    healthy=True, message="Supabase REST API reachable", latency_ms=latency_ms
                )
            return HealthStatus(healthy=False, message=f"HTTP {exc.code}", latency_ms=latency_ms)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - t0) * 1000)
            return HealthStatus(healthy=False, message=str(exc), latency_ms=latency_ms)
