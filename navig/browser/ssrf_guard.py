"""SSRF route-guard for Playwright browser automation.

Validating only the *entry* URL is not enough for a real browser. Playwright follows
HTTP redirects, ``<meta refresh>`` and JS ``location=`` on its own, and loads whatever
subresources (images, scripts, ``fetch``/XHR) the page asks for — none of which passed
the entry check. So a public URL the agent was steered to (by a prompt-injection riding
model output, or a malicious page) that ``302``s to ``169.254.169.254`` (cloud metadata)
or the local daemon would be rendered and its content read back to the model.

This installs a request interceptor that re-validates **every** in-page request against
the SSRF policy and aborts the blocked ones. Install it on a Playwright ``BrowserContext``
(covers every current and future tab/page) or a single ``Page``. Verdicts are cached per
host for the life of the target — DNS is blocking and pages hit the same hosts repeatedly.
Unverifiable ⇒ blocked (**fail closed**): a host we cannot resolve is one the browser could
not have safely reached anyway.

Secure by default; local/LAN hosts are reachable only when the operator sets
``net.ssrf.allow_private_network`` (via ``policy_from_config``), exactly like every other
navig SSRF surface (``safe_fetch``, ``ics_calendar``, ``browser_fetch``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from navig.net.ssrf import SsrfPolicy, check_url

logger = logging.getLogger(__name__)

# Only http(s) leaves the machine over the network. data:/blob:/about:/chrome:/file
# resources never make an outbound request, so they are not an SSRF vector and blocking
# them would break rendering for no security gain.
_NETWORK_SCHEMES = frozenset({"http", "https"})


def ssrf_verdict(url: str, policy: SsrfPolicy) -> bool:
    """Whether *policy* permits fetching *url* (synchronous — resolves DNS via ``check_url``).

    Non-network schemes (``data:``/``blob:``/``about:`` …) are always allowed. A blocked
    range OR an unresolvable host returns ``False`` (fail closed).
    """
    scheme = url.partition("://")[0].lower()
    if scheme not in _NETWORK_SCHEMES:
        return True
    try:
        check_url(url, policy)
        return True
    except Exception:  # noqa: BLE001 — SsrfBlockedError, ValueError, or DNS failure ⇒ blocked
        return False


async def install_ssrf_route_guard(target: Any, policy: SsrfPolicy) -> None:
    """Abort every request *target* makes that the SSRF *policy* rejects.

    *target* is a Playwright ``BrowserContext`` (preferred — covers all tabs) or ``Page``;
    both expose ``.route(pattern, handler)``. See the module docstring for the threat model.
    """
    verdicts: dict[str, bool] = {}

    async def _handler(route: Any) -> None:
        try:
            url = route.request.url
            scheme = url.partition("://")[0].lower()
            if scheme not in _NETWORK_SCHEMES:
                await route.continue_()  # data:/blob:/about: never leave the process
                return

            host_key = url.partition("://")[2].split("/", 1)[0].lower()
            allowed = verdicts.get(host_key)
            if allowed is None:
                allowed = await asyncio.to_thread(ssrf_verdict, url, policy)
                verdicts[host_key] = allowed
                if not allowed:
                    logger.warning("browser SSRF guard blocked in-page request to %s", host_key)

            await (route.continue_() if allowed else route.abort())
        except Exception as exc:  # noqa: BLE001 — a raising route handler can hang Playwright
            logger.debug("browser SSRF route guard handler error: %s", exc)

    await target.route("**/*", _handler)
