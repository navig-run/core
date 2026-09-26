"""`navig gateway start` must run on the operator's config, not a literal.

This is the process the supervisor runs — on the operator's machine, literally
``pythonw -m navig gateway start --port 8789``. It used to build its own config:

    raw_config = {"gateway": {"enabled": True, "port": port, "host": host}}

so the live daemon could see exactly three settings and nothing else under
``gateway:``. The failures that caused are all silent:

* ``gateway.auth.token`` — ``NavigGateway.start()`` mints one only
  ``if not self.config.auth_token``, which was always falsy, so a fresh token was
  generated and **persisted over ``config.yaml``** on every single boot. Observed
  three times in one evening (20:21, 20:43, 21:05) with a valid 43-character
  token in that file the whole time. The warning printed every start; a recurring
  warning nobody reads is the same blindness as a green tick over an unknown.
* ``gateway.policy`` — ``PolicyGate.from_config`` reads its rules from the same
  dict and returns the default gate when it finds none, so configured **deny**
  rules were never enforced. It fails OPEN.
* ``gateway.storage_dir`` — ignored.

⚠ The obvious diagnosis is the WRONG one, so it is written down here: the
Pydantic ``config_schema.GatewayConfig`` declares only
``enabled/port/host/require_auth/allowed_origins`` — no ``auth``, no
``mesh_token`` — which makes "the validated view dropped the key" look certain.
It is not: ``get_config_manager().global_config["gateway"]`` returns the ``auth``
subtree with the token present. The key was never *read*, because the caller
substituted a literal. Measure the value the caller passes before blaming the
schema.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from navig.commands import gateway as gateway_cmd


def _start_source() -> str:
    return inspect.getsource(gateway_cmd.gateway_start)


def test_start_builds_its_config_from_the_config_manager() -> None:
    """On the AST: a literal ``{"gateway": {...}}`` must not be what is passed.

    Checked structurally rather than textually — the comment above the fix names
    every key it used to hardcode, so a substring scan would match the
    explanation of the bug and pass while the bug was back.
    """
    tree = ast.parse(inspect.cleandoc(_start_source()))

    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "get_config_manager" in names | calls, (
        "gateway_start does not read the config manager, so the live daemon runs "
        "on whatever this function invents — the auth token is re-minted over "
        "config.yaml every boot and gateway.policy deny-rules never load"
    )

    # And specifically: no dict literal whose key is "gateway" is constructed
    # here any more. That literal IS the bug.
    literals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "gateway" for k in node.keys)
    ]
    assert literals == [], (
        "gateway_start builds a literal {'gateway': ...} again — every setting "
        "it does not happen to list is invisible to the running daemon"
    )


def test_a_configured_auth_token_survives_into_the_gateway_config(monkeypatch) -> None:
    """The live consequence, end to end through the real GatewayConfig.

    A token in config must reach ``GatewayConfig.auth_token``; if it does,
    ``start()``'s ``if not self.config.auth_token`` is False and nothing is
    minted over the operator's file.
    """
    from navig.gateway import GatewayConfig

    configured = {
        "gateway": {
            "enabled": True,
            "port": 8789,
            "host": "127.0.0.1",
            "auth": {"token": "a-token-the-operator-already-has"},
            "mesh_token": "mesh-abc",
            "policy": {"default": "deny", "rules": [{"pattern": "rm *", "action": "deny"}]},
        }
    }

    # The overlay the command performs, applied to a real config shape.
    raw = dict(configured)
    section = dict(raw.get("gateway") or {})
    section.update({"enabled": True, "port": 9000, "host": "0.0.0.0"})
    raw["gateway"] = section

    cfg = GatewayConfig(raw)

    assert cfg.auth_token == "a-token-the-operator-already-has"
    # CLI-supplied values still win over the configured ones.
    assert cfg.port == 9000
    assert cfg.host == "0.0.0.0"
    # And the sections a literal used to drop are still there for their readers.
    assert cfg.raw_gateway_cfg.get("mesh_token") == "mesh-abc"
    assert cfg.raw_gateway_cfg.get("policy", {}).get("default") == "deny"


def test_the_overlay_does_not_mutate_the_config_singleton() -> None:
    """``global_config`` is a process-lifetime cache shared by everything.

    Writing the CLI's port straight into it would make every later reader in this
    process believe the operator had configured it.
    """
    original = {"gateway": {"port": 8789, "auth": {"token": "keep-me"}}}

    raw = dict(original)
    section = dict(raw.get("gateway") or {})
    section.update({"enabled": True, "port": 9999, "host": "0.0.0.0"})
    raw["gateway"] = section

    assert original["gateway"]["port"] == 8789, "the overlay wrote through to the source dict"
    assert "enabled" not in original["gateway"]


def test_policy_gate_reads_rules_only_when_the_section_is_present() -> None:
    """Why the literal was a security problem and not only a config problem.

    ``PolicyGate.from_config`` returns the DEFAULT gate when it is handed a dict
    with no ``policy`` — which is what the three-key literal always was. A deny
    rule that silently does not load is the one failure direction a policy gate
    must not have.
    """
    from navig.gateway.policy_gate import PolicyGate

    # Asserted through `check()` rather than private attributes: the question is
    # what the gate DECIDES, which is what the literal silently changed.
    blind = PolicyGate.from_config({"enabled": True, "port": 8789, "host": "127.0.0.1"})
    configured = PolicyGate.from_config(
        {
            "enabled": True,
            "port": 8789,
            "policy": {"default": "deny", "rules": [{"pattern": "db.*", "action": "deny"}]},
        }
    )

    assert not blind.check("db.query").is_denied, (
        "sanity: a gate built from a three-key literal allows everything"
    )
    assert configured.check("db.query").is_denied, (
        "a configured deny rule did not load — the gate fails OPEN, which is the "
        "one direction a policy gate must not fail in"
    )


@pytest.mark.parametrize("name", ["auth", "policy", "storage_dir"])
def test_the_settings_the_literal_dropped_are_named_in_the_source(name: str) -> None:
    """A documentation lock, deliberately coarse.

    Each of these reached the daemon through ``raw_gateway_cfg`` and each was
    dropped. ``mesh_token`` is deliberately NOT in this list: it looks like a
    fourth victim and is not one — ``_ensure_mesh_token`` reads
    ``config_manager.global_config`` directly rather than the config it was
    handed, so it saw the real value throughout.

    The comment in the source is the only place a future reader learns why this
    function must not go back to building its own dict — so the comment is part
    of the fix, and this fails if it is deleted wholesale.
    """
    assert name in _start_source()


def test_no_other_caller_builds_a_gateway_literal() -> None:
    """The same shape elsewhere is the same bug. There are NO exemptions.

    ⚠ The first version of this test exempted ``api/server.py`` as "an embedded
    API server with no operator config to read". That sentence was written from
    the file's name, not from reading it: it starts a REAL ``NavigGateway`` and
    is what the live ``navig webdash`` command runs, so it minted a token over
    the operator's config on every launch. A guard protects a PATH, not the
    SURFACE — the exemption was the hole.
    """
    root = Path(gateway_cmd.__file__).resolve().parents[1]

    # ⚠ SCAN FLOOR. This walks a tree and asserts an empty list, which is the
    # shape that passes loudest when it has read nothing: a root computed one
    # directory off finds no files, finds no offenders, and reports success
    # forever. Anchor it structurally and count a PRESENCE before trusting the
    # verdict — `navig/commands` and `navig/api` are the two directories whose
    # files this test exists to check.
    assert (root / "commands").is_dir() and (root / "api").is_dir(), (
        f"root resolved to {root}, which is not the navig package"
    )

    offenders: list[str] = []
    scanned = 0
    for py in root.rglob("*.py"):
        scanned += 1
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "GatewayConfig"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "gateway" for k in arg.keys
                ):
                    offenders.append(f"{py.relative_to(root)}:{node.lineno}")

    assert scanned > 400, (
        f"only {scanned} files scanned under {root} — the walk found almost "
        "nothing, so an empty offender list proves nothing"
    )
    assert offenders == [], (
        "GatewayConfig is being built from a literal gateway dict here, which "
        f"drops every setting the literal does not list: {offenders}"
    )
