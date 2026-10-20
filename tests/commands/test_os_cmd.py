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
            "NAVIG_OS_ANNOUNCE": "1",
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


class TestHostedLink:
    """The os.navig.run connect link. Its format is a contract with the web UI's
    `parseConnectFragment` (apps/os/apps/webui/src/hosted/connection.ts), whose test
    parses exactly the string asserted first here."""

    def test_matches_what_the_web_ui_parses(self) -> None:
        assert os_cmd.hosted_link("abc123", 9100) == (
            "https://os.navig.run/#server=http%3A%2F%2F127.0.0.1%3A9100&token=abc123"
        )

    def test_the_token_rides_in_the_fragment_never_the_query(self) -> None:
        # A fragment is not sent in the HTTP request, so the token never reaches the
        # site's access logs. A query string would be.
        link = os_cmd.hosted_link("secret-token", 9100)
        assert "?" not in link
        assert link.index("#") < link.index("secret-token")

    def test_a_wildcard_bind_is_dialled_as_loopback(self) -> None:
        for wildcard in ("0.0.0.0", "::"):
            assert "server=http%3A%2F%2F127.0.0.1%3A9100" in os_cmd.hosted_link("t", 9100, wildcard)

    def test_an_ipv6_host_is_bracketed(self) -> None:
        assert "server=http%3A%2F%2F%5B%3A%3A1%5D%3A9100" in os_cmd.hosted_link("t", 9100, "::1")

    def test_serve_prints_the_link_but_print_env_does_not(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        os_dir = tmp_path / "os"
        (os_dir / "packages" / "server" / "src").mkdir(parents=True)
        (os_dir / "package.json").write_text("{}", encoding="utf-8")
        (os_dir / "packages" / "server" / "src" / "index.ts").write_text("", encoding="utf-8")
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: os_dir)
        monkeypatch.setattr(os_cmd.shutil, "which", lambda _name: "bun")
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: {"token": "tok-xyz", "password": "pw"})
        monkeypatch.setattr(os_cmd, "save_credentials", lambda *_a: True)

        class _Done:
            returncode = 0

        monkeypatch.setattr(os_cmd.subprocess, "run", lambda *_a, **_k: _Done())
        runner = CliRunner()

        dry = runner.invoke(os_cmd.app, ["serve", "--print-env"], env={"COLUMNS": "400"})
        assert dry.exit_code == 0
        assert "tok-xyz" not in dry.output

        real = runner.invoke(os_cmd.app, ["serve"], env={"COLUMNS": "400"})
        assert real.exit_code == 0
        assert "os.navig.run/#server=" in real.output
        assert "token=tok-xyz" in real.output


