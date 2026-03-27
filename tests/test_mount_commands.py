"""
Tests for navig.commands.mount — drive junction registry.
"""

from __future__ import annotations

import json
import platform
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

# ── Helpers ───────────────────────────────────────────────────


def _patch_registry(tmp: Path, monkeypatch):
    """Redirect registry file to a temp directory."""
    import navig.commands.mount as m

    monkeypatch.setattr(m, "_registry_path", lambda: tmp / "registry" / "drives.json")
    monkeypatch.setattr(m, "_scripts_dir", lambda: tmp / "scripts")


# ── _load / _save registry ───────────────────────────────────


class TestRegistryIO:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_load_empty_registry(self, monkeypatch):
        _patch_registry(self.tmp, monkeypatch)
        from navig.commands.mount import _load_registry

        data = _load_registry()
        assert data == {"drives": {}}

    def test_save_and_load(self, monkeypatch):
        _patch_registry(self.tmp, monkeypatch)
        from navig.commands.mount import _load_registry, _save_registry

        _save_registry({"drives": {"test": {"label": "test"}}})
        loaded = _load_registry()
        assert "test" in loaded["drives"]

    def test_load_corrupt_file_returns_empty(self, monkeypatch):
        _patch_registry(self.tmp, monkeypatch)
        from navig.commands.mount import _load_registry, _registry_path

        path = _registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("INVALID JSON{{}", encoding="utf-8")
        data = _load_registry()
        assert data == {"drives": {}}


# ── junction helpers ──────────────────────────────────────────


class TestJunctionHelpers:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_junction_alive_existing_dir(self):
        from navig.commands.mount import _junction_alive

        d = self.tmp / "mydir"
        d.mkdir()
        assert _junction_alive(d) is True

    def test_junction_alive_missing(self):
        from navig.commands.mount import _junction_alive

        assert _junction_alive(self.tmp / "nonexistent") is False

    @pytest.mark.skipif(
        platform.system() == "Windows", reason="Symlinks may need elevation on Windows"
    )
    def test_create_and_remove_junction_posix(self):
        from navig.commands.mount import _create_junction, _remove_junction

        source = self.tmp / "source"
        source.mkdir()
        target = self.tmp / "link"

        err = _create_junction(source, target)
        assert err is None
        assert target.exists()

        err = _remove_junction(target)
        assert err is None
        assert not target.exists()

    def test_create_junction_missing_source(self):
        from navig.commands.mount import _create_junction

        source = self.tmp / "nonexistent"
        target = self.tmp / "link"
        err = _create_junction(source, target)
        assert err is not None
        assert "Source does not exist" in err


# ── CLI commands ──────────────────────────────────────────────


