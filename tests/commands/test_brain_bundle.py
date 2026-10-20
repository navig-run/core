"""`navig backup export|import --brain` — moving a whole brain between machines.

A config backup travels anywhere; a brain also carries the state a brain RUNS on
(databases, memory, sessions, license). Two properties matter more than anything
else here:

- the vault is NOT carried (its key is bound to the source machine — carrying it
  would ship a folder that only looks like a vault), and neither are logs/cache;
- round-trip: export then import returns byte-identical state, and the unsafe
  combinations (no encryption, JSON format, daemon still running) are refused.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest
import typer

from navig.commands import config_backup as cb

# ── a config manager that points at a temp tree (never the real ~/.navig) ─────


class FakeConfigManager:
    """Just enough ConfigManager for export/import: a directory and its yaml files."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir

    def get_config_directories(self) -> list[Path]:
        return [self.config_dir]

    # console_helper.confirm_operation reads these through the same patched
    # get_config_manager, so the fake must answer them too. "interactive" (not
    # "auto") keeps the test honest: it is the --yes flag that bypasses the
    # prompt, not a permissive execution mode.
    def get_execution_mode(self) -> str:
        return "interactive"

    def get_confirmation_level(self) -> str:
        return "standard"

    def list_hosts(self) -> list[str]:
        d = self.config_dir / "hosts"
        return [p.stem for p in sorted(d.glob("*.yaml"))] if d.is_dir() else []

    def list_apps(self, host: str) -> list[str]:
        d = self.config_dir / "apps" / host
        return [p.stem for p in sorted(d.glob("*.yaml"))] if d.is_dir() else []

    def host_exists(self, name: str) -> bool:
        return (self.config_dir / "hosts" / f"{name}.yaml").is_file()

    def app_exists(self, host: str, app: str) -> bool:
        return (self.config_dir / "apps" / host / f"{app}.yaml").is_file()

    def load_host_config(self, name: str) -> dict:
        return {}

    def save_host_config(self, name: str, cfg: dict) -> None:
        raise AssertionError("no hosts in these fixtures")

    def load_app_config(self, host: str, app: str) -> dict:
        return {}

    def save_app_config(self, host: str, app: str, cfg: dict) -> None:
        raise AssertionError("no apps in these fixtures")


def _write_brain(config_dir: Path, data_dir: Path) -> dict[str, bytes]:
    """Populate a source install; returns the bytes each moved file must keep."""
    files: dict[str, bytes] = {}

    def put(rel: str, body: bytes) -> None:
        p = config_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        files[rel] = body

    put("config.yaml", b"deck:\n  api_key: keep-me\n")
    put("license.key", b"LIC-123")
    put("genesis.json", b'{"name": "neuron"}')
    put("daemon/config.json", b'{"gateway": true, "telegram_bot": true}')
    put("memory/first.md", b"a memory worth keeping")
    put("sessions/s1.json", b'{"id": "s1"}')
    # The state whose loss is silent: automation, identity, approvals, and the
    # env vars that gate who may talk to the bot.
    put(".env", b"ALLOWED_TELEGRAM_USERS=12345\n")
    put("scheduler/cron_jobs.json", b'{"jobs": []}')
    put("workspace/SOUL.md", b"# the agent's continuity")
    put("approvals/a1.json", b'{"decision": "granted"}')

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "brain.db").write_bytes(b"SQLite format 3\x00fake")
    (data_dir / "brain.db-wal").write_bytes(b"WAL")
    files["data/brain.db"] = b"SQLite format 3\x00fake"
    files["data/brain.db-wal"] = b"WAL"

    # Must never travel with the bundle.
    (config_dir / "vault").mkdir(parents=True, exist_ok=True)
    (config_dir / "vault" / "vault.db").write_bytes(b"MACHINE-BOUND")
    (config_dir / "logs").mkdir(parents=True, exist_ok=True)
    (config_dir / "logs" / "daemon.log").write_bytes(b"log line")
    return files


@pytest.fixture
def source_tree(tmp_path: Path) -> tuple[Path, Path, dict[str, bytes]]:
    config_dir = tmp_path / "src-config"
    data_dir = tmp_path / "src-data"
    config_dir.mkdir(parents=True)
    files = _write_brain(config_dir, data_dir)
    return config_dir, data_dir, files


