"""Helpers for the Reflex Unified acceptance harness (non-fixture; imported by tests).

Kept in a plain module rather than ``conftest`` so test files can share it under
pytest's ``--import-mode=importlib`` without turning the repo's ``tests`` tree into a
package.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_ROOT = REPO_ROOT / "core"
BASE_REV = "71f2ec23cb"  # origin/main the branch is based on; plugin trees compared against it

#: Plan §2.1 — the only capability types and property instances allowed in an endpoint.
CAPABILITY_TYPES = {
    "devices.capabilities.on_off",
    "devices.capabilities.range",
    "devices.capabilities.mode",
    "devices.capabilities.color_setting",
    "devices.capabilities.toggle",
}
PROPERTY_TYPES = {
    "devices.properties.temperature",
    "devices.properties.humidity",
    "devices.properties.battery_level",
}
DIRECTIVES = {"on_off", "set_range", "set_mode", "set_color", "toggle"}
META_KEYS = {"retrievable", "reportable", "supported_directives", "supported_events"}
#: Plan §2.1 — provider-specific inventory fields that must never serialise into an endpoint.
PROVIDER_FIELDS = {"address", "entity_id", "dev_id", "token_env", "mac", "dps", "scale", "ir"}
#: Lead invariant (labs memo §3) — the endpoint schema carries no auth/permission surface.
FORBIDDEN_ENDPOINT_FIELDS = {
    "auth",
    "authorization",
    "permissions",
    "permission",
    "credential",
    "credentials",
    "token",
    "api_key",
    "apikey",
    "secret",
    "secrets",
}

#: Plan §3.2 — the 10 shipped tools.
EXPECTED_TOOLS = {
    "home.on",
    "home.off",
    "home.toggle",
    "home.set",
    "home.status",
    "tv.power",
    "tv.volume",
    "tv.play",
    "assistant.status",
    "campaigns.status",
}


# --------------------------------------------------------------------------- #
# RED helpers — fail with the owning task, never skip
# --------------------------------------------------------------------------- #
def require(module_name: str):
    """Import an implementation module or fail the test with an actionable message."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        pytest.fail(
            f"acceptance target not implemented yet: import {module_name!r} failed ({exc}). "
            "RED is expected until the owning Phase 1 task lands; do not weaken the test.",
            pytrace=False,
        )


def require_path(relative: str) -> Path:
    """Return a repo-relative path or fail because the target is not built yet."""
    path = REPO_ROOT / relative
    if not path.exists():
        pytest.fail(
            f"acceptance target not implemented yet: {relative} does not exist. "
            "RED is expected until the owning Phase 1 task lands; do not weaken the test.",
            pytrace=False,
        )
    return path


# --------------------------------------------------------------------------- #
# CLI / payload helpers
# --------------------------------------------------------------------------- #
def invoke_cli(args, capsys):
    """Invoke the real Typer app in-process (idiom of tests/cli/test_cli_ask_alias.py)."""
    require("navig.cli")
    from navig.cli import app

    exit_code = 0
    try:
        result = app(list(args), standalone_mode=False)
    except SystemExit as exc:  # Typer exits for usage errors / typer.Exit
        code = exc.code
        exit_code = int(code) if isinstance(code, int) else 1
        result = None
    if isinstance(result, int) and result != 0:
        exit_code = int(result)

    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def combined(stdout: str, stderr: str) -> str:
    return f"{stdout}\n{stderr}"


def json_payload(stdout: str) -> dict:
    """Parse the JSON object ``emit_json`` wrote to stdout."""
    text = stdout.strip()
    start = text.find("{")
    if start < 0:
        pytest.fail(
            "expected a JSON object on stdout (emit_json), got "
            f"{text[:300]!r} — check the --json path emits through navig.console_helper.emit_json",
            pytrace=False,
        )
    try:
        payload, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout is not valid JSON ({exc}): {text[:300]!r}", pytrace=False)
    if not isinstance(payload, dict):
        pytest.fail(f"expected a JSON object, got {type(payload).__name__}: {payload!r}", pytrace=False)
    return payload


def as_dict(obj):
    if isinstance(obj, dict):
        return obj
    as_dict_method = getattr(obj, "as_dict", None)
    if callable(as_dict_method):
        return as_dict_method()
    return dataclasses.asdict(obj)


def json_value(text: str):
    """Parse the first JSON value (object OR array) written to stdout."""
    stripped = text.strip()
    start = min(
        [index for index in (stripped.find("{"), stripped.find("[")) if index >= 0],
        default=-1,
    )
    if start < 0:
        pytest.fail(f"expected JSON on stdout, got {stripped[:300]!r}", pytrace=False)
    try:
        value, _end = json.JSONDecoder().raw_decode(stripped[start:])
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout is not valid JSON ({exc}): {stripped[:300]!r}", pytrace=False)
    return value


