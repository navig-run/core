"""Single-source-of-truth guard for NAVIG port defaults.

The canonical port defaults live in ``config/defaults.yaml``.
``navig._daemon_defaults`` mirrors them as zero-dependency Python constants for
code paths that cannot call the config manager (dataclass/CLI argument defaults,
``.get(key, default)`` fallbacks). These tests fail loudly the moment the two
drift — keeping port selection to exactly one source of truth.
"""
from __future__ import annotations

from pathlib import Path

import yaml

import navig
from navig._daemon_defaults import _DAEMON_PORT, _GATEWAY_PORT, _OAUTH_REDIRECT_PORT


def _load_defaults() -> dict:
    # navig/__init__.py → navig/ → <navig-core>/ → config/defaults.yaml
    path = Path(navig.__file__).resolve().parent.parent / "config" / "defaults.yaml"
    assert path.exists(), f"defaults.yaml not found at {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_gateway_port_matches_defaults_yaml() -> None:
    assert _GATEWAY_PORT == _load_defaults()["gateway"]["port"]


def test_daemon_port_matches_defaults_yaml() -> None:
    assert _DAEMON_PORT == _load_defaults()["daemon"]["port"]


def test_oauth_redirect_port_matches_defaults_yaml() -> None:
    assert _OAUTH_REDIRECT_PORT == _load_defaults()["oauth"]["redirect_port"]


def test_oauth_redirect_uri_embeds_redirect_port() -> None:
    uri = _load_defaults()["oauth"]["redirect_uri"]
    assert f":{_OAUTH_REDIRECT_PORT}/" in uri, uri


def test_gateway_and_daemon_ports_never_collide() -> None:
    # A gateway that falls back to the daemon's IPC port squats it and breaks
    # every gateway-probing client (status, doctor, deck, flux, mesh).
    assert _GATEWAY_PORT != _DAEMON_PORT


def test_mesh_election_endpoint_uses_gateway_port() -> None:
    endpoint = _load_defaults().get("mesh", {}).get("election_endpoint", "")
    assert f":{_GATEWAY_PORT}/" in endpoint, (
        f"mesh.election_endpoint must use gateway.port ({_GATEWAY_PORT}); got {endpoint!r}"
    )
