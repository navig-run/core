"""`navig vault status/set/get/run/recovery/backup/restore/devices/grant` on a REAL vault,
plus the /api/vault/v1 route over real HTTP and host-credential resolution
through an enrolled device.

Isolation: every test points NAVIG_VAULT_DIR at its own tmp dir and drops the
get_vault() singleton first — the operator's vault is never opened.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.vault import vault_app

pytestmark = pytest.mark.integration

SECRET = "Pa55-vps-ssh"


@pytest.fixture
def real_vault(tmp_path, monkeypatch):
    from navig_vault import keyslots
    from navig_vault.session import SessionStore

    from navig.vault import core as vault_core

    vdir = tmp_path / "vault"
    monkeypatch.setenv("NAVIG_VAULT_DIR", str(vdir))
    monkeypatch.setattr(vault_core, "_vault", None)
    keyslots.forget_cached_vmks()
    SessionStore.clear()
    vault = vault_core.get_vault()
    assert Path(vault.vault_dir) == vdir, f"vault escaped isolation: {vault.vault_dir}"
    yield vault
    vault._store.close()
    keyslots.forget_cached_vmks()
    SessionStore.clear()


def _run(args: list[str], stdin: str | None = None):
    return CliRunner().invoke(vault_app, args, input=stdin)


# ── set / get with vault:// ─────────────────────────────────────────────────


def test_set_from_stdin_and_get_masks_by_default(real_vault):
    res = _run(["set", "vault://hosts/cybesis-vps/ssh", "--stdin"], stdin=SECRET + "\n")
    assert res.exit_code == 0, res.output
    assert SECRET not in res.output
    assert real_vault._store.get("host/cybesis-vps/ssh_password") is not None

    masked = _run(["get", "vault://hosts/cybesis-vps/ssh"])
    assert masked.exit_code == 0 and SECRET not in masked.output and "*" in masked.output
    raw = _run(["get", "vault://hosts/cybesis-vps/ssh", "--raw"])
    assert raw.output.strip() == SECRET


def test_set_expect_version_is_compare_and_swap(real_vault):
    assert _run(["set", "vault://ci/token", "--stdin", "--expect-version", "0"], stdin="a\n").exit_code == 0
    clash = _run(["set", "vault://ci/token", "--stdin", "--expect-version", "0"], stdin="b\n")
    assert clash.exit_code == 1 and "version 1" in clash.output
    assert _run(["get", "vault://ci/token", "--raw"]).output.strip() == "a"


def test_a_positional_secret_still_works_but_warns(real_vault):
    res = _run(["set", "vault://ci/legacy", "hunter2"])
    assert res.exit_code == 0
    assert "shell history" in res.output


def test_get_of_a_missing_ref_exits_1(real_vault):
    assert _run(["get", "vault://nothing/here", "--raw"]).exit_code == 1


# ── run ─────────────────────────────────────────────────────────────────────


def test_run_injects_env_and_files_never_argv(real_vault, tmp_path):
    _run(["set", "vault://ci/token", "--stdin"], stdin=SECRET + "\n")
    probe = tmp_path / "probe.py"
    report = tmp_path / "report.json"
    # The child's stdout bypasses CliRunner (it inherits the real fd), so the probe
    # writes what it saw to a file the test reads back.
    probe.write_text(
        "import os, sys, json, pathlib\n"
        "f = pathlib.Path(os.environ['TOKF'])\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'argv': sys.argv,"
        " 'env_ok': os.environ['TOK'] == f.read_text(), 'env_len': len(os.environ['TOK']),"
        " 'file_path': str(f)}))\n",
        encoding="utf-8",
    )
    res = _run(["run", "-e", "TOK=vault://ci/token", "-f", "TOKF=vault://ci/token", "--",
                sys.executable, str(probe), str(report)])
    assert res.exit_code == 0, res.output
    seen = json.loads(report.read_text(encoding="utf-8"))
    assert seen["env_ok"] and seen["env_len"] == len(SECRET)
    assert SECRET not in json.dumps(seen["argv"]), "the secret reached the child's argv"
    assert not Path(seen["file_path"]).exists(), "the secret file must be deleted when the command exits"


def test_run_passes_the_exit_code_through(real_vault):
    _run(["set", "vault://ci/token", "--stdin"], stdin="x\n")
    res = _run(["run", "-e", "T=vault://ci/token", "--", sys.executable, "-c", "raise SystemExit(7)"])
    assert res.exit_code == 7


def test_run_refuses_an_unresolvable_ref_before_starting_anything(real_vault, tmp_path):
    marker = tmp_path / "ran"
    res = _run(["run", "-e", "T=vault://missing/x", "--", sys.executable, "-c",
                f"open(r'{marker}','w').write('x')"])
    assert res.exit_code == 1 and not marker.exists()


# ── status / recovery / backup / restore ────────────────────────────────────


def test_status_recovery_backup_restore_round_trip(real_vault, tmp_path):
    _run(["set", "vault://hosts/cybesis-vps/ssh", "--stdin"], stdin=SECRET + "\n")
    before = json.loads(_run(["status", "--json"]).output)
    assert before["recovery"] is False and before["mode"] == "keyslots"

    init = _run(["recovery", "init"])
    assert init.exit_code == 0, init.output
    key = next(w for w in init.output.split() if w.startswith("NAVIG-RK1-"))

    assert _run(["recovery", "verify"], stdin=key + "\n").exit_code == 0
    assert _run(["recovery", "verify"], stdin="NAVIG-RK1-AAAAA\n").exit_code == 1
    assert _run(["recovery", "init"]).exit_code == 1, "a second init must not silently replace the key"

    out = tmp_path / "b.json"
    assert _run(["backup", "--to", str(out)]).exit_code == 0
    assert SECRET.encode() not in out.read_bytes()

    restored = _run(["restore", str(out), "--into", str(tmp_path / "restored")], stdin=key + "\n")
    assert restored.exit_code == 0, restored.output
    assert "1 item(s)" in restored.output

    after = json.loads(_run(["status", "--json"]).output)
    assert after["recovery"] is True


# ── federation verbs + host fallback ────────────────────────────────────────


def test_enroll_grant_and_list_devices(real_vault):
    _run(["set", "vault://hosts/cybesis-vps/ssh", "--stdin"], stdin=SECRET + "\n")
    enroll = _run(["devices", "enroll", "neuron-pc"])
    assert enroll.exit_code == 0, enroll.output
    assert _run(["grant", "neuron-pc", "vault://hosts/cybesis-vps/ssh", "--cache-ttl", "1h"]).exit_code == 0
    listed = json.loads(_run(["devices", "list", "--json"]).output)
    assert listed[0]["name"] == "neuron-pc"
    assert listed[0]["grants"][0]["pattern"] == "host/cybesis-vps/ssh_password"
    assert listed[0]["grants"][0]["cache_ttl_s"] == 3600
    assert _run(["grant", "neuron-pc", "federation/*"]).exit_code == 2
    assert _run(["revoke", "neuron-pc"]).exit_code == 0
    assert _run(["devices", "revoke", "neuron-pc"]).exit_code == 0


async def test_the_route_serves_a_device_over_real_http_and_refuses_relays(real_vault, tmp_path, monkeypatch):
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from navig_vault.federation.authority import Authority
    from navig_vault.federation.client import FederationClient

    from navig.gateway.routes import vault_federation as route

    real_vault.put("host/cybesis-vps/ssh_password", json.dumps({"value": SECRET}).encode())
    authority = Authority(real_vault)
    monkeypatch.setattr(route, "_AUTHORITY", authority)

    app = web.Application()
    route.register(app, None)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("")).rstrip("/")
    loop = asyncio.get_running_loop()
    try:
        # Disabled (the default): indistinguishable from a missing route.
        monkeypatch.setattr(route, "_enabled", lambda: False)
        import urllib.request

        def _post(path, headers=None):
            req = urllib.request.Request(base + path, data=b"{}", method="POST", headers=headers or {})
            try:
                return urllib.request.urlopen(req).status  # noqa: S310
            except urllib.error.HTTPError as exc:
                return exc.code

        assert await loop.run_in_executor(None, _post, "/api/vault/v1/status") == 404

        monkeypatch.setattr(route, "_enabled", lambda: True)
        code = authority.create_enrollment("relayed")
        assert await loop.run_in_executor(
            None, lambda: _post_json(base, "/api/vault/v1/enroll", {"code": code}, {"X-Navig-Via": "lighthouse"})
        ) == 403

        device_vault_dir = tmp_path / "device"
        from navig_vault.core import Vault

        fed = FederationClient(Vault(device_vault_dir))
        code = authority.create_enrollment("neuron-pc")
        await loop.run_in_executor(None, fed.join, base, code)
        authority.grant(fed.credentials()["device_id"], "host/cybesis-vps/*")
        value = await loop.run_in_executor(None, fed.resolve, "vault://hosts/cybesis-vps/ssh")
        assert value == SECRET
    finally:
        await server.close()


def _post_json(base: str, path: str, payload: dict, headers: dict) -> int:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        return urllib.request.urlopen(req).status  # noqa: S310
    except urllib.error.HTTPError as exc:
        return exc.code


def test_a_host_ref_missing_locally_resolves_through_the_authority(real_vault, tmp_path, monkeypatch):
    """An enrolled machine resolves a host's *_ref it does not hold — granted, sealed, audited."""
    from navig_vault.core import Vault
    from navig_vault.federation.authority import Authority
    from navig_vault.federation.client import FederationClient

    from navig.core.host_secrets import resolve_host_secret

    outpost = Vault(tmp_path / "outpost")
    outpost.put("host/cybesis-vps/ssh_password", json.dumps({"value": SECRET}).encode())
    authority = Authority(outpost)

    def transport(method, path, body, headers):
        return authority.handle(method, path, headers, body, relayed=False)

    import navig.vault.federation_client as fc

    monkeypatch.setattr(fc, "client", lambda: FederationClient(real_vault, transport=transport))
    fed = fc.client()
    fed.join("http://outpost.test", authority.create_enrollment("neuron-pc"))
    authority.grant(fed.credentials()["device_id"], "host/cybesis-vps/*")

    entry = {"name": "cybesis-vps", "ssh_password_ref": "vault:host/cybesis-vps/ssh_password"}
    assert resolve_host_secret(entry, "ssh_password") == SECRET
    assert real_vault._store.get("host/cybesis-vps/ssh_password") is None, "nothing copied into the device vault"
    outpost._store.close()
