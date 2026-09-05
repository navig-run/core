"""keyscan_and_trust must not leak a temp file.

It used to open a ``NamedTemporaryFile(delete=False)`` it never wrote, read, or
deleted (the scanned keys go straight to known_hosts), leaking a 0-byte
``*.keyscan`` file on every host-key heal.
"""

from __future__ import annotations

import asyncio
import tempfile
from unittest.mock import MagicMock

import navig.selfheal.ssh_healer as sh


async def test_keyscan_and_trust_leaves_no_temp_file(tmp_path, monkeypatch):
    # Isolate: known_hosts and any NamedTemporaryFile output land under tmp_path.
    monkeypatch.setattr(sh, "_KNOWN_HOSTS_PATH", tmp_path / "known_hosts")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    # Mock ssh-keyscan: return code 0 with one fake host-key line.
    fake_proc = MagicMock()
    fake_proc.returncode = 0

    async def fake_exec(*args, **kwargs):
        return fake_proc

    async def fake_communicate(proc, timeout):
        return (b"example.com ssh-ed25519 AAAAFAKEKEY\n", b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(sh, "communicate_or_kill", fake_communicate)

    result = await sh.SSHHealer().keyscan_and_trust("example.com")

    assert result.status == "resolved"
    assert (tmp_path / "known_hosts").read_bytes().startswith(b"example.com")
    # The dead NamedTemporaryFile(delete=False) block leaked one *.keyscan per call.
    assert list(tmp_path.glob("*.keyscan")) == []
