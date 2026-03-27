"""
SiteCheckTool — HTTP site availability and latency checker.

Reports: status code, redirect chain, response latency, TLS cert expiry.
Zero API key required. Uses httpx for async HTTP.
"""

from __future__ import annotations

import logging
import ssl
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)


class SiteCheckTool(BaseTool):
    name = "site_check"
    description = "Check if a URL/domain is reachable. Returns status code, latency, redirect chain."

    async def run(
        self,
        args: Dict[str, Any],
        on_status: Optional[StatusCallback] = None,
    ) -> ToolResult:
        url: str = args.get("url", "")
        if not url:
            return ToolResult(name=self.name, success=False, error="url arg required")

        # Normalise — add https:// if bare domain
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        await self._emit(
            on_status, "Resolving DNS…", f"target: {urlparse(url).netloc}", 20
        )

        try:
            import httpx
        except ImportError:
            return ToolResult(
                name=self.name, success=False, error="httpx not installed"
            )

        t0 = time.monotonic()
        redirect_chain: list[str] = []
        status_code: Optional[int] = None
        cert_expiry: Optional[str] = None

        try:
            await self._emit(on_status, "Establishing connection…", "", 40)

            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(10.0),
                verify=True,
            ) as client:
                resp = await client.head(url)
                status_code = resp.status_code
                redirect_chain = [str(r.url) for r in resp.history] + [str(resp.url)]

            latency_ms = (time.monotonic() - t0) * 1000

            await self._emit(
                on_status,
                "Checking response…",
                f"HTTP {status_code} · {latency_ms:.0f}ms",
                70,
            )

            # TLS cert expiry (best-effort)
            parsed = urlparse(url)
            if parsed.scheme == "https":
                try:
                    cert_expiry = await _get_cert_expiry(parsed.netloc)
                except Exception:
                    cert_expiry = None

            output = {
                "url": url,
                "status_code": status_code,
                "latency_ms": round(latency_ms, 1),
                "redirects": len(redirect_chain) - 1,
                "final_url": redirect_chain[-1] if redirect_chain else url,
                "cert_expiry": cert_expiry,
                "online": 200 <= status_code < 400 if status_code else False,
            }
            return ToolResult(name=self.name, success=True, output=output)

        except httpx.ConnectError as exc:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"connection failed: {exc}",
            )
        except httpx.TimeoutException:
            return ToolResult(
                name=self.name,
                success=False,
                error="request timed out (10s)",
            )
        except Exception as exc:
            return ToolResult(
                name=self.name,
                success=False,
                error=str(exc),
            )


async def _get_cert_expiry(hostname: str) -> Optional[str]:
    """Return TLS cert expiry date string, or None on failure."""
    import asyncio
    import datetime

    def _sync_check() -> Optional[str]:
        ctx = ssl.create_default_context()
        try:
            with ctx.wrap_socket(
                __import__("socket").socket(), server_hostname=hostname
            ) as s:
                s.settimeout(5.0)
                s.connect((hostname, 443))
                cert = s.getpeercert()
                not_after = cert.get("notAfter", "")
                if not_after:
                    dt = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                    return dt.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        return None

    return await asyncio.get_event_loop().run_in_executor(None, _sync_check)
