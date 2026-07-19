"""Reusable CDP/Playwright response interception — capture a page's OWN signed JSON.

The durable way to read a site whose API is signed (TikTok ``msToken``/``X-Bogus``,
YouTube ``youtubei/v1``) is **not** to reverse the signature — it's to let a real browser
make the request and capture the JSON it gets back — a browser-native capture technique,
reimplemented here with no external dependency.

Two primitives, both duck-typed on a Playwright/Patchright ``page`` so any controller tier
can use them:

- :class:`JSONCollector` — buffer every response whose URL matches a filter, parsed as JSON.
- :func:`install_read_only_guard` — abort engagement *mutations* (like/follow/comment-post)
  so scraping can never accidentally act on the account driving it.

Nothing here is TikTok-specific; callers pass the URL fragments they care about.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

__all__ = [
    "JSONCollector",
    "install_read_only_guard",
    "capture_json",
    "MUTATION_URL_MARKERS",
]


class JSONCollector:
    """Attach to a page and buffer JSON bodies of responses matching any URL fragment.

    Reading the body inside the ``response`` event is best-effort: non-JSON, redirects
    and already-consumed bodies are skipped silently rather than raising into the page.
    """

    def __init__(self, page: Any, url_filters: list[str]):
        self._page = page
        self._filters = [f.lower() for f in url_filters]
        self._items: list[dict] = []  # {"url", "status", "json"}
        self._attached = False

    def _match(self, url: str) -> bool:
        u = (url or "").lower()
        return any(f in u for f in self._filters)

    async def _on_response(self, response: Any) -> None:
        try:
            if not self._match(response.url):
                return
            ctype = (response.headers or {}).get("content-type", "")
            if "json" not in ctype.lower() and not response.url.lower().endswith(".json"):
                # TikTok's list endpoints return JSON without always tagging content-type;
                # try anyway but don't hard-require it.
                pass
            data = await response.json()
        except Exception:  # noqa: BLE001 — non-JSON / consumed / redirect
            return
        # Capture the request SHAPE for the signer cache — header/param NAMES only, never
        # values (msToken/X-Bogus/cookies are secrets and must not be buffered/persisted).
        req = getattr(response, "request", None)
        req_shape = None
        if req is not None:
            try:
                req_shape = {
                    "url": req.url,               # transient (has signed values) — not persisted raw
                    "method": req.method,
                    "header_names": sorted((req.headers or {}).keys()),
                }
            except Exception:  # noqa: BLE001
                req_shape = None
        self._items.append({
            "url": response.url, "status": response.status, "json": data,
            "request": req_shape,
        })

    def start(self) -> "JSONCollector":
        if not self._attached:
            self._page.on("response", self._on_response)
            self._attached = True
        return self

    def stop(self) -> None:
        if self._attached:
            try:
                self._page.remove_listener("response", self._on_response)
            except Exception:  # noqa: BLE001
                pass
            self._attached = False

    @property
    def items(self) -> list[dict]:
        return list(self._items)

    def json_bodies(self) -> list[dict]:
        return [it["json"] for it in self._items]


# ── read-only guard ───────────────────────────────────────────────────────────
# URL fragments for engagement MUTATIONS across the sites we scrape. We abort these
# (not all POSTs — pages legitimately POST to fetch data) so a read-only scrape can
# never like/follow/comment from the account whose cookies drive the browser.
MUTATION_URL_MARKERS: tuple[str, ...] = (
    "/commit/item/digg", "/commit/follow/user", "/comment/publish", "/comment/digg",
    "/aweme/v1/commit/", "/im/send", "/like", "/follow", "/subscribe", "/favorite",
    "/vote", "/share/create",
)


def _is_mutation(url: str, method: str, extra: tuple[str, ...]) -> bool:
    if method.upper() not in ("POST", "PUT", "PATCH", "DELETE"):
        return False
    u = (url or "").lower()
    return any(m in u for m in MUTATION_URL_MARKERS) or any(m.lower() in u for m in extra)


_RO_GUARD_FLAG = "_navig_readonly_guard"


async def install_read_only_guard(page: Any, *, extra_markers: tuple[str, ...] = ()) -> None:
    """Route the page so engagement-mutation requests are aborted, everything else passes.

    **Idempotent** — installing again on the same page is a no-op, so reusing one page across
    many captures (e.g. a shared-controller photo batch) never stacks duplicate ``page.route``
    handlers (which would leak per navigation).
    """
    if getattr(page, _RO_GUARD_FLAG, False):
        return

    async def _route(route: Any) -> None:
        req = route.request
        try:
            if _is_mutation(req.url, req.method, extra_markers):
                logger.info("[intercept] read-only guard aborted %s %s", req.method, req.url[:120])
                await route.abort()
                return
            await route.continue_()
        except Exception:  # noqa: BLE001 — a routing hiccup must not wedge the page
            try:
                await route.continue_()
            except Exception:  # noqa: BLE001
                pass

    await page.route("**/*", _route)
    try:
        setattr(page, _RO_GUARD_FLAG, True)
    except Exception:  # noqa: BLE001 — page may forbid attrs; guard still installed
        pass


async def capture_json(
    page: Any,
    url_filters: list[str],
    *,
    trigger: Callable[[], Any] | None = None,
    scroller: Callable[[], Any] | None = None,
    rounds: int = 6,
    settle_ms: int = 1200,
    read_only: bool = True,
    with_meta: bool = False,
    stop_when: Callable[[list[dict]], bool] | None = None,
) -> list[dict]:
    """Collect JSON bodies matching *url_filters* while driving the page.

    Flow: install the read-only guard (optional) → attach the collector → run *trigger*
    (e.g. navigation) → repeat *scroller* up to *rounds* times, settling between each so
    lazy-loaded API calls fire → return the parsed JSON bodies (or, with ``with_meta=True``,
    the full items incl. request shape). The caller owns the page lifecycle; this only
    observes + scrolls it.

    ``stop_when`` — optional predicate called with the collected items after each settle; return
    ``True`` to stop scrolling early (e.g. enough results captured, or the API signalled no more
    pages). Lets a bounded ``rounds`` act as a safety cap rather than a fixed cost.
    """
    from navig.browser.pacing import jittered  # noqa: PLC0415

    if read_only:
        await install_read_only_guard(page)
    collector = JSONCollector(page, url_filters).start()

    def _done() -> bool:
        if stop_when is None:
            return False
        try:
            return bool(stop_when(collector.items))
        except Exception:  # noqa: BLE001 — a predicate error must never wedge the capture
            return False

    try:
        if trigger is not None:
            await trigger()
        # Jitter every settle so the scroll cadence isn't a metronome (a detection tell).
        await asyncio.sleep(jittered(settle_ms / 1000))
        if _done():
            return collector.items if with_meta else collector.json_bodies()
        for _ in range(max(0, rounds)):
            if scroller is not None:
                try:
                    await scroller()
                except Exception:  # noqa: BLE001
                    break
            await asyncio.sleep(jittered(settle_ms / 1000))
            if _done():
                break
        return collector.items if with_meta else collector.json_bodies()
    finally:
        collector.stop()