class TestMountCLI:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runner = CliRunner()

    def _app(self):
        from navig.commands.mount import mount_app

        return mount_app

    def _patch(self, monkeypatch):
        _patch_registry(self.tmp, monkeypatch)

    def test_list_empty(self, monkeypatch):
        self._patch(monkeypatch)
        result = self.runner.invoke(self._app(), ["list"])
        assert result.exit_code == 0
        assert "No drive junctions" in result.output

    def test_add_and_list(self, monkeypatch):
        self._patch(monkeypatch)
        source = self.tmp / "src"
        source.mkdir()
        target = self.tmp / "link"

        import navig.commands.mount as m

        monkeypatch.setattr(m, "_create_junction", lambda s, t: None)

        result = self.runner.invoke(
            self._app(),
            ["add", "mylink", str(source), str(target)],
        )
        assert result.exit_code == 0

        result = self.runner.invoke(self._app(), ["list"])
        assert "mylink" in result.output

    def test_add_already_registered(self, monkeypatch):
        self._patch(monkeypatch)
        import navig.commands.mount as m

        monkeypatch.setattr(m, "_create_junction", lambda s, t: None)

        source = self.tmp / "src2"
        source.mkdir()
        target = self.tmp / "link2"

        self.runner.invoke(self._app(), ["add", "dup", str(source), str(target)])
        result = self.runner.invoke(self._app(), ["add", "dup", str(source), str(target)])
        assert result.exit_code != 0
        assert "already registered" in result.output

    def test_add_no_create_flag(self, monkeypatch):
        self._patch(monkeypatch)
        source = self.tmp / "src3"
        source.mkdir()
        target = self.tmp / "link3"

        result = self.runner.invoke(
            self._app(),
            ["add", "nolink", str(source), str(target), "--no-create"],
        )
        assert result.exit_code == 0
        data = json.loads((self.tmp / "registry" / "drives.json").read_text())
        assert "nolink" in data["drives"]

    def test_verify_all_dead(self, monkeypatch):
        self._patch(monkeypatch)
        import navig.commands.mount as m

        monkeypatch.setattr(m, "_create_junction", lambda s, t: None)

        source = self.tmp / "s"
        source.mkdir()
        target = self.tmp / "dead_link"

        self.runner.invoke(self._app(), ["add", "dd", str(source), str(target), "--no-create"])
        result = self.runner.invoke(self._app(), ["verify"])
        assert result.exit_code == 0
        assert "dead" in result.output.lower() or "✗" in result.output

    def test_verify_json_output(self, monkeypatch):
        self._patch(monkeypatch)
        result = self.runner.invoke(self._app(), ["verify", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_remove_nonexistent_label(self, monkeypatch):
        self._patch(monkeypatch)
        result = self.runner.invoke(self._app(), ["remove", "ghost", "--yes"])
        assert result.exit_code != 0

    def test_remove_existing(self, monkeypatch):
        self._patch(monkeypatch)
        source = self.tmp / "rsrc"
        source.mkdir()
        target = self.tmp / "rlink"

        import navig.commands.mount as m

        monkeypatch.setattr(m, "_create_junction", lambda s, t: None)
        monkeypatch.setattr(m, "_remove_junction", lambda t: None)
        monkeypatch.setattr(m, "_junction_alive", lambda t: False)

        self.runner.invoke(self._app(), ["add", "rem_test", str(source), str(target)])
        result = self.runner.invoke(self._app(), ["remove", "rem_test", "--yes"])
        assert result.exit_code == 0
        data = json.loads((self.tmp / "registry" / "drives.json").read_text())
        assert "rem_test" not in data["drives"]

    def test_sync_writes_ps1(self, monkeypatch):
        self._patch(monkeypatch)
        import navig.commands.mount as m

        monkeypatch.setattr(m, "_create_junction", lambda s, t: None)
        monkeypatch.setattr(m, "_junction_alive", lambda t: False)

        source = self.tmp / "ss"
        source.mkdir()
        target = self.tmp / "tt"
        self.runner.invoke(
            self._app(), ["add", "sync_test", str(source), str(target), "--no-create"]
        )

        result = self.runner.invoke(self._app(), ["sync"])
        assert result.exit_code == 0
        script = self.tmp / "scripts" / "mount-drive.ps1"
        assert script.is_file()
        content = script.read_text()
        assert "sync_test" in content
        assert "mklink" in content or "Auto-generated" in content

    def test_sync_dry_run(self, monkeypatch):
        self._patch(monkeypatch)
        result = self.runner.invoke(self._app(), ["sync", "--dry-run"])
        assert result.exit_code == 0
        assert "Would write" in result.output or "dry" in result.output.lower()

    def test_list_json_output(self, monkeypatch):
        self._patch(monkeypatch)
        import navig.commands.mount as m

        monkeypatch.setattr(m, "_create_junction", lambda s, t: None)
        source = self.tmp / "j"
        source.mkdir()
        self.runner.invoke(self._app(), ["add", "json_test", str(source), "--no-create"])
        result = self.runner.invoke(self._app(), ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert any(d["label"] == "json_test" for d in data)


# ── PS1 generator ─────────────────────────────────────────────


class TestPS1Generator:
    def test_empty_drives(self):
        from navig.commands.mount import _generate_ps1

        script = _generate_ps1({})
        assert "No junctions registered" in script

    def test_single_drive(self):
        from navig.commands.mount import _generate_ps1

        drives = {
            "mylink": {
                "label": "mylink",
                "source": "C:\\Users\\test\\source",
                "target": "C:\\mnt\\mylink",
            }
        }
        script = _generate_ps1(drives)
        assert "mylink" in script
        assert "mklink" in script
        assert "mnt" in script

    def test_multiple_drives(self):
        from navig.commands.mount import _generate_ps1

        drives = {
            "a": {"label": "a", "source": "/src/a", "target": "/mnt/a"},
            "b": {"label": "b", "source": "/src/b", "target": "/mnt/b"},
        }
        script = _generate_ps1(drives)
        assert "--- a ---" in script
        assert "--- b ---" in script


# ── verify_on_startup ─────────────────────────────────────────


class TestVerifyOnStartup:
    def test_returns_empty_list_when_no_registry(self, monkeypatch, tmp_path):
        _patch_registry(tmp_path, monkeypatch)
        from navig.commands.mount import verify_on_startup

        result = verify_on_startup()
        assert result == []

    def test_returns_dead_labels(self, monkeypatch, tmp_path):
        _patch_registry(tmp_path, monkeypatch)
        import navig.commands.mount as m

        monkeypatch.setattr(m, "_junction_alive", lambda t: False)

        m._save_registry(
            {
                "drives": {
                    "deadlink": {
                        "label": "deadlink",
                        "source": "/src",
                        "target": "/mnt/deadlink",
                        "alive": True,
                    }
                }
            }
        )

        from navig.commands.mount import verify_on_startup

        dead = verify_on_startup()
        assert "deadlink" in dead