def plan_of(envelope: dict) -> dict:
    """The router plan carried by an Answer envelope (plan §4 ``Answer.plan``)."""
    plan = envelope.get("plan")
    if not isinstance(plan, dict) or not plan:
        pytest.fail(
            "Answer envelope has no plan object — navig ask --plan --json must emit "
            f"Answer.plan{{tool,slots,target,blast_radius,profile,commit_token}}; got {envelope!r}",
            pytrace=False,
        )
    return plan


def token_of(plan: dict) -> str:
    for key in ("commit_token", "token"):
        value = plan.get(key)
        if isinstance(value, str) and value:
            return value
    pytest.fail(f"plan carries no commit token (commit_token/token): {plan!r}", pytrace=False)


def plan_text(plan: dict) -> str:
    return json.dumps(plan, sort_keys=True, default=str).lower()


def answer_envelope_ok(envelope: dict) -> None:
    """A CLI Answer envelope is valid according to core.navig.ui.answer.validate_answer."""
    answer = require("navig.ui.answer")
    problems = answer.validate_answer(envelope)
    if problems is None:
        problems = []
    assert not problems, f"CLI envelope is not a valid Answer: {problems}"


def runtime_db_path() -> Path:
    """The documented RuntimeStore database that owns the ``commit_plans`` table (plan §3.4).

    Resolved through the public store singleton, not by guessing a path: the store caches
    the config dir it was opened with (the suite isolates it once per session).
    """
    from navig.store.runtime import get_runtime_store

    store = get_runtime_store()
    db_path = getattr(store, "db_path", None)
    if db_path is None:
        pytest.fail(
            "the RuntimeStore exposes no db_path; commit tokens must live in the existing "
            "RuntimeStore (plan §3.4)",
            pytrace=False,
        )
    return Path(db_path)


def assert_no_provider_fields(endpoint: dict) -> None:
    serialised = json.dumps(endpoint, default=str).lower()
    leaked = sorted(field for field in PROVIDER_FIELDS if f'"{field}"' in serialised)
    assert not leaked, f"provider-specific inventory fields leaked into the endpoint: {leaked}"


def assert_no_auth_fields(endpoint: dict) -> None:
    """The endpoint schema is a device model, not an auth surface (lead invariant #3)."""
    keys: set[str] = set()

    def _walk(node) -> None:
        if isinstance(node, dict):
            keys.update(str(key).lower() for key in node)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(endpoint)
    leaked = sorted(keys & FORBIDDEN_ENDPOINT_FIELDS)
    assert not leaked, f"auth/permission fields must not appear in an endpoint: {leaked}"


