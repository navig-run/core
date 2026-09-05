"""Where the browser subsystem keeps its own directories.

Every one of these used to be a literal ``"~/.navig/..."``. A default install is
unaffected either way — :func:`navig.platform.paths.config_dir` *is* ``~/.navig`` there —
which is exactly why they survived: they look correct on the only machine anyone tests on.
An install that moved its config got a **split brain**: config resolved through
``config_dir()`` while screenshots and browser profiles stayed in the real home.

It was not theoretical, and not only a production concern. ``BrowserController.__init__``
and ``StealthBrowser.__init__`` ``mkdir`` these paths, so merely constructing one **created
directories in the operator's real ``~/.navig``** — from the test suite, under a fully
isolated config. Measured with an audit of every write under the real home during a full
run: 96 mkdirs, 64 of them from tests, and ``screenshots`` alone accounted for 56.

Resolved per call, never frozen into a module constant: ``config_dir()`` reads
``NAVIG_CONFIG_DIR`` live, and a constant evaluated at import would capture whatever was set
when the module first loaded.

Same class as the gateway's ``storage_dir`` (#1121).
"""

from __future__ import annotations

from navig.platform.paths import browser_profile_dir
from navig.platform.paths import screenshot_dir as _screenshot_dir


def screenshot_dir() -> str:
    """Default screenshot directory, as a string (these are config defaults)."""
    return str(_screenshot_dir())


def profile_dir(name: str) -> str:
    """Default browser-profile directory, as a string."""
    return str(browser_profile_dir(name))