class TestRemoteCommands:
    """`navig os pair` / `navig os devices` — thin, honest front-ends over the daemon."""

    @pytest.fixture()
    def daemon(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[tuple[str, str, object]] = []
        replies: dict[tuple[str, str], tuple[int, dict]] = {}

        def _fake(method, url, body=None, timeout=10.0):
            path = "/" + url.split("/", 3)[3]
            calls.append((method, path, body))
            if (method, path) not in replies:
                raise os_cmd.DaemonUnavailable("refused")
            return replies[(method, path)]

        monkeypatch.setattr(os_cmd, "_http", _fake)
        return calls, replies

    def test_pair_says_how_to_start_navig_when_it_is_not_running(self, daemon) -> None:
        result = CliRunner().invoke(os_cmd.app, ["pair"])
        assert result.exit_code == 1
        assert "navig start" in result.output

    def test_pair_prints_the_link_and_json_carries_it(self, daemon, monkeypatch: pytest.MonkeyPatch) -> None:
        _, replies = daemon
        link = "https://os.navig.run/#pair=1&v=1&e=https%3A%2F%2Fe&t=" + "a" * 64 + "&c=code"
        replies[("POST", "/api/deck/os/pair")] = (200, {"link": link, "expires_at": 1.0, "ttl_s": 120})
        monkeypatch.setattr(os_cmd, "terminal_qr", lambda data: None)
        human = CliRunner().invoke(os_cmd.app, ["pair"], env={"COLUMNS": "400"})
        assert human.exit_code == 0
        assert link in human.output.replace("\n", "")
        machine = CliRunner().invoke(os_cmd.app, ["pair", "--json"])
        assert json.loads(machine.output) == {"ok": True, "link": link, "expires_at": 1.0}

    def test_pair_passes_on_the_daemons_reason(self, daemon) -> None:
        _, replies = daemon
        replies[("POST", "/api/deck/os/pair")] = (409, {"error": "edge_outdated", "message": "update it: navig lighthouse redeploy"})
        result = CliRunner().invoke(os_cmd.app, ["pair"], env={"COLUMNS": "400"})
        assert result.exit_code == 1
        assert "navig lighthouse redeploy" in result.output

    def test_devices_lists_and_revoke_removes(self, daemon) -> None:
        calls, replies = daemon
        replies[("GET", "/api/deck/os/devices")] = (200, {"devices": [
            {"id": "d1", "name": "Safari on iPhone", "created_at": "2026-09-29T10:00:00Z", "last_seen": None,
             "msgs_today": 12, "connected": True},
        ]})
        listed = CliRunner().invoke(os_cmd.app, ["devices"], env={"COLUMNS": "200"})
        assert listed.exit_code == 0 and "Safari on iPhone" in listed.output and "d1" in listed.output
        as_json = CliRunner().invoke(os_cmd.app, ["devices", "--json"])
        assert json.loads(as_json.output)["devices"][0]["id"] == "d1"

        replies[("DELETE", "/api/deck/os/devices/d1")] = (200, {"revoked": True, "sessions_ended": 1})
        revoked = CliRunner().invoke(os_cmd.app, ["devices", "revoke", "d1"])
        assert revoked.exit_code == 0 and "Removed d1" in revoked.output
        replies[("DELETE", "/api/deck/os/devices/nope")] = (404, {"error": "not_found"})
        missing = CliRunner().invoke(os_cmd.app, ["devices", "revoke", "nope"])
        assert missing.exit_code == 1

    def test_link_prints_the_link_and_json_carries_it(self, daemon) -> None:
        _, replies = daemon
        link = "https://e.workers.dev/os/" + "a" * 64 + "#link=code&v=1"
        replies[("POST", "/api/deck/os/link")] = (200, {"link": link, "expires_at": 2.0, "ttl_s": 600})
        human = CliRunner().invoke(os_cmd.app, ["link"], env={"COLUMNS": "400"})
        assert human.exit_code == 0
        assert link in human.output.replace("\n", "")
        assert "10 minutes" in human.output
        machine = CliRunner().invoke(os_cmd.app, ["link", "--json"])
        assert json.loads(machine.output) == {"ok": True, "link": link, "expires_at": 2.0}

    def test_link_fails_loudly_with_the_daemons_reason(self, daemon) -> None:
        _, replies = daemon
        replies[("POST", "/api/deck/os/link")] = (409, {"error": "edge_outdated", "message": "update it: navig lighthouse redeploy"})
        result = CliRunner().invoke(os_cmd.app, ["link"], env={"COLUMNS": "400"})
        assert result.exit_code == 1
        assert "navig lighthouse redeploy" in result.output

    def test_devices_tells_a_linked_machine_from_a_phone(self, daemon) -> None:
        _, replies = daemon
        replies[("GET", "/api/deck/os/devices")] = (200, {"devices": [
            {"id": "d1", "name": "Safari on iPhone", "kind": "remote"},
            {"id": "d2", "name": "navig-os-lab", "kind": "brain"},
        ]})
        listed = CliRunner().invoke(os_cmd.app, ["devices"], env={"COLUMNS": "200"})
        assert listed.exit_code == 0
        row = {d: next(line for line in listed.output.splitlines() if f" {d} " in line) for d in ("d1", "d2")}
        assert "this brain" in row["d2"] and "this brain" not in row["d1"]

    def test_terminal_qr_is_none_without_the_optional_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def _no_qrcode(name, *a, **k):
            if name == "qrcode":
                raise ImportError("not installed")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_qrcode)
        assert os_cmd.terminal_qr("https://os.navig.run/#x") is None


# ---------------------------------------------------------------------------
# A Linux install: a prebuilt server tree, no checkout, no bun
# ---------------------------------------------------------------------------


def _make_installed_tree(root: Path) -> Path:
    """A directory shaped like REAL `build-server.ts` output (what navig-os-server installs).

    Real means it ALSO holds package.json and packages/server/src/index.ts — the tree is
    assembled source run by its own bun. A fixture without them hid the bug where the
    installed tree was taken for a checkout and handed to a system bun that is not there.
    """
    tree = root / "opt" / "navig-os" / "server"
    (tree / "bin").mkdir(parents=True)
    (tree / "packages" / "server" / "src").mkdir(parents=True)
    (tree / "launch.json").write_text('{"version": "0.10.4"}', encoding="utf-8")
    (tree / "bin" / "navig-server").write_text("#!/bin/sh\n", encoding="utf-8")
    (tree / "package.json").write_text('{"name": "navig-os"}', encoding="utf-8")
    (tree / "packages" / "server" / "src" / "index.ts").write_text("", encoding="utf-8")
    return tree


