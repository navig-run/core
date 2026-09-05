"""NAVIG browser automation commands backed by the gateway API."""

from __future__ import annotations

import typer

from navig.gateway_client import gateway_base_url, gateway_request_headers
from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

_BROWSER_REQUEST_TIMEOUT: int = 30  # Browser automation calls can take a few seconds

browser_app = typer.Typer(
    name="browser",
    help="Browser automation via the NAVIG gateway",
    no_args_is_help=True,
)


def _browser_api(
    method: str,
    path: str,
    *,
    action: str,
    json_body: dict | None = None,
    timeout: float = _BROWSER_REQUEST_TIMEOUT,
    expect_payload: bool = True,
) -> dict:
    """Call the gateway's browser API, or exit non-zero saying why.

    All six commands carried this block, and **every** failure branch exited 0 —
    a non-200, a 503 "browser module not available", a gateway that is not
    running, and any other exception. 24 paths. So

        navig browser open https://example.com && navig browser click "#buy"

    clicked against a page that was never navigated to, and with the gateway down
    the whole chain printed warnings and reported success. Browser automation is
    scripted by definition; these exit codes are the only thing a script can see.

    Centralising also settles two drifts: `browser status` explained the 503 as
    "install playwright" while the other five just said "not available" (the
    actionable wording now applies everywhere), and the per-command timeouts stay
    explicit at the call sites instead of being implied.
    """
    try:
        import requests  # noqa: PLC0415 — lazy: keeps `navig --help` fast
    except ImportError as exc:
        ch.error(f"Cannot {action}: the 'requests' package is not installed")
        ch.info("Install with: pip install requests")
        raise typer.Exit(1) from exc

    try:
        response = requests.request(
            method,
            f"{gateway_base_url()}{path}",
            headers=gateway_request_headers(),
            json=json_body,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError as exc:
        ch.error(f"Cannot {action}: the gateway is not running")
        ch.info("Start it with: navig gateway start")
        raise typer.Exit(1) from exc
    except requests.exceptions.Timeout as exc:
        ch.error(f"Cannot {action}: the gateway did not answer within {timeout:g}s")
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001 — reported with its type, then exits
        ch.error(f"Cannot {action}: {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc

    if response.status_code == 503:
        ch.error(f"Cannot {action}: the browser module is not available")
        ch.info("Install it with: pip install playwright  (then: playwright install)")
        raise typer.Exit(1)

    if response.status_code != 200:
        ch.error(f"Failed to {action}: {_safe_get_error(response)}")
        raise typer.Exit(1)

    if not expect_payload:
        return {}

    try:
        payload = _unwrap(response.json())
    except Exception as exc:  # noqa: BLE001 — a 200 we cannot read is not a success
        ch.error(f"Cannot {action}: the gateway sent a response that could not be read")
        raise typer.Exit(1) from exc
    return payload if isinstance(payload, dict) else {"data": payload}



def _unwrap(body):
    """Payload out of the gateway's ``json_ok`` envelope ({"ok":…,"data":…,"error":…}).

    Reading a field straight off ``response.json()`` always misses — the payload is one
    level down — and a miss looks exactly like "the daemon has nothing to report". That
    emptied the whole flux (#713) and cron (#714) surfaces. Lazily imported to keep CLI
    startup fast.

    NOT for error bodies: ``envelope_error`` puts the message at the TOP level ``error``
    key with ``data: None``, so an error-extraction helper must read the raw body.
    """
    from navig.gateway_client import unwrap_envelope

    return unwrap_envelope(body)


def _safe_get_error(response) -> str:
    try:
        data = response.json()
        return data.get("error", "Unknown error")
    except Exception:
        return f"Gateway format invalid (HTTP {response.status_code})"


@browser_app.command("status")
def browser_status() -> None:
    """Show browser status."""
    # 5s, not the 30s automation timeout: this is a quick probe.
    data = _browser_api("GET", "/browser/status", action="get browser status", timeout=5)
    if not data.get("started"):
        # The QUERY succeeded; the answer is just "not running". Exit 0 — only an
        # unanswerable query fails, which `_browser_api` already handled.
        ch.info("Browser is not running")
        return

    ch.success("Browser is running")
    ch.info("  Active page loaded" if data.get("has_page") else "  No page loaded")


@browser_app.command("open")
def browser_open(
    url: str = typer.Argument(..., help="URL to navigate to"),
) -> None:
    """Navigate browser to a URL."""
    _browser_api(
        "POST",
        "/browser/navigate",
        action=f"navigate to {url}",
        json_body={"url": url},
        expect_payload=False,
    )
    ch.success(f"Navigated to: {url}")


@browser_app.command("screenshot")
def browser_screenshot(
    path: str | None = typer.Option(None, "--path", "-p", help="Save path"),
    full_page: bool = typer.Option(False, "--full", "-f", help="Capture full page"),
) -> None:
    """Capture a browser screenshot."""
    data = _browser_api(
        "POST",
        "/browser/screenshot",
        action="capture a screenshot",
        json_body={"path": path, "full_page": full_page},
    )
    ch.success(f"Screenshot saved: {data.get('path', 'unknown')}")


@browser_app.command("click")
def browser_click(
    selector: str = typer.Argument(..., help="CSS selector to click"),
) -> None:
    """Click an element on the active page."""
    _browser_api(
        "POST",
        "/browser/click",
        action=f"click {selector!r}",
        json_body={"selector": selector},
        expect_payload=False,
    )
    ch.success(f"Clicked: {selector}")


@browser_app.command("fill")
def browser_fill(
    selector: str = typer.Argument(..., help="CSS selector for input"),
    value: str = typer.Argument(..., help="Value to fill"),
) -> None:
    """Fill an input field on the active page."""
    _browser_api(
        "POST",
        "/browser/fill",
        action=f"fill {selector!r}",
        json_body={"selector": selector, "value": value},
        expect_payload=False,
    )
    ch.success(f"Filled: {selector}")


@browser_app.command("stop")
def browser_stop() -> None:
    """Stop the browser controller."""
    _browser_api("POST", "/browser/stop", action="stop the browser", timeout=10,
                 expect_payload=False)
    ch.success("Browser stopped")
