"""NAVIG browser automation commands backed by the gateway API."""

from __future__ import annotations

from typing import Optional

import typer

from navig.gateway.client import gateway_base_url, gateway_request_headers
from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

browser_app = typer.Typer(
    name="browser",
    help="Browser automation via the NAVIG gateway",
    no_args_is_help=True,
)


def _gateway_unavailable() -> None:
    ch.warning("Gateway is not running")
    ch.info("Start with: navig gateway start")


@browser_app.command("status")
def browser_status() -> None:
    """Show browser status."""
    import requests

    try:
        response = requests.get(
            f"{gateway_base_url()}/browser/status",
            headers=gateway_request_headers(),
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            if data.get("started"):
                ch.success("Browser is running")
                ch.info(
                    "  Active page loaded"
                    if data.get("has_page")
                    else "  No page loaded"
                )
            else:
                ch.info("Browser is not running")
        elif response.status_code == 503:
            ch.warning("Browser module not available (install playwright)")
        else:
            ch.error(f"Failed: {response.status_code}")
    except requests.exceptions.ConnectionError:
        _gateway_unavailable()
    except Exception as exc:
        ch.error(f"Error: {exc}")


@browser_app.command("open")
def browser_open(
    url: str = typer.Argument(..., help="URL to navigate to"),
) -> None:
    """Navigate browser to a URL."""
    import requests

    try:
        response = requests.post(
            f"{gateway_base_url()}/browser/navigate",
            headers=gateway_request_headers(),
            json={"url": url},
            timeout=30,
        )
        if response.status_code == 200:
            ch.success(f"Navigated to: {url}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        _gateway_unavailable()
    except Exception as exc:
        ch.error(f"Error: {exc}")


@browser_app.command("screenshot")
def browser_screenshot(
    path: Optional[str] = typer.Option(None, "--path", "-p", help="Save path"),
    full_page: bool = typer.Option(False, "--full", "-f", help="Capture full page"),
) -> None:
    """Capture a browser screenshot."""
    import requests

    try:
        response = requests.post(
            f"{gateway_base_url()}/browser/screenshot",
            headers=gateway_request_headers(),
            json={"path": path, "full_page": full_page},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            ch.success(f"Screenshot saved: {data.get('path', 'unknown')}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        _gateway_unavailable()
    except Exception as exc:
        ch.error(f"Error: {exc}")


@browser_app.command("click")
def browser_click(
    selector: str = typer.Argument(..., help="CSS selector to click"),
) -> None:
    """Click an element on the active page."""
    import requests

    try:
        response = requests.post(
            f"{gateway_base_url()}/browser/click",
            headers=gateway_request_headers(),
            json={"selector": selector},
            timeout=30,
        )
        if response.status_code == 200:
            ch.success(f"Clicked: {selector}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        _gateway_unavailable()
    except Exception as exc:
        ch.error(f"Error: {exc}")


@browser_app.command("fill")
def browser_fill(
    selector: str = typer.Argument(..., help="CSS selector for input"),
    value: str = typer.Argument(..., help="Value to fill"),
) -> None:
    """Fill an input field on the active page."""
    import requests

    try:
        response = requests.post(
            f"{gateway_base_url()}/browser/fill",
            headers=gateway_request_headers(),
            json={"selector": selector, "value": value},
            timeout=30,
        )
        if response.status_code == 200:
            ch.success(f"Filled: {selector}")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        _gateway_unavailable()
    except Exception as exc:
        ch.error(f"Error: {exc}")


@browser_app.command("stop")
def browser_stop() -> None:
    """Stop the browser controller."""
    import requests

    try:
        response = requests.post(
            f"{gateway_base_url()}/browser/stop",
            headers=gateway_request_headers(),
            timeout=10,
        )
        if response.status_code == 200:
            ch.success("Browser stopped")
        elif response.status_code == 503:
            ch.warning("Browser module not available")
        else:
            ch.error(f"Failed: {response.json().get('error', 'Unknown error')}")
    except requests.exceptions.ConnectionError:
        _gateway_unavailable()
    except Exception as exc:
        ch.error(f"Error: {exc}")
