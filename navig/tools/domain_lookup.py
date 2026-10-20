"""
DomainLookupTool — registration facts for a domain (expiry, registrar, status).

Measured 2026-10-16: asked to remind the operator before schema.org expires, the
agent shelled out instead, and both obvious commands fail silently on Windows —
``whois`` is Sysinternals whois (prints its licence, exits 1 until the EULA is
accepted) and ``curl -s https://rdap.org/domain/…`` returns an empty body because
rdap.org answers with a redirect. The agent then told the operator RDAP was
"not reachable from this machine" and guessed the date. This tool is the one
lookup the agent should use: RDAP first, then port-43 whois, no binaries.
"""

from __future__ import annotations

import logging
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)


class DomainLookupTool(BaseTool):
    name = "domain_lookup"
    description = (
        "Look up a domain's registration record: expiry date, registrar, creation "
        "date and status (RDAP, falling back to whois). Use this for any question "
        "about when a domain expires or who registered it — do not run whois or "
        "curl in a shell."
    )
    owner_only = False
    parameters = [
        {
            "name": "domain",
            "type": "string",
            "description": "The domain name, e.g. example.com (a URL is accepted)",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        domain = str(args.get("domain", "") or "").strip()
        if not domain:
            return ToolResult(name=self.name, success=False, error="domain arg required")

        from navig.telegram.biz_lookups import lookup_domain

        await self._emit(on_status, "Querying RDAP…", domain[:60], 30)
        try:
            info = await lookup_domain(domain)
        except Exception as exc:  # noqa: BLE001 — report it; never pretend
            logger.debug("domain_lookup failed for %s: %s", domain, exc)
            return ToolResult(name=self.name, success=False, error=f"lookup failed: {exc}")

        outcome = info.get("outcome")
        if outcome == "invalid":
            return ToolResult(name=self.name, success=False, error=f"not a domain name: {domain!r}")
        if outcome == "failed":
            # A network failure is NOT "the domain has no record" — say which.
            return ToolResult(
                name=self.name,
                success=False,
                error=f"RDAP and whois both failed for {info.get('domain')} (network error)",
            )
        return ToolResult(name=self.name, success=True, output=info)