def _stage(config_dir: Path, data_dir: Path, dest: Path) -> Path:
    """Stage into a directory shaped like the archive's `navig-config/` root."""
    dest.mkdir(parents=True, exist_ok=True)
    cb._stage_brain(dest, config_dir, data_dir)
    return dest


# ── staging ──────────────────────────────────────────────────────────────────


def test_stage_brain_takes_state_and_leaves_vault_and_logs(source_tree, tmp_path: Path):
    config_dir, data_dir, _files = source_tree
    dest = tmp_path / "staging" / "root"
    staged = cb._stage_brain(dest, config_dir, data_dir)

    brain = dest / "brain"
    assert (brain / "license.key").is_file()
    assert (brain / "daemon" / "config.json").is_file()
    assert (brain / "memory" / "first.md").is_file()
    assert (brain / "data" / "brain.db").is_file()
    assert (brain / "data" / "brain.db-wal").is_file()
    # Automation, identity, approvals and the bot's allow-list travel too — the
    # brain without them boots right and does nothing.
    assert (brain / ".env").read_bytes() == b"ALLOWED_TELEGRAM_USERS=12345\n"
    assert (brain / "scheduler" / "cron_jobs.json").is_file()
    assert (brain / "workspace" / "SOUL.md").is_file()
    assert (brain / "approvals" / "a1.json").is_file()

    # The vault is machine-bound, logs are regenerated: neither travels.
    assert not (brain / "vault").exists()
    assert not (brain / "logs").exists()
    assert staged["files"] == 4  # license + genesis + .env + daemon/config.json
    assert staged["databases"] == 2
    assert staged["dirs"] == 5
    assert staged["dir_files"] == {
        "memory": 1,
        "sessions": 1,
        "scheduler": 1,
        "workspace": 1,
        "approvals": 1,
    }


def test_brain_round_trip_is_byte_identical(source_tree, tmp_path: Path):
    config_dir, data_dir, files = source_tree
    staging = tmp_path / "staging"
    _stage(config_dir, data_dir, staging)

    target_config = tmp_path / "dst-config"
    target_data = tmp_path / "dst-data"
    restored = cb._restore_brain(staging, target_config, target_data)

    # Everything the brain scope stages must come back byte-identical — except
    # config.yaml, which is the archive's job (test_import_restores_config.py).
    expected = {k: v for k, v in files.items() if k != "config.yaml"}
    got: dict[str, bytes] = {}
    for rel in expected:
        p = (target_config / rel) if not rel.startswith("data/") else (target_data / rel[5:])
        assert p.is_file(), f"missing after restore: {rel}"
        got[rel] = p.read_bytes()
    assert got == expected
    assert restored["databases"] == 2 and restored["files"] == 4
    # config.yaml is the config scope's job, not the brain scope's.
    assert not (target_config / "config.yaml").exists()


# ── the archive ──────────────────────────────────────────────────────────────


def test_archive_records_its_scope_and_carries_the_brain(
    source_tree, tmp_path: Path, monkeypatch
):
    config_dir, data_dir, _files = source_tree
    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager(config_dir))
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: data_dir)

    out = tmp_path / "brain.tar.gz"
    summary = cb._create_archive(out, include_secrets=True, brain=True)
    assert summary["brain"]["databases"] == 2

    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
        manifest = json.load(tar.extractfile("navig-config/manifest.json"))  # noqa: S307

    assert manifest["scope"] == "brain"
    assert manifest["brain"]["databases"] == 2
    assert "navig-config/brain/data/brain.db" in names
    assert "navig-config/brain/license.key" in names
    assert not any("vault" in n for n in names)

    # The plain config scope stays exactly what it was: no brain payload.
    out2 = tmp_path / "config.tar.gz"
    cb._create_archive(out2, include_secrets=True, brain=False)
    with tarfile.open(out2, "r:gz") as tar:
        names2 = tar.getnames()
        manifest2 = json.load(tar.extractfile("navig-config/manifest.json"))  # noqa: S307
    assert manifest2["scope"] == "config"
    assert not any("/brain/" in n for n in names2)


