"""Windows remote targets: exec transfer commands, target probe, install verdict.

No real SSH — every layer is mocked at execute_command / subprocess.run, so
these tests assert the exact remote command lines (the PowerShell one-liners
are the whole point: Windows OpenSSH has no `base64`).
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import navig.remote as remote_mod
from navig.commands.remote import _WINGET_PRESENT_RC, _install_verdict
from navig.remote import RemoteOperations

# 192.0.2.10 = TEST-NET-1 (RFC 5737 documentation address); no real host or
# username may appear here — the core-public-tree guard scans for both.
SERVER = {"user": "tester", "host": "192.0.2.10", "port": 22, "ssh_key": ""}
WINDOWS_OUT = "Microsoft Windows [Version 10.0.26200.9168]"


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    remote_mod._WIN_PROBE_CACHE.clear()
    yield
    remote_mod._WIN_PROBE_CACHE.clear()


@pytest.fixture()
def ops():
    return RemoteOperations(MagicMock())


def _completed(rc: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


# ── target probe ─────────────────────────────────────────────────────────────
def test_is_windows_target_true_and_cached(ops):
    with patch.object(ops, "execute_command", return_value=_completed(0, WINDOWS_OUT)) as probe:
        assert ops.is_windows_target(SERVER) is True
        assert ops.is_windows_target(SERVER) is True  # cached — no second probe
        probe.assert_called_once()
        assert probe.call_args[0][0] == "ver"


def test_is_windows_target_false_on_linux(ops):
    with patch.object(ops, "execute_command", return_value=_completed(127, "", "ver: not found")):
        assert ops.is_windows_target(SERVER) is False


def test_is_windows_target_unreachable_assumes_posix(ops):
    with patch.object(ops, "execute_command", side_effect=RuntimeError("timeout")):
        assert ops.is_windows_target(SERVER) is False


# ── exec upload ──────────────────────────────────────────────────────────────
def test_exec_upload_windows_uses_dotnet_decoder(ops, tmp_path: Path):
    src = tmp_path / "in.bin"
    payload = bytes(range(256))
    src.write_bytes(payload)

    run_calls: list[dict] = []

    def fake_run(args, **kwargs):
        run_calls.append({"args": args, "input": kwargs.get("input")})
        return _completed(0)

    # probe → windows, then hash verify (uppercase hex, like Get-FileHash)
    sha = hashlib.sha256(payload).hexdigest()
    with (
        patch.object(
            ops,
            "execute_command",
            side_effect=[_completed(0, WINDOWS_OUT), _completed(0, sha.upper())],
        ),
        patch("navig.remote.subprocess.run", side_effect=fake_run),
    ):
        assert ops._exec_upload(src, "C:/Users/tester/out file.bin", SERVER) is True

    cmd = run_calls[0]["args"][-1]
    assert "FromBase64String" in cmd
    assert "WriteAllBytes" in cmd
    assert "C:/Users/tester/out file.bin" in cmd
    assert "$" not in cmd  # must parse under cmd.exe AND PowerShell DefaultShell
    assert run_calls[0]["input"] == base64.b64encode(payload)


def test_exec_upload_posix_unchanged(ops, tmp_path: Path):
    src = tmp_path / "in.bin"
    payload = b"posix-path"
    src.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()

    run_calls: list[dict] = []

    def fake_run(args, **kwargs):
        run_calls.append({"args": args, "input": kwargs.get("input")})
        return _completed(0)

    with (
        patch.object(ops, "execute_command", side_effect=[_completed(127, ""), _completed(0, sha)]),
        patch("navig.remote.subprocess.run", side_effect=fake_run),
    ):
        assert ops._exec_upload(src, "/tmp/out.bin", SERVER) is True
    cmd = run_calls[0]["args"][-1]
    assert cmd.startswith("base64 -d > ")
    assert "powershell" not in cmd


def test_exec_upload_hash_mismatch_fails(ops, tmp_path: Path):
    src = tmp_path / "in.bin"
    src.write_bytes(b"x")
    with (
        patch.object(ops, "execute_command", side_effect=[_completed(0, WINDOWS_OUT), _completed(0, "DEADBEEF")]),
        patch("navig.remote.subprocess.run", return_value=_completed(0)),
    ):
        assert ops._exec_upload(src, "C:/x.bin", SERVER) is False


# ── exec download ────────────────────────────────────────────────────────────
def test_exec_download_windows_uses_dotnet_encoder(ops, tmp_path: Path):
    blob = b"round-trip-bytes"
    encoded = base64.b64encode(blob).decode()
    seen: list[str] = []

    def fake_exec(cmd, server_config, **kwargs):
        seen.append(cmd)
        if cmd == "ver":
            return _completed(0, WINDOWS_OUT)
        return _completed(0, encoded)

    with patch.object(ops, "execute_command", side_effect=fake_exec):
        dst = tmp_path / "out.bin"
        assert ops._exec_download("C:/dir/file with space.bin", dst, SERVER) is True
    assert dst.read_bytes() == blob
    encode_cmd = seen[-1]
    assert "ToBase64String" in encode_cmd and "ReadAllBytes" in encode_cmd
    assert "$" not in encode_cmd


def test_exec_download_posix_unchanged(ops, tmp_path: Path):
    blob = b"posix"
    encoded = base64.b64encode(blob).decode()
    seen: list[str] = []

    def fake_exec(cmd, server_config, **kwargs):
        seen.append(cmd)
        if cmd == "ver":
            return _completed(127, "")
        return _completed(0, encoded)

    with patch.object(ops, "execute_command", side_effect=fake_exec):
        dst = tmp_path / "out.bin"
        assert ops._exec_download("/tmp/f.bin", dst, SERVER) is True
    assert dst.read_bytes() == blob
    assert seen[-1].startswith("base64 ")


def test_ps_quote_doubles_apostrophes():
    assert RemoteOperations._ps_quote("C:/o'brien/x.txt") == "C:/o''brien/x.txt"


# ── install verdict ──────────────────────────────────────────────────────────
def test_install_verdict_zero_is_success():
    assert _install_verdict("winget", 0, "") == (True, False)
    assert _install_verdict("apt-get", 0, "") == (True, False)


def test_install_verdict_winget_truncated_hresult_is_present():
    # 0x8A15002B arrives byte-truncated over ssh as 43
    assert _install_verdict("winget", _WINGET_PRESENT_RC, "") == (True, True)
    assert _WINGET_PRESENT_RC == 43


def test_install_verdict_winget_text_marker_is_present():
    ok, present = _install_verdict(
        "winget", 99, "No available upgrade found.\nNo newer package versions"
    )
    assert (ok, present) == (True, True)


def test_install_verdict_other_pm_never_present():
    # apt failing with 43 must stay a failure — the marker is winget-specific
    assert _install_verdict("apt-get", 43, "No available upgrade found") == (False, False)
    assert _install_verdict("choco", 43, "") == (False, False)


def test_install_verdict_real_failure():
    assert _install_verdict("winget", 1, "No package found matching criteria") == (False, False)
