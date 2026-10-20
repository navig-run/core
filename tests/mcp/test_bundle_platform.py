"""The catalogue must not advertise what the platform cannot serve.

Measured on the supplied deployment (Debian container running ``navig mcp serve``): the
``desktop`` bundle registered unconditionally, so 25 Windows UI-automation tools appeared in
``tools/list`` — and every call raised ``ValueError("desktop tools are Windows only")`` from
``desktop._desktop_client()``. The guard exists; it just runs after the client has already
been promised the tool.

An agent plans against ``tools/list``. A capability in that list that always fails is worse
than a shorter list, and a reviewer of a hosted instance reads it as a promise.
"""

from __future__ import annotations

import sys
from typing import Any

from navig.mcp.tools import all_bundles, bundle_name, register_all_tools


def test_desktop_bundle_follows_the_platform(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    linux_names = {bundle_name(bundle) for bundle in all_bundles()}
    assert "desktop" not in linux_names, "a Linux server must not offer the Windows desktop bundle"

    monkeypatch.setattr(sys, "platform", "win32")
    win_names = {bundle_name(bundle) for bundle in all_bundles()}
    assert "desktop" in win_names, "the Windows desktop bundle disappeared from Windows"


def test_a_linux_server_advertises_no_windows_only_tool(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    class _StubServer:
        def __init__(self) -> None:
            self.tools: dict[str, dict[str, Any]] = {}
            self._tool_handlers: dict[str, Any] = {}
            self._tool_safety: dict[str, str] = {}

    server = _StubServer()
    register_all_tools(server)

    ui_automation = sorted(
        name for name in server.tools if name.startswith(("desktop_", "windows_"))
        and name != "desktop_filesystem"  # path operations work on every platform
    )
    assert ui_automation == [], f"Windows-only tools advertised on Linux: {ui_automation}"
    # The exception is deliberate: desktop_filesystem is plain path work, so it is served
    # everywhere — dropping it would remove a capability Linux can actually use.
    assert "desktop_filesystem" in server.tools
    # The platform gate must not take the rest of the catalogue with it.
    assert "navig_list_hosts" in server.tools