class TestInstalledServer:
    def test_finds_the_packaged_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tree = _make_installed_tree(tmp_path)
        monkeypatch.delenv("NAVIG_OS_DIR", raising=False)
        monkeypatch.setattr(os_cmd, "INSTALLED_SERVER_DIRS", (tree,))
        assert os_cmd.find_installed_server() == tree

    def test_a_named_directory_is_authoritative(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tree = _make_installed_tree(tmp_path)
        monkeypatch.setattr(os_cmd, "INSTALLED_SERVER_DIRS", (tree,))
        monkeypatch.setenv("NAVIG_OS_DIR", str(tmp_path / "elsewhere"))
        assert os_cmd.find_installed_server() is None

    def test_an_installed_tree_is_never_taken_for_a_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Exactly what the navig-os-server unit sets: NAVIG_OS_DIR=/opt/navig-os/server.
        tree = _make_installed_tree(tmp_path)
        monkeypatch.setenv("NAVIG_OS_DIR", str(tree))
        assert os_cmd.find_os_dir() is None
        assert os_cmd.find_installed_server() == tree.resolve()

    def test_serve_with_the_units_env_runs_the_tree_not_bun(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tree = _make_installed_tree(tmp_path)
        monkeypatch.setenv("NAVIG_OS_DIR", str(tree))
        monkeypatch.delenv("NAVIG_SERVICE", raising=False)
        monkeypatch.delenv("JOURNAL_STREAM", raising=False)
        monkeypatch.setattr(os_cmd.shutil, "which", lambda _n: None)  # no bun on this box
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: {"token": "t", "password": "p"})
        monkeypatch.setattr(os_cmd, "save_credentials", lambda *_a: True)
        seen: list = []

        class _Done:
            returncode = 0

        def fake_run(argv, **_kw):
            seen.append(argv)
            return _Done()

        monkeypatch.setattr(os_cmd.subprocess, "run", fake_run)
        result = runner.invoke(os_cmd.app, ["serve"])
        assert result.exit_code == 0, result.output
        assert seen == [[str(tree.resolve() / "bin" / "navig-server")]]

    def test_a_launch_json_alone_is_not_a_server(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tree = tmp_path / "half"
        tree.mkdir()
        (tree / "launch.json").write_text("{}", encoding="utf-8")
        monkeypatch.delenv("NAVIG_OS_DIR", raising=False)
        assert os_cmd.find_installed_server(str(tree)) is None

    def test_serve_runs_the_tree_and_needs_no_bun(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tree = _make_installed_tree(tmp_path)
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: None)
        monkeypatch.setattr(os_cmd, "find_installed_server", lambda *_a, **_k: tree)
        monkeypatch.setattr(os_cmd.shutil, "which", lambda _n: pytest.fail("bun must not be required"))
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: {"token": "t", "password": "p"})
        monkeypatch.setattr(os_cmd, "save_credentials", lambda *_a: True)
        monkeypatch.delenv("NAVIG_SERVICE", raising=False)
        monkeypatch.delenv("JOURNAL_STREAM", raising=False)

        seen: list[tuple] = []

        class _Done:
            returncode = 0

        def fake_run(*a, **k):
            seen.append((a, k))
            return _Done()

        monkeypatch.setattr(os_cmd.subprocess, "run", fake_run)
        result = runner.invoke(os_cmd.app, ["serve"])
        assert result.exit_code == 0, result.output
        (argv,), kwargs = seen[0]
        assert argv == [str(tree / "bin" / "navig-server")]
        assert kwargs["cwd"] == str(tree)

    def test_print_env_names_the_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tree = _make_installed_tree(tmp_path)
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: None)
        monkeypatch.setattr(os_cmd, "find_installed_server", lambda *_a, **_k: tree)
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: {"token": "t", "password": "p"})
        monkeypatch.setattr(os_cmd, "save_credentials", lambda *_a: True)
        result = runner.invoke(os_cmd.app, ["serve", "--print-env"], env={"COLUMNS": "400"})
        assert result.exit_code == 0, result.output
        assert "navig-server" in result.output

    def test_nothing_found_points_at_the_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: None)
        monkeypatch.setattr(os_cmd, "find_installed_server", lambda *_a, **_k: None)
        monkeypatch.delenv("NAVIG_OS_DIR", raising=False)
        result = runner.invoke(os_cmd.app, ["serve"], env={"COLUMNS": "400"})
        assert result.exit_code == 1
        assert "apt install navig-os-server" in result.output


