"""The bridge from core to the shipped ``navig-home`` plugin.

The plugin owns the only device inventory and every provider integration. Core
must not import it (it is a plugin, and its YAML is not a contract), so this
module talks to the plugin's public CLI — the same surface an operator uses:

    navig home list --json              → the inventory
    navig home status --json            → live snapshots + provider readiness
    navig home providers --json         → provider readiness on its own
    navig home on|off|toggle <id> --json
    navig home set <id> key=value --json

Two rules make it safe on every surface:

* **fixed argv, never a shell** — the child is this NAVIG install's launcher
  (``navig.platform.navig_cli.navig_launcher``) plus a literal argument list;
* **an explicit encoding** — ``utf-8`` with ``errors="replace"``, so a provider's
  byte-decoded name can never kill the reader thread (the guard in
  ``tests/quality/test_dynamic_subprocess_encoding.py`` asks for exactly this).

``runner`` is injectable: tests (and the deck route's diagnostics) drive the
whole layer with a fake child, so no test needs a plugin install, a daemon or a
network.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from navig.endpoints.errors import HomeUnavailable
from navig.platform.navig_cli import navig_launcher
from navig.platform.process import spawn_kwargs

#: A provider action should answer quickly; the plugin's own budget is 8 s.
DEFAULT_TIMEOUT = 8.0

#: The hint for "the plugin is not reachable at all".
INSTALL_HINT = (
    "install the home plugin: `navig plugin install navig-home`, "
    "then `navig home list --json` should answer"
)

#: The hint for "the plugin answered, but the action/read failed" — a provider
#: that is not configured is the common case, and the fix is its readiness row.
STATUS_HINT = (
    "run `navig home status` to see provider readiness and the exact fix "
    "(or `navig home providers`)"
)

#: Child stderr phrases that mean the `home` verb itself is missing.
_MISSING_MARKERS: tuple[str, ...] = (
    "no such command",
    "unrecognized",
    "unknown command",
    "not installed",
)


def _error_hint(message: str) -> str:
    lowered = message.lower()
    if any(marker in lowered for marker in _MISSING_MARKERS):
        return INSTALL_HINT
    return STATUS_HINT


@dataclass(frozen=True)
class CommandResult:
    """One child invocation, as the bridge needs it."""

    args: tuple[str, ...] = ()
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


#: ``runner(args, timeout=…) -> CommandResult`` — *args* excludes the launcher.
Runner = Callable[..., CommandResult]


def _subprocess_runner(args: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
    """Run one NAVIG CLI command as a fixed argv list and capture its streams."""
    argv = [*navig_launcher(prefer_entrypoint=True), *args]
    completed = subprocess.run(  # noqa: S603 - fixed argv list, never a shell
        argv,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        **spawn_kwargs(),
    )
    return CommandResult(
        args=tuple(args),
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _first_json(text: str) -> Any | None:
    """The first JSON object/array in *text*, tolerating a banner line above it.

    ``navig home … --json`` prints one document, but a bootstrap/onboarding line
    or a rich console notice may precede it. Scanning for the first ``{``/``[``
    and using ``raw_decode`` keeps the parse strict about the document itself
    while ignoring any preamble.
    """
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


def _first_message(text: str, *, limit: int = 400) -> str:
    """A child's human error, reflowed and glyph-stripped.

    ``ch.error`` prints through a Rich console, which HARD-WRAPS at the console
    width when stderr is piped — so a long message arrives split across lines and
    the last line is usually just the tail of the hint. Joining the lines back
    (and stripping the leading ✗/⚠/ℹ glyph) keeps the actionable sentence that
    the operator needs.
    """
    lines = [raw.strip() for raw in (text or "").splitlines() if raw.strip()]
    if not lines:
        return ""
    joined = " ".join(lines).lstrip("✗⚠ℹ✖× ").strip()
    return joined[:limit]


class HomeBridge:
    """Fixed-argv bridge to ``navig home`` — injectable for tests."""

    def __init__(
        self,
        *,
        runner: Runner | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.runner: Runner = runner or _subprocess_runner
        self.timeout = timeout

    # ── transport ────────────────────────────────────────────────────────

    def _run(self, args: Sequence[str]) -> dict[str, Any]:
        argv = [str(item) for item in args]
        command = "navig " + " ".join(argv)
        try:
            result = self.runner(argv, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise HomeUnavailable(
                f"`{command}` timed out after {self.timeout:.0f}s",
                hint=INSTALL_HINT,
            ) from exc
        except OSError as exc:
            raise HomeUnavailable(
                f"could not run `{command}`: {exc}",
                hint=INSTALL_HINT,
            ) from exc
        if int(result.returncode) != 0:
            reason = _first_message(result.stderr or result.stdout)
            if not reason:
                reason = f"exit {result.returncode}"
            raise HomeUnavailable(
                f"`{command}` failed: {reason}",
                hint=_error_hint(reason),
            )
        payload = _first_json(result.stdout or "")
        if payload is None:
            raise HomeUnavailable(
                f"`{command}` returned no JSON",
                hint=INSTALL_HINT,
            )
        if not isinstance(payload, dict):
            raise HomeUnavailable(
                f"`{command}` returned an unexpected JSON shape",
                hint=INSTALL_HINT,
            )
        if payload.get("ok") is False:
            message = str(payload.get("error") or "the home plugin reported a failure")
            raise HomeUnavailable(f"`{command}`: {message}", hint=_error_hint(message))
        return payload

    # ── reads ────────────────────────────────────────────────────────────

    def devices(self) -> list[dict[str, Any]]:
        """The static inventory rows (``navig home list --json``)."""
        payload = self._run(["home", "list", "--json"])
        return [row for row in payload.get("devices") or [] if isinstance(row, dict)]

    def status(self) -> dict[str, Any]:
        """The live snapshot payload (``navig home status --json``)."""
        return self._run(["home", "status", "--json"])

    def states(self) -> list[dict[str, Any]]:
        """The per-device live snapshots from :meth:`status`."""
        payload = self.status()
        return [row for row in payload.get("devices") or [] if isinstance(row, dict)]

    def readiness(self) -> list[dict[str, Any]]:
        """Provider readiness carried by ``navig home status --json``."""
        payload = self.status()
        return [row for row in payload.get("providers") or [] if isinstance(row, dict)]

    def providers(self) -> list[dict[str, Any]]:
        """Provider readiness from its own command (``navig home providers --json``)."""
        payload = self._run(["home", "providers", "--json"])
        return [row for row in payload.get("providers") or [] if isinstance(row, dict)]

    # ── actions ──────────────────────────────────────────────────────────

    def power(self, device_id: str, on: bool) -> dict[str, Any]:
        verb = "on" if on else "off"
        return self._run(["home", verb, str(device_id), "--json"])

    def toggle(self, device_id: str) -> dict[str, Any]:
        return self._run(["home", "toggle", str(device_id), "--json"])

    def set(self, device_id: str, assignments: Sequence[str]) -> dict[str, Any]:
        values = [str(item) for item in assignments if str(item).strip()]
        if not values:
            raise HomeUnavailable(
                "no assignment to set",
                hint='pass key=value pairs, e.g. brightness=40',
            )
        return self._run(["home", "set", str(device_id), *values, "--json"])