def test_export_brain_refuses_json_format(source_tree, tmp_path: Path, monkeypatch):
    config_dir, data_dir, _files = source_tree
    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager(config_dir))
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: data_dir)

    with pytest.raises(typer.Exit) as exc:
        cb.export_config(
            {"scope": "brain", "format": "json", "encrypt": True, "password": "pw",
             "output": tmp_path / "x.json", "yes": True, "json": True}
        )
    assert exc.value.exit_code == 2


def test_export_brain_requires_encryption(source_tree, tmp_path: Path, monkeypatch):
    config_dir, data_dir, _files = source_tree
    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager(config_dir))
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: data_dir)

    with pytest.raises(typer.Exit) as exc:
        cb.export_config(
            {"scope": "brain", "format": "archive", "encrypt": False,
             "output": tmp_path / "x.tar.gz", "yes": True, "json": True}
        )
    assert exc.value.exit_code == 2


def test_import_brain_refuses_while_the_daemon_runs(tmp_path: Path, monkeypatch):
    from navig.daemon.supervisor import NavigDaemon

    fake = tmp_path / "brain.tar.gz"
    fake.write_bytes(b"not really a tarball")
    monkeypatch.setattr(NavigDaemon, "is_running", lambda *a, **k: True)

    with pytest.raises(typer.Exit) as exc:
        cb.import_config({"file": fake, "scope": "brain", "merge": True, "yes": True, "json": True})
    assert exc.value.exit_code == 1


def test_export_brain_refuses_while_the_daemon_runs(
    source_tree, tmp_path: Path, monkeypatch
):
    """The export side of the same rule: no bundle taken under a live writer.

    A copy taken mid-write looks exactly like a good one and fails somewhere far
    away — on the machine just migrated to.
    """
    from navig.daemon.supervisor import NavigDaemon

    config_dir, data_dir, _files = source_tree
    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager(config_dir))
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: data_dir)
    monkeypatch.setattr(NavigDaemon, "is_running", lambda *a, **k: True)

    with pytest.raises(typer.Exit) as exc:
        cb.export_config(
            {"scope": "brain", "format": "archive", "encrypt": True, "password": "pw",
             "output": tmp_path / "brain.tar.gz", "yes": True, "json": True}
        )
    assert exc.value.exit_code == 1
    # It refused before writing anything.
    assert not (tmp_path / "brain.tar.gz").exists()


def test_brain_export_then_import_round_trip(
    source_tree, tmp_path: Path, monkeypatch
):
    """The whole path: encrypted export on A, decrypt+restore on B, bytes equal."""
    config_dir, data_dir, files = source_tree
    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager(config_dir))
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: data_dir)

    out = tmp_path / "brain.tar.gz"
    cb.export_config(
        {"scope": "brain", "format": "archive", "encrypt": True, "password": "pw-1",
         "output": out, "yes": True, "json": True}
    )
    encrypted = out.with_suffix(out.suffix + ".enc")
    assert encrypted.is_file(), "export --brain --encrypt must write <file>.enc"

    # Now become the target machine: a fresh config dir and data dir.
    target_config = tmp_path / "target-config"
    target_config.mkdir()
    target_data = tmp_path / "target-data"
    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager(target_config))
    monkeypatch.setattr("navig.platform.paths.data_dir", lambda: target_data)

    from navig.daemon.supervisor import NavigDaemon

    monkeypatch.setattr(NavigDaemon, "is_running", lambda *a, **k: False)

    cb.import_config(
        {"file": encrypted, "scope": "brain", "password": "pw-1", "merge": False,
         "yes": True, "json": True}
    )

    assert (target_data / "brain.db").read_bytes() == files["data/brain.db"]
    assert (target_config / "license.key").read_bytes() == files["license.key"]
    assert (target_config / "memory" / "first.md").read_bytes() == files["memory/first.md"]
    assert (target_config / "daemon" / "config.json").read_bytes() == files["daemon/config.json"]
    # The decrypted plaintext copy (written beside the .enc) is cleaned up — what
    # remains is the encrypted bundle, not a plaintext twin of your brain.
    assert encrypted.is_file()
    assert not out.exists()
