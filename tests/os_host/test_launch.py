"""`navig.os_host.launch` — mint a one-time launch code, open the browser on it.

The kiosk session runs this module as a restricted user who must not hold the server
token or the web password; connecting to the server's launch socket IS the credential.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from navig.os_host import launch

CODE = "A" * 22 + "b-_" + "c" * 18  # 43 chars, base64url — the shape the server mints

unix_only = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs AF_UNIX")


def _serve_once(path: str, reply: bytes) -> threading.Thread:
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def run() -> None:
        conn, _ = srv.accept()
        conn.sendall(reply)
        conn.close()
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


class TestUrlAndArgv:
    def test_the_code_rides_in_the_fragment_never_a_query(self) -> None:
        url = launch.launch_url(CODE, 9100)
        assert url == f"http://127.0.0.1:9100/launch#c={CODE}"
        assert "?" not in url

    def test_port_comes_from_the_environment_when_not_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NAVIG_OS_PORT", "9200")
        assert launch.launch_url(CODE).startswith("http://127.0.0.1:9200/")
        monkeypatch.setenv("NAVIG_OS_PORT", "not-a-port")
        assert launch.launch_url(CODE).startswith("http://127.0.0.1:9100/")

    def test_kiosk_is_full_screen_without_a_keyring_prompt(self) -> None:
        argv = launch.browser_argv("/usr/bin/chromium", "http://x/launch#c=1", kiosk=True)
        assert argv[0] == "/usr/bin/chromium"
        assert "--kiosk" in argv
        assert "--password-store=basic" in argv
        assert argv[-1] == "http://x/launch#c=1"

    def test_app_mode_is_a_window_on_the_url(self) -> None:
        argv = launch.browser_argv("chromium", "http://x/launch#c=1", kiosk=False)
        assert "--app=http://x/launch#c=1" in argv
        assert "--kiosk" not in argv

    def test_socket_path_defaults_and_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NAVIG_LAUNCH_SOCKET", raising=False)
        assert launch.socket_path() == launch.DEFAULT_SOCKET
        monkeypatch.setenv("NAVIG_LAUNCH_SOCKET", "/run/x.sock")
        assert launch.socket_path() == "/run/x.sock"


class TestImportsNothingElseFromNavig:
    def test_module_is_self_contained(self) -> None:
        # The kiosk user must not trigger onboarding or grow a ~/.navig: this module may
        # import the standard library and nothing from navig beyond its own package.
        # Read the IMPORTS, not the text: the docstring says "from navig" in prose.
        import ast

        tree = ast.parse(Path(launch.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert imported, "found no imports at all — the scan is broken, not the module"
        assert {m for m in imported if m.split(".")[0] == "navig"} == set()


@pytest.fixture
def sock() -> Path:
    """A socket path short enough for ``AF_UNIX``.

    ``sockaddr_un.sun_path`` holds ~108 bytes on Linux; pytest's ``tmp_path`` embeds the
    whole test name, and the longer names in this class pushed the joined path past it, so
    ``bind`` failed before the behaviour under test ran (2026-10-30, outpost).
    """
    d = Path(tempfile.mkdtemp(prefix="ngh"))
    try:
        yield d / "s"
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestMintOverASocket:
    def test_reads_the_code_the_server_hands_out(self, sock: Path) -> None:
        path = str(sock)
        t = _serve_once(path, f"{CODE}\n".encode())
        assert launch.mint_code(path) == CODE
        t.join(2)

    def test_garbage_is_not_a_code(self, sock: Path) -> None:
        path = str(sock)
        _serve_once(path, b"<html>not a code</html>")
        with pytest.raises(launch.LaunchError, match="not a launch code"):
            launch.mint_code(path)

    def test_no_socket_says_the_server_is_not_running(self, sock: Path) -> None:
        with pytest.raises(launch.LaunchError, match="not running"):
            launch.mint_code(str(sock.parent / "absent.sock"))

    @pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="root ignores modes")
    def test_a_socket_this_account_may_not_open_names_the_group(self, sock: Path) -> None:
        path = str(sock)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        os.chmod(path, 0o000)
        try:
            with pytest.raises(launch.LaunchError, match="navig-launch"):
                launch.mint_code(path)
        finally:
            srv.close()

    def test_main_print_url_prints_one_working_url(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = str(sock)
        _serve_once(path, f"{CODE}\n".encode())
        assert launch.main(["--print-url", "--socket", path, "--port", "9123"]) == 0
        assert capsys.readouterr().out.strip() == f"http://127.0.0.1:9123/launch#c={CODE}"


def test_main_reports_a_missing_server_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_path=None, timeout=5.0):
        raise launch.LaunchError("NAVIG OS is not running here")

    monkeypatch.setattr(launch, "mint_code", refuse)
    assert launch.main(["--print-url"]) == 1
    assert "not running" in capsys.readouterr().err


def test_main_without_a_browser_fails_clearly(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launch, "mint_code", lambda *_a, **_k: CODE)
    monkeypatch.setattr(launch, "find_browser", lambda _e="": None)
    monkeypatch.setattr(launch.os, "execv", lambda *a: pytest.fail("must not exec"))
    assert launch.main(["--kiosk"]) == 1
    assert "chromium" in capsys.readouterr().err.lower()


def test_main_replaces_itself_with_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "mint_code", lambda *_a, **_k: CODE)
    monkeypatch.setattr(launch, "find_browser", lambda _e="": "/usr/bin/chromium")
    seen: list[list[str]] = []
    monkeypatch.setattr(launch.os, "execv", lambda exe, argv: seen.append(argv))
    launch.main(["--kiosk", "--port", "9100"])
    assert seen and seen[0][0] == "/usr/bin/chromium"
    assert seen[0][-1] == f"http://127.0.0.1:9100/launch#c={CODE}"
