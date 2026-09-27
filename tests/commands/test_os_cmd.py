"""``navig os`` — the verb that serves NAVIG OS to a browser from this machine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands import os_cmd

runner = CliRunner()


def _make_os_checkout(root: Path) -> Path:
    """A directory shaped like an apps/os checkout."""
    os_dir = root / "apps" / "os"
    (os_dir / "packages" / "server" / "src").mkdir(parents=True)
    (os_dir / "package.json").write_text('{"name": "navig-os"}', encoding="utf-8")
    (os_dir / "packages" / "server" / "src" / "index.ts").write_text("", encoding="utf-8")
    return os_dir


class TestFindOsDir:
    def test_finds_an_explicit_path(self, tmp_path: Path) -> None:
        os_dir = _make_os_checkout(tmp_path)
        assert os_cmd.find_os_dir(str(os_dir)) == os_dir.resolve()

    def test_reads_the_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        os_dir = _make_os_checkout(tmp_path)
        monkeypatch.setenv("NAVIG_OS_DIR", str(os_dir))
        assert os_cmd.find_os_dir() == os_dir.resolve()

    def test_walks_up_from_where_the_operator_stood(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # NOT Path.cwd(): navig chdirs into the active space, so a cwd-based walk looks for
        # the monorepo underneath whatever space is active and misses it entirely.
        os_dir = _make_os_checkout(tmp_path)
        deep = tmp_path / "some" / "nested" / "place"
        deep.mkdir(parents=True)
        monkeypatch.setattr("navig.platform.paths.invocation_cwd", lambda: deep)
        monkeypatch.delenv("NAVIG_OS_DIR", raising=False)
        assert os_cmd.find_os_dir() == os_dir.resolve()

    def test_refuses_a_directory_that_only_looks_right(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A bare package.json is not enough — the server entry has to be there too, or an
        # unrelated `apps/os` folder would be handed to `bun run` and fail confusingly.
        impostor = tmp_path / "apps" / "os"
        impostor.mkdir(parents=True)
        (impostor / "package.json").write_text("{}", encoding="utf-8")
        monkeypatch.delenv("NAVIG_OS_DIR", raising=False)
        assert os_cmd.find_os_dir(str(impostor)) is None

    def test_an_explicit_path_that_is_wrong_does_not_fall_through_to_another_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Searching on would run a DIFFERENT directory than the one the operator named,
        # report success, and leave them looking at the wrong tree. A named path is an
        # answer either way.
        real = _make_os_checkout(tmp_path)
        bogus = tmp_path / "not-here"
        monkeypatch.setattr("navig.platform.paths.invocation_cwd", lambda: real.parent)
        monkeypatch.delenv("NAVIG_OS_DIR", raising=False)
        assert os_cmd.find_os_dir() == real.resolve()          # discovery still works
        assert os_cmd.find_os_dir(str(bogus)) is None          # but a named path wins

    def test_a_wrong_env_var_does_not_fall_through_either(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real = _make_os_checkout(tmp_path)
        monkeypatch.setattr("navig.platform.paths.invocation_cwd", lambda: real.parent)
        monkeypatch.setenv("NAVIG_OS_DIR", str(tmp_path / "nope"))
        assert os_cmd.find_os_dir() is None


class TestCredentials:
    def test_generated_secrets_are_unguessable_and_distinct(self) -> None:
        tokens = {os_cmd.generate_token() for _ in range(8)}
        assert len(tokens) == 8
        assert all(len(t) == 64 for t in tokens)
        assert len({os_cmd.generate_password() for _ in range(8)}) == 8
        assert len(os_cmd.generate_password()) >= 16

    def test_missing_vault_reads_as_nothing_stored_rather_than_crashing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(os_cmd, "_vault", lambda: None)
        assert os_cmd.load_credentials() == {}
        assert os_cmd.save_credentials("t", "p") is False

    def test_a_raising_vault_is_reported_as_a_failed_save_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The caller warns on False. Returning True here would let the server start with
        # credentials that were never persisted, so the NEXT start mints different ones and
        # every saved bookmark stops working with nothing explaining why.
        def boom():
            raise RuntimeError("vault locked")

        monkeypatch.setattr(os_cmd, "_vault", boom)
        assert os_cmd.save_credentials("t", "p") is False
        assert os_cmd.load_credentials() == {}


class TestEnvironment:
    def test_carries_exactly_what_the_server_reads(self) -> None:
        env = os_cmd.build_env("tok", "pw", 9100, "127.0.0.1")
        assert env == {
            "NAVIG_SERVER_TOKEN": "tok",
            "NAVIG_WEBUI_PASSWORD": "pw",
            "NAVIG_RPC_PORT": "9100",
            "NAVIG_RPC_HOST": "127.0.0.1",
        }

    def test_redaction_hides_both_secrets_and_keeps_the_rest(self) -> None:
        red = os_cmd.redact_env(os_cmd.build_env("tok", "pw", 9100, "127.0.0.1"))
        assert red["NAVIG_SERVER_TOKEN"] == "<hidden>"
        assert red["NAVIG_WEBUI_PASSWORD"] == "<hidden>"
        assert red["NAVIG_RPC_PORT"] == "9100"
        assert "tok" not in json.dumps(red)
        assert "pw" not in json.dumps(red).replace("NAVIG_WEBUI_PASSWORD", "")


class TestProbe:
    def test_a_refused_port_is_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(*_args, **_kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", refuse)
        assert os_cmd.probe(9100, timeout=0.01) == {"reachable": False, "error": "connection refused"}

    def test_a_503_is_reachable_but_unhealthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A server answering 503 IS up. Collapsing it into "not running" would send the
        # operator looking for a process that is right there, telling them what is wrong.
        import urllib.error

        def unhealthy(*_args, **_kwargs):
            raise urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr("urllib.request.urlopen", unhealthy)
        result = os_cmd.probe(9100, timeout=0.01)
        assert result["reachable"] is True
        assert "503" in str(result["status"])


class TestServeCommand:
    def test_print_env_starts_nothing_and_prints_no_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        os_dir = _make_os_checkout(tmp_path)
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: os_dir)
        monkeypatch.setattr(os_cmd.shutil, "which", lambda _n: "/usr/bin/bun")
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: {"token": "TOKEN-SECRET", "password": "PW-SECRET"})
        monkeypatch.setattr(os_cmd, "save_credentials", lambda *_a: True)

        started: list[object] = []
        monkeypatch.setattr(os_cmd.subprocess, "run", lambda *a, **k: started.append(a))

        result = runner.invoke(os_cmd.app, ["serve", "--print-env"])
        assert result.exit_code == 0, result.output
        assert started == [], "--print-env must not start the server"
        assert "TOKEN-SECRET" not in result.output
        assert "PW-SECRET" not in result.output
        assert "<hidden>" in result.output

    def test_missing_source_fails_with_an_actionable_message(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: None)
        result = runner.invoke(os_cmd.app, ["serve"])
        assert result.exit_code == 1
        assert "--dir" in result.output or "NAVIG_OS_DIR" in result.output

    def test_a_named_directory_that_is_wrong_is_named_in_the_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # "Could not find it" would be a lie when they told us exactly where to look.
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: None)
        result = runner.invoke(os_cmd.app, ["serve", "--dir", "D:/somewhere/else"])
        assert result.exit_code == 1
        assert "somewhere/else" in result.output.replace("\\", "/")
        assert "Could not find" not in result.output

    def test_missing_bun_fails_rather_than_starting_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: _make_os_checkout(tmp_path))
        monkeypatch.setattr(os_cmd.shutil, "which", lambda _n: None)
        result = runner.invoke(os_cmd.app, ["serve"])
        assert result.exit_code == 1
        assert "bun" in result.output.lower()

    def test_a_server_that_dies_makes_the_command_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Reporting success for a server that exited non-zero is the silent-failure shape:
        # the operator sees "Starting…" and a shell prompt, and nothing says it died.
        os_dir = _make_os_checkout(tmp_path)
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: os_dir)
        monkeypatch.setattr(os_cmd.shutil, "which", lambda _n: "/usr/bin/bun")
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: {"token": "t", "password": "p"})
        monkeypatch.setattr(os_cmd, "save_credentials", lambda *_a: True)

        class Died:
            returncode = 3

        monkeypatch.setattr(os_cmd.subprocess, "run", lambda *a, **k: Died())
        result = runner.invoke(os_cmd.app, ["serve"])
        assert result.exit_code == 3, result.output

    def test_warns_when_the_credentials_could_not_be_persisted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        os_dir = _make_os_checkout(tmp_path)
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: os_dir)
        monkeypatch.setattr(os_cmd.shutil, "which", lambda _n: "/usr/bin/bun")
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: {"token": "t", "password": "p"})
        monkeypatch.setattr(os_cmd, "save_credentials", lambda *_a: False)

        result = runner.invoke(os_cmd.app, ["serve", "--print-env"])
        assert result.exit_code == 0
        assert "vault" in result.output.lower()


class TestStatusCommand:
    def test_json_output_is_machine_readable_and_carries_no_secret(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: None)
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: {"token": "TOKEN-SECRET", "password": "PW-SECRET"})
        monkeypatch.setattr(os_cmd, "probe", lambda *_a, **_k: {"reachable": False, "error": "refused"})

        result = runner.invoke(os_cmd.app, ["status", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["reachable"] is False
        assert payload["credentials_stored"] is True
        assert "TOKEN-SECRET" not in result.output
        assert "PW-SECRET" not in result.output

    def test_human_output_says_how_to_start_it_when_it_is_down(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: None)
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: {})
        monkeypatch.setattr(os_cmd, "probe", lambda *_a, **_k: {"reachable": False, "error": "refused"})

        result = runner.invoke(os_cmd.app, ["status"])
        assert result.exit_code == 0
        assert "navig os serve" in result.output
