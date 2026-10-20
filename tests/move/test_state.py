"""Packing one machine's NAVIG and landing it on another — with real vaults.

Each "machine" is a temp home with its own config dir, NAVIG OS dir and vault. The vault
is the real navig-vault, so the secrets are genuinely decrypted on one side and
re-sealed on the other.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from navig.move import state
from navig.move.bundle import BundleError, BundleWriter
from navig.vault.core import Vault, VaultItemKind

PASS = "ABCDE-FGHJK-MNPQR-STUVW-XYZ23"


def _machine(root, *, deck_key: str | None = "deck-key-old"):
    home = root
    config = home / ".navig"
    os_dir = home / ".navig-os"
    (config / "spaces" / "homelab").mkdir(parents=True)
    (config / "spaces" / "homelab" / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    (config / "spaces" / "homelab" / "node_modules" / "x").mkdir(parents=True)
    (config / "spaces" / "homelab" / "node_modules" / "x" / "big.js").write_text("x", encoding="utf-8")
    (config / "personas").mkdir()
    (config / "personas" / "operator.yaml").write_text("name: operator\n", encoding="utf-8")
    lines = [f"deck:\n  api_key: {deck_key}\n"] if deck_key else []
    lines.append(f"spaces:\n  root: {home / '.navig' / 'spaces'}\n")
    (config / "config.yaml").write_text("".join(lines), encoding="utf-8")
    (config / "spaces.json").write_text(
        json.dumps({"spaces": [{"name": "homelab", "path": str(config / "spaces" / "homelab")}]}), encoding="utf-8"
    )
    for runtime in ("daemon/stop_requested", "logs/debug.log", "gateway.json", "worker.pid"):
        p = config / runtime
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("runtime", encoding="utf-8")
    os_dir.mkdir()
    (os_dir / "config.json").write_text(
        json.dumps({
            "workspaces": [{"name": "My Workspace", "rootPath": str(home / ".navig-os" / "workspaces" / "my-workspace")}],
            "llmConnections": [{"slug": "claude", "name": "Claude"}],
        }),
        encoding="utf-8",
    )
    (os_dir / "credentials.enc").write_bytes(b"CRAFT01\0 machine-bound")
    (os_dir / "brain-link.json").write_text("{}", encoding="utf-8")
    vault = Vault(vault_dir=config / "vault")
    return state.Roots(config=config, data=config / "data", os=os_dir, home=home), vault


@pytest.fixture()
def old(tmp_path):
    roots, vault = _machine(tmp_path / "old")
    vault.put("openai", b"sk-old-secret", kind=VaultItemKind.SECRET, provider="openai", metadata={"profile_id": "work"})
    vault.put("telegram_bot_token", b"123:abc", kind=VaultItemKind.TOKEN)
    return roots, vault


def _new(tmp_path, *, deck_key=None):
    home = tmp_path / "new"
    home.mkdir()
    roots = state.Roots(config=home / ".navig", data=home / ".navig" / "data", os=home / ".navig-os", home=home)
    if deck_key:
        roots.config.mkdir()
        (roots.config / "config.yaml").write_text(f"deck:\n  api_key: {deck_key}\n", encoding="utf-8")
    return roots


def test_a_whole_navig_moves_and_its_secrets_are_resealed(old, tmp_path) -> None:
    roots, vault = old
    out = tmp_path / "move.navigmove"
    manifest = state.pack(out, PASS, roots, vault)
    assert manifest["vault_items"] == 2 and manifest["identity"] is True
    assert manifest["os_connections_to_sign_in"] == ["Claude"]
    assert state.verify(out, PASS)["files"] == manifest["files"]

    new = _new(tmp_path)
    result = state.unpack(out, PASS, new, lambda: Vault(vault_dir=new.config / "vault"))

    assert (new.config / "spaces" / "homelab" / "PLAN.md").read_text(encoding="utf-8") == "# plan\n"
    assert (new.config / "personas" / "operator.yaml").exists()
    # Runtime state of the OLD machine never arrives.
    for runtime in ("daemon", "logs", "gateway.json", "worker.pid", "spaces/homelab/node_modules"):
        assert not (new.config / runtime).exists(), runtime
    assert not (new.os / "credentials.enc").exists()
    assert not (new.os / "brain-link.json").exists()
    # Secrets: decrypted there, re-sealed here under THIS vault's key.
    assert result.vault_restored == 2
    fresh = Vault(vault_dir=new.config / "vault")
    assert fresh.get_bytes("openai") == b"sk-old-secret"
    item = next(i for i in fresh.list() if i.label == "openai")
    assert (item.provider, item.metadata.get("profile_id")) == ("openai", "work")
    assert fresh.get_bytes("telegram_bot_token") == b"123:abc"


def test_paths_under_the_old_home_point_at_the_new_one(old, tmp_path) -> None:
    roots, vault = old
    out = tmp_path / "move.navigmove"
    state.pack(out, PASS, roots, vault)
    new = _new(tmp_path)
    result = state.unpack(out, PASS, new, lambda: Vault(vault_dir=new.config / "vault"))
    assert set(result.paths_rewritten) == {"navig/config.yaml", "navig/spaces.json", "navig-os/config.json"}
    spaces = json.loads((new.config / "spaces.json").read_text(encoding="utf-8"))
    assert spaces["spaces"][0]["path"] == str(new.home / ".navig" / "spaces" / "homelab")
    ws = json.loads((new.os / "config.json").read_text(encoding="utf-8"))["workspaces"][0]["rootPath"]
    assert ws == str(new.home / ".navig-os" / "workspaces" / "my-workspace")
    assert str(roots.home) not in (new.config / "config.yaml").read_text(encoding="utf-8")


def test_a_machine_with_its_own_identity_is_refused_and_untouched(old, tmp_path) -> None:
    roots, vault = old
    out = tmp_path / "move.navigmove"
    state.pack(out, PASS, roots, vault)
    new = _new(tmp_path, deck_key="deck-key-of-the-new-machine")
    with pytest.raises(state.MoveError, match="already has a NAVIG identity"):
        state.unpack(out, PASS, new, lambda: Vault(vault_dir=new.config / "vault"))
    assert "deck-key-of-the-new-machine" in (new.config / "config.yaml").read_text(encoding="utf-8")
    assert not list(new.home.glob(".navig-move-staging-*"))


def test_replace_keeps_what_was_there_as_a_backup(old, tmp_path) -> None:
    roots, vault = old
    out = tmp_path / "move.navigmove"
    state.pack(out, PASS, roots, vault)
    new = _new(tmp_path, deck_key="deck-key-of-the-new-machine")
    result = state.unpack(out, PASS, new, lambda: Vault(vault_dir=new.config / "vault"), replace=True)
    assert len(result.backups) == 1
    assert "deck-key-of-the-new-machine" in (result.backups[0] / "config.yaml").read_text(encoding="utf-8")
    assert "deck-key-old" in (new.config / "config.yaml").read_text(encoding="utf-8")


def test_a_wrong_passphrase_changes_nothing(old, tmp_path) -> None:
    roots, vault = old
    out = tmp_path / "move.navigmove"
    state.pack(out, PASS, roots, vault)
    new = _new(tmp_path)
    (new.home / ".navig-os").mkdir()
    (new.home / ".navig-os" / "config.json").write_text('{"mine": true}', encoding="utf-8")
    with pytest.raises(BundleError, match="Wrong passphrase"):
        state.unpack(out, "AAAAA-AAAAA", new, lambda: Vault(vault_dir=new.config / "vault"))
    assert (new.home / ".navig-os" / "config.json").read_text(encoding="utf-8") == '{"mine": true}'
    assert not list(new.home.glob("*.before-move-*"))


def test_an_unreadable_secret_stops_the_move_before_anything_is_written(old, tmp_path) -> None:
    roots, vault = old

    class Locked:
        def list(self):
            return vault.list()

        def get_bytes(self, label):
            raise RuntimeError("vault is locked")

    out = tmp_path / "move.navigmove"
    with pytest.raises(state.MoveError, match="run navig move out in a terminal"):
        state.pack(out, PASS, roots, Locked())
    assert not out.exists()


def _evil_bundle(path, name: str) -> None:
    """A bundle that is valid in every way (manifest included) except one entry, so a
    refusal can only come from the entry check — not from, say, a missing manifest."""
    writer = BundleWriter(path, PASS, {})
    with tarfile.open(fileobj=writer, mode="w|gz") as tar:
        data = b"escaped"
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        manifest = json.dumps({"format_version": state.FORMAT_VERSION, "source": {}}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    writer.close()


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("../escape.txt", "unsafe path"),
        ("/etc/escape.txt", "unsafe path"),
        ("navig/../../escape.txt", "unsafe path"),
        ("somewhere-else/escape.txt", "unexpected entry"),
    ],
)
def test_a_bundle_cannot_write_outside_its_dirs(tmp_path, name, why) -> None:
    out = tmp_path / "evil.navigmove"
    _evil_bundle(out, name)
    new = _new(tmp_path)
    with pytest.raises(state.MoveError, match=why):
        state.unpack(out, PASS, new, lambda: Vault(vault_dir=new.config / "vault"))
    assert not [p for p in tmp_path.rglob("escape.txt")], "an entry escaped before the refusal"
    assert not new.config.exists()


def test_a_link_inside_a_bundle_is_refused(tmp_path) -> None:
    out = tmp_path / "evil.navigmove"
    writer = BundleWriter(out, PASS, {})
    with tarfile.open(fileobj=writer, mode="w|gz") as tar:
        info = tarfile.TarInfo("navig/config.yaml")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    writer.close()
    new = _new(tmp_path)
    with pytest.raises(state.MoveError, match="unsupported entry"):
        state.unpack(out, PASS, new, lambda: Vault(vault_dir=new.config / "vault"))


def test_windows_paths_follow_the_new_home_case_insensitively(tmp_path) -> None:
    new_home = tmp_path / "home"
    assert state._rewrite_value(r"C:\Users\Sub\.navig\spaces\x", r"c:\users\sub", new_home) == str(
        new_home / ".navig" / "spaces" / "x"
    )
    assert state._rewrite_value("/home/sub/.navig", "/home/sub", new_home) == str(new_home / ".navig")
    # Not under the old home, or merely sharing a prefix: untouched.
    assert state._rewrite_value("/home/adam/x", "/home/ada", new_home) == "/home/adam/x"
    assert state._rewrite_value("https://example.com", "/home/sub", new_home) == "https://example.com"
