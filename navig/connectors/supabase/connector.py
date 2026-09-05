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
from navig.connectors.errors import ConnectorAPIError, ConnectorAuthError
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


def _err_detail(data: dict | list) -> str:
    """Pull a short human message out of a PostgREST error body."""
    if isinstance(data, dict):
        return str(data.get("message") or data.get("msg") or data.get("hint") or data.get("raw") or data)[:200]
    return str(data)[:200]


def _act_result(label: str, status_code: int, resp: dict | list, ok_codes: tuple[int, ...]) -> ActionResult:
    """Build a valid ``ActionResult`` for a Supabase write/RPC op.

    On failure it surfaces the HTTP status + PostgREST message in ``error`` (never a phantom
    success); on success it carries the response payload in ``resource.metadata`` so callers can
    read the created rows / RPC return / bucket list (``ActionResult`` has no ``data`` field —
    the previous code passed ``data=`` and raised ``TypeError`` on every call).
    """
    if status_code not in ok_codes:
        return ActionResult(success=False, error=f"HTTP {status_code}: {_err_detail(resp)}")
    return ActionResult(
        success=True,
        resource=Resource(
            id=label,
            source="supabase",
            title=label,
            preview=str(resp)[:400],
            resource_type=ResourceType.DOCUMENT,
            metadata={"status": status_code, "response": resp},
        ),
    )


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

    def _require_connected(self) -> None:
        """Guard called at the top of every read/write path.

        Without it, ``search``/``fetch``/``act`` referenced a method that did not exist — every
        call raised ``AttributeError`` before doing anything, so the connector was dead on arrival
        (hidden because it had no tests). Now it fails with a clear, catchable message when
        ``connect()`` was never called or failed (no URL / key), instead of firing a request at
        ``https://None/...``.
        """
        if not self._url or not self._anon_key:
            raise ConnectorAuthError(
                self.manifest.id,
                "Not connected — call connect() first (needs SUPABASE_URL + SUPABASE_ANON_KEY).",
            )

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
        if not 200 <= status_code < 300:
            # A bad column/table, malformed filter, rate limit or server error returns an error
            # body (a dict, not a row list). Surfacing it as an empty result would make a FAILED
            # query look identical to "no matches" — a phantom-empty. Raise instead.
            raise ConnectorAPIError(self.manifest.id, status_code, _err_detail(data))
        rows = data if isinstance(data, list) else []
        return [
            Resource(
                id=str(row.get("id", i)),
                source="supabase",
                title=str(row.get("title", row.get("name", f"Row {i}"))),
                preview=str(row)[:400],
                url=f"{self._url}/rest/v1/{table}?id=eq.{row.get('id', '')}",
                resource_type=ResourceType.DOCUMENT,
                metadata={"table": table, "row": row},
            )
            for i, row in enumerate(rows)
        ]

    # ── Fetch: read all rows from a table ───────────────────────────────────

    async def fetch(self, resource_id: str, **kwargs: Any) -> Resource | None:
        """Fetch rows from *resource_id* (a table) with optional filters.

        Args:
            resource_id: PostgREST table name.
            kwargs:
                select (str): Comma-separated columns (default "*").
                order (str): Column to order by (e.g. "id").
                limit (int): Max rows per page (default 1000).
                offset (int): Row offset for pagination.
                filters (dict): Column-filter pairs, e.g. {"status": "eq.active"}.
        """
        # `resource_id` is the base-class parameter name (BaseConnector.fetch);
        # 9 of 12 connectors already use it. Bound to the domain name here so the
        # body, its log lines and its `metadata` keys stay unchanged.
        table = resource_id
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
        if not 200 <= status_code < 300:
            # A bad filter / server error must not be reported as "0 row(s) fetched" success.
            raise ConnectorAPIError(self.manifest.id, status_code, _err_detail(data))
        rows = data if isinstance(data, list) else []
        # We requested at most `limit` rows and don't auto-paginate, so an exact-`limit` result
        # means MORE rows almost certainly exist — flag it rather than presenting a capped read
        # as the whole table (the module docstring's "read all rows" promise).
        truncated = len(rows) == int(limit)
        body = f"{len(rows)} row(s) fetched"
        if truncated:
            body += f" (limit {limit} reached — more rows may exist; raise `limit` or page with `offset`)"
        return Resource(
            id=table,
            source="supabase",
            title=f"Supabase table: {table}",
            preview=body,
            url=f"{self._url}/rest/v1/{table}",
            resource_type=ResourceType.DOCUMENT,
            metadata={"table": table, "row_count": len(rows), "rows": rows, "truncated": truncated},
        )

    # ── Act: insert / update / delete / rpc ─────────────────────────────────

    async def act(self, action: Action) -> ActionResult:
        """Run a Supabase write/RPC op. The op name comes from ``action.params['op']`` (the five
        DB operations below don't map onto the generic ``ActionType`` enum, so they're carried in
        params — ``action.action_type`` is unused here).

        insert:  {"op": "insert", "table": "...", "rows": [...]}
        update:  {"op": "update", "table": "...", "filters": {"id": "eq.5"}, "data": {...}}
        delete:  {"op": "delete", "table": "...", "filters": {"id": "eq.5"}}
        rpc:     {"op": "rpc", "function": "my_func", "params": {...}}
        buckets: {"op": "buckets"} → list storage buckets
        """
        self._require_connected()
        p = action.params
        name = p.get("op", "")

        if name == "insert":
            import json

            rows = p.get("rows", [])
            table = p["table"]
            body = json.dumps(rows).encode()
            hdrs = {**self._headers(), "Content-Type": "application/json"}
            status_code, resp = _sb_request(self._rest_url(table), hdrs, "POST", body)
            return _act_result(f"insert:{table}", status_code, resp, (200, 201))

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
            return _act_result(f"update:{table}", status_code, resp, (200, 204))

        if name == "delete":
            table = p["table"]
            filters = urllib.parse.urlencode(p.get("filters", {}))
            status_code, resp = _sb_request(
                f"{self._rest_url(table)}?{filters}", self._headers(), "DELETE"
            )
            return _act_result(f"delete:{table}", status_code, resp, (200, 204))

        if name == "rpc":
            import json

            func = p["function"]
            body = json.dumps(p.get("params", {})).encode()
            hdrs = {**self._headers(), "Content-Type": "application/json"}
            status_code, resp = _sb_request(f"{self._url}/rest/v1/rpc/{func}", hdrs, "POST", body)
            return _act_result(f"rpc:{func}", status_code, resp, (200,))

        if name == "buckets":
            status_code, resp = _sb_request(f"{self._url}/storage/v1/bucket", self._headers())
            return _act_result("buckets", status_code, resp, (200,))

        return ActionResult(success=False, error=f"Unknown action: {name}")

    # ── Health ───────────────────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Probe the Supabase REST root to verify the project is reachable."""
        if not self._url or not self._anon_key:
            return HealthStatus(ok=False, message="Not connected", latency_ms=0)
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
                    ok=True,
                    message=f"Supabase REST API reachable ({self._url})",
                    latency_ms=latency_ms,
                )
            return HealthStatus(
                ok=False, message=f"Unexpected HTTP {resp.status}", latency_ms=latency_ms
            )
        except urllib.error.HTTPError as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            # 400 from PostgREST root means it's up (no table specified)
            if exc.code == 400:
                return HealthStatus(
                    ok=True, message="Supabase REST API reachable", latency_ms=latency_ms
                )
            return HealthStatus(ok=False, message=f"HTTP {exc.code}", latency_ms=latency_ms)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - t0) * 1000)
            return HealthStatus(ok=False, message=str(exc), latency_ms=latency_ms)