def normalise_imports(path: Path) -> set[str]:
    """Directly imported module names in one Python file (AST, no execution)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


#: Lead invariant #1 — risk is a gate, not a cost/latency/quality weight.
SCORING_IDENTIFIERS = {"cost", "latency", "quality", "pricing", "price"}


def scoring_identifier_uses(path: Path) -> list[str]:
    """Identifiers that would indicate ranking by cost/latency/quality instead of risk gates."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    uses: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in SCORING_IDENTIFIERS:
            uses.append(f"{path.name}:{node.lineno} .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in SCORING_IDENTIFIERS:
            uses.append(f"{path.name}:{node.lineno} {node.id}")
    return uses


# --------------------------------------------------------------------------- #
# The documented fake HomeBridge seam (plan §2.3: ``HomeBridge(…, runner=…)``)
# --------------------------------------------------------------------------- #
#: Realistic ``navig home list --json`` / ``navig home status --json`` payloads
#: (shipped plugin shapes: plugins/navig-home/navig_home/commands/home.py:287-303, 502-533).
DEVICES = [
    {
        "id": "desk-lamp",
        "name": "Desk lamp",
        "room": "office",
        "kind": "light",
        "provider": "govee",
        "address": "192.0.2.40",
        "model": "H6159",
        "capabilities": ["power", "toggle", "brightness", "color", "color_temp"],
    },
    {
        "id": "living-room-ac",
        "name": "Living-room AC",
        "room": "living room",
        "kind": "climate",
        "provider": "ha",
        "entity_id": "climate.living_room",
        "model": "PAC",
        "capabilities": ["power", "toggle", "target_temp", "mode", "fan"],
    },
    {
        "id": "kitchen-light",
        "name": "Kitchen light",
        "room": "kitchen",
        "kind": "light",
        "provider": "ha",
        "entity_id": "light.kitchen",
        "capabilities": ["power", "toggle", "brightness"],
    },
    {
        "id": "bedroom-sensor",
        "name": "Bedroom sensor",
        "room": "bedroom",
        "kind": "sensor",
        "provider": "ha",
        "entity_id": "sensor.bedroom",
        "capabilities": ["sensor"],
    },
    {
        "id": "loft-humidifier",
        "name": "Loft humidifier",
        "room": "loft",
        "kind": "humidifier",
        "provider": "ha",
        "entity_id": "humidifier.loft",
        "capabilities": ["power", "toggle", "humidity", "mode"],
    },
]

INITIAL_STATE = {
    "desk-lamp": {"power": True, "brightness": 60, "color": "white", "color_temp": 4000},
    "living-room-ac": {"power": True, "target_temp": 21.0, "mode": "auto", "fan": "auto"},
    "kitchen-light": {"power": False, "brightness": 40},
    "bedroom-sensor": {"temperature": 21.5, "humidity": 45},
    "loft-humidifier": {"power": False, "humidity": 38, "mode": "auto"},
}

#: loft-humidifier is the honest-offline row: readiness and live snapshot both say not-ok,
#: so the endpoint must project ``status.online == false`` — never an invented online.
OFFLINE_IDS = {"loft-humidifier"}

PROVIDERS = [
    {"provider": "govee", "ready": True, "configured": True, "detail": "fake govee ready"},
    {"provider": "ha", "ready": True, "configured": True, "detail": "fake ha ready"},
]


def _resolve_device(token: str, rows: list[dict]) -> dict | None:
    token = str(token or "").strip().lower()
    if not token:
        return None
    candidates = [token]
    if ":" in token:
        candidates.append(token.split(":", 1)[1])
    for candidate in candidates:
        for row in rows:
            if row["id"].lower() == candidate:
                return row
        for row in rows:
            if row["name"].lower() == candidate:
                return row
    matches = [
        row for row in rows
        if any(candidate in row["id"].lower() or candidate in row["name"].lower() for candidate in candidates)
    ]
    return matches[0] if len(matches) == 1 else None


def expected_status_rows() -> list[dict]:
    """The live snapshot the projection must consume (same shape as navig home status --json)."""
    rows = []
    for row in DEVICES:
        if row["id"] in OFFLINE_IDS:
            rows.append(
                {
                    "device": row["id"],
                    "name": row["name"],
                    "room": row["room"],
                    "kind": row["kind"],
                    "provider": row["provider"],
                    "ok": False,
                    "ready": False,
                    "error": "provider not ready (fake)",
                    "hint": "configure the provider",
                }
            )
            continue
        state = dict(INITIAL_STATE[row["id"]])
        rows.append(
            {
                "device": row["id"],
                "name": row["name"],
                "room": row["room"],
                "kind": row["kind"],
                "provider": row["provider"],
                "ok": True,
                "ready": True,
                "state": state,
                "power": "on" if state.get("power") else "off",
            }
        )
    return rows


class FakeHome:
    """Fake ``navig-home`` behind the documented HomeBridge runner seam.

    - ``devices()``/``states()``/``power()``/``toggle()``/``set()`` are the HomeBridge
      method surface from plan §2.3;
    - ``run(argv, …)`` is the same fake at the subprocess boundary (fixed-argv bridge),
      returning a CompletedProcess so a bridge calling ``subprocess.run`` directly sees it;
    - every invocation is recorded in ``calls``, every mutation in ``mutations``.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.mutations: list[tuple[str, str]] = []
        self.attempted: list[tuple[str, str]] = []
        self.fail_next_mutation = False
        self.rows = [dict(row) for row in DEVICES]
        self.states = {row["id"]: dict(INITIAL_STATE[row["id"]]) for row in self.rows}
        self.provider_rows = [dict(row) for row in PROVIDERS]

    # -- payloads ---------------------------------------------------------- #
    def list_payload(self) -> dict:
        return {"ok": True, "count": len(self.rows), "devices": [dict(r) for r in self.rows], "paths": {}}

    def status_payload(self, device: str | None = None) -> dict:
        rows = expected_status_rows()
        if device:
            wanted = _resolve_device(device, self.rows)
            keep = wanted["id"] if wanted else None
            rows = [row for row in rows if row["device"] == keep]
        fresh = []
        for row in rows:
            if row.get("ok") and row["device"] in self.states:
                state = dict(self.states[row["device"]])
                row = {**row, "state": state, "power": "on" if state.get("power") else "off"}
            fresh.append(row)
        return {"ok": True, "count": len(fresh), "devices": fresh, "providers": [dict(p) for p in self.provider_rows], "paths": {}}

    def _mutation_payload(self, verb: str, device: str, result: dict, command: dict | None = None) -> dict:
        row = self.rows[self._index(device)]
        payload = {
            "ok": True,
            "device": row["id"],
            "name": row["name"],
            "provider": row["provider"],
            "action": verb,
            "result": result,
        }
        if command is not None:
            payload["command"] = command
        return payload

    def _index(self, device: str) -> int:
        for index, row in enumerate(self.rows):
            if row["id"] == device:
                return index
        raise ValueError(f"fake home has no device {device!r}")

    def _refuse_or_apply(self, verb: str, device: str) -> dict | None:
        """Record the attempt; honour ``fail_next_mutation``. Returns a failure payload or None."""
        self.attempted.append((verb, device))
        if self.fail_next_mutation:
            self.fail_next_mutation = False
            return {
                "ok": False,
                "device": device,
                "error": "fake provider failure",
                "hint": "retry later",
            }
        return None

    def _set_power(self, device: str, on: bool) -> dict:
        row = _resolve_device(device, self.rows)
        if row is None:
            return {"ok": False, "error": f"device not found: {device}", "hint": "navig home list"}
        verb = "on" if on else "off"
        refused = self._refuse_or_apply(verb, row["id"])
        if refused is not None:
            return refused
        self.states[row["id"]]["power"] = bool(on)
        self.mutations.append((verb, row["id"]))
        return self._mutation_payload(verb, row["id"], {"changed": True})

    def _toggle(self, device: str) -> dict:
        row = _resolve_device(device, self.rows)
        if row is None:
            return {"ok": False, "error": f"device not found: {device}", "hint": "navig home list"}
        refused = self._refuse_or_apply("toggle", row["id"])
        if refused is not None:
            return refused
        current = bool(self.states[row["id"]].get("power"))
        self.states[row["id"]]["power"] = not current
        self.mutations.append(("toggle", row["id"]))
        return self._mutation_payload("toggle", row["id"], {"changed": True})

    def _set(self, device: str, assignments: dict | list | None = None) -> dict:
        row = _resolve_device(device, self.rows)
        if row is None:
            return {"ok": False, "error": f"device not found: {device}", "hint": "navig home list"}
        refused = self._refuse_or_apply("set", row["id"])
        if refused is not None:
            return refused
        if isinstance(assignments, list):
            changes: dict = {}
            for item in assignments:
                if isinstance(item, str) and "=" in item:
                    key, value = item.split("=", 1)
                    changes[key] = value
                elif isinstance(item, dict):
                    changes.update(item)
        else:
            changes = dict(assignments or {})
        self.states[row["id"]].update(changes)
        self.mutations.append(("set", row["id"]))
        return self._mutation_payload("set", row["id"], {"changed": True}, command=changes)

    # -- HomeBridge surface (plan §2.3; mirrors navig.endpoints.client.HomeBridge) -- #
    def devices(self) -> list[dict]:
        self.calls.append(["home", "list", "--json"])
        return [dict(row) for row in self.rows]

    def status(self) -> dict:
        self.calls.append(["home", "status", "--json"])
        return self.status_payload()

    def states(self) -> list[dict]:
        return list(self.status()["devices"])

    def readiness(self) -> list[dict]:
        return [dict(row) for row in self.provider_rows]

    def providers(self) -> list[dict]:
        self.calls.append(["home", "providers", "--json"])
        return [dict(row) for row in self.provider_rows]

    def power(self, device: str, on: bool) -> dict:
        self.calls.append(["home", "on" if on else "off", str(device), "--json"])
        return self._set_power(str(device), bool(on))

    def toggle(self, device: str) -> dict:
        self.calls.append(["home", "toggle", str(device), "--json"])
        return self._toggle(str(device))

    def set(self, device: str, assignments: dict | list | None = None) -> dict:
        self.calls.append(["home", "set", str(device), "--json"])
        return self._set(str(device), assignments)

    # -- subprocess boundary (same fake for the fixed-argv bridge) --------- #
    def handle(self, argv: list[str]) -> dict:
        flat = [str(item) for item in argv]
        self.calls.append(flat)
        if "home" not in flat:
            return {"ok": True, "devices": [], "count": 0}
        rest = flat[flat.index("home") + 1 :]
        if not rest:
            return {"ok": True}
        sub = rest[0]
        args = [item for item in rest[1:] if not item.startswith("-")]
        if sub == "list":
            return self.list_payload()
        if sub == "status":
            return self.status_payload(args[0] if args else None)
        if sub == "providers":
            return {"ok": True, "providers": [dict(p) for p in self.provider_rows]}
        if sub == "on":
            return self._set_power(args[0], True)
        if sub == "off":
            return self._set_power(args[0], False)
        if sub == "toggle":
            return self._toggle(args[0])
        if sub == "set":
            return self._set(args[0], args[1:])
        return {"ok": False, "error": f"fake home: unsupported subcommand {sub!r}"}

    def run(self, argv, *args, **kwargs):
        """Runner / ``subprocess.run`` replacement returning a CompletedProcess."""
        payload = self.handle([str(item) for item in argv])
        ok = bool(payload.get("ok", True))
        return subprocess.CompletedProcess(argv, 0 if ok else 1, stdout=json.dumps(payload), stderr="")

    # -- assertions -------------------------------------------------------- #
    def mutating_calls(self) -> list[list[str]]:
        mutating = {"on", "off", "toggle", "set"}
        return [call for call in self.calls if len(call) > 1 and call[0] == "home" and call[1] in mutating]


class FakeCommandRunner:
    """Fake ``navig.reflex.executor.CommandRunner`` — routes a bound ``navig …`` argv to FakeHome.

    This is the documented injection seam for the effect phase (plan §3.1: the executor
    takes an injectable ``CommandRunner``), so a commit's real argv is executed against the
    fake provider while the test still records what was run.
    """

    def __init__(self, home: FakeHome) -> None:
        self.home = home

    def run(self, argv):
        from navig.reflex.executor import CommandOutcome  # noqa: PLC0415 — public protocol type

        flat = [str(item) for item in argv]
        payload = self.home.handle(flat)
        ok = bool(payload.get("ok", True))
        return CommandOutcome(
            argv=tuple(flat),
            ok=ok,
            exit_code=0 if ok else 1,
            payload=payload,
            output=json.dumps(payload),
            error="" if ok else str(payload.get("error", "")),
        )


# --------------------------------------------------------------------------- #
# Tool-registry helpers (plan §3.2 and §4)
# --------------------------------------------------------------------------- #
def _normalise_entries(items) -> list[tuple[str, object]]:
    entries: list[tuple[str, object]] = []
    for entry in items:
        name = getattr(entry, "name", None)
        if name is None and isinstance(entry, dict):
            name = entry.get("name")
        if name is None:
            return []
        entries.append((str(name), entry))
    return entries


def tool_entries(manifest_module) -> list[tuple[str, object]]:
    """Enumerate the reflex tool registry from its public manifest module.

    The plan fixes ``manifest.py`` + a ``ToolRegistry`` with 10 tools; the module exposes
    a registry object (``navig.reflex.manifest.default_registry()``) with ``all()`` /
    ``manifests()``. Scan public data for the first enumerable tool set.
    """
    factory_names = {
        "default_registry",
        "build_registry",
        "get_registry",
        "registry",
        "tool_registry",
        "tools",
        "tool_manifests",
    }
    for attr in sorted(dir(manifest_module)):
        if attr.startswith("_"):
            continue
        value = getattr(manifest_module, attr)
        if attr.lower() in factory_names and callable(value):
            try:
                value = value()
            except TypeError:
                continue
        if isinstance(value, type):
            continue
        if isinstance(value, dict) and value:
            entries = _normalise_entries(list(value.values()))
            if entries:
                return entries
        if isinstance(value, (list, tuple)) and value:
            entries = _normalise_entries(list(value))
            if entries:
                return entries
        for method_name in ("all", "manifests", "enabled"):
            method = getattr(value, method_name, None)
            if callable(method):
                produced = method()
                if isinstance(produced, (list, tuple)) and produced:
                    entries = _normalise_entries(list(produced))
                    if entries:
                        return entries
    pytest.fail(
        "navig.reflex.manifest exposes no enumerable tool registry — plan §3.2 requires "
        "``ToolManifest`` + a ``ToolRegistry`` with the 10 tools. Expected "
        "``default_registry()``/``build_registry()`` or ``TOOL_REGISTRY``.",
        pytrace=False,
    )


def entry_field(entry, field: str):
    if isinstance(entry, dict):
        return entry.get(field)
    return getattr(entry, field, None)


def entry_command(entry) -> tuple[str, ...]:
    command = entry_field(entry, "command")
    if command is None:
        return ()
    if isinstance(command, str):
        return tuple(re.split(r"\s+", command))
    return tuple(str(part) for part in command)