class TestSecretsStayOutOfTheJournal:
    """Under a systemd unit, the output of `serve` is the journal: other accounts read it."""

    def _serve(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored: dict[str, str]):
        os_dir = _make_os_checkout(tmp_path)
        monkeypatch.setattr(os_cmd, "find_os_dir", lambda *_a, **_k: os_dir)
        monkeypatch.setattr(os_cmd.shutil, "which", lambda _n: "/usr/bin/bun")
        monkeypatch.setattr(os_cmd, "load_credentials", lambda: stored)
        monkeypatch.setattr(os_cmd, "save_credentials", lambda *_a: True)
        monkeypatch.setattr(os_cmd, "generate_password", lambda: "FRESH-PW-SECRET")

        class _Done:
            returncode = 0

        monkeypatch.setattr(os_cmd.subprocess, "run", lambda *_a, **_k: _Done())
        return runner.invoke(os_cmd.app, ["serve"], env={"COLUMNS": "400"})

    def test_a_unit_never_prints_the_password_or_the_token_link(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NAVIG_SERVICE", "1")
        result = self._serve(tmp_path, monkeypatch, {"token": "TOKEN-SECRET", "password": ""})
        assert result.exit_code == 0, result.output
        assert "FRESH-PW-SECRET" not in result.output
        assert "TOKEN-SECRET" not in result.output
        assert "--password" in result.output  # it still says how to set one

    def test_a_terminal_still_gets_both_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_SERVICE", raising=False)
        monkeypatch.delenv("JOURNAL_STREAM", raising=False)
        result = self._serve(tmp_path, monkeypatch, {"token": "TOKEN-SECRET", "password": ""})
        assert result.exit_code == 0, result.output
        assert "FRESH-PW-SECRET" in result.output
        assert "token=TOKEN-SECRET" in result.output

    def test_an_inherited_journal_stream_that_is_not_our_stdout_does_not_count(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # JOURNAL_STREAM leaks into terminal shells on some desktops and is inherited by
        # children whose stdout is a pipe. Only a dev:inode MATCH means "this is the journal".
        monkeypatch.delenv("NAVIG_SERVICE", raising=False)
        monkeypatch.setenv("JOURNAL_STREAM", "1:1")
        assert os_cmd._output_is_a_log() is False

    def test_a_matching_journal_stream_counts(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import os
        import sys

        target = tmp_path / "journal"
        target.write_text("")
        with target.open("w") as fh:
            st = os.fstat(fh.fileno())
            monkeypatch.setattr(sys, "stdout", fh)
            monkeypatch.delenv("NAVIG_SERVICE", raising=False)
            monkeypatch.setenv("JOURNAL_STREAM", f"{st.st_dev}:{st.st_ino}")
            assert os_cmd._output_is_a_log() is True


class TestOpenCommand:
    CODE = "Z" * 43

    def test_print_url_prints_the_one_time_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from navig.os_host import launch

        monkeypatch.setattr(launch, "mint_code", lambda *_a, **_k: self.CODE)
        result = runner.invoke(os_cmd.app, ["open", "--print-url", "--port", "9100"])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == f"http://127.0.0.1:9100/launch#c={self.CODE}"

    def test_a_server_that_is_not_running_fails_with_the_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from navig.os_host import launch

        def refuse(*_a, **_k):
            raise launch.LaunchError("NAVIG OS is not running here (no launch socket at /run/x)")

        monkeypatch.setattr(launch, "mint_code", refuse)
        result = runner.invoke(os_cmd.app, ["open"], env={"COLUMNS": "400"})
        assert result.exit_code == 1
        assert "not running" in result.output

    def test_opens_a_detached_kiosk_browser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from navig.os_host import launch

        monkeypatch.setattr(launch, "mint_code", lambda *_a, **_k: self.CODE)
        monkeypatch.setattr(launch, "find_browser", lambda _e="": "/usr/bin/chromium")
        spawned: list[tuple] = []
        monkeypatch.setattr(os_cmd.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)))
        result = runner.invoke(os_cmd.app, ["open", "--kiosk"])
        assert result.exit_code == 0, result.output
        argv, kw = spawned[0]
        assert argv[0] == "/usr/bin/chromium" and "--kiosk" in argv
        assert kw["start_new_session"] is True
