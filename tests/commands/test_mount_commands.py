"""
Tests for navig.commands.mount — drive junction registry.
"""

from __future__ import annotations

import json
import platform
import shutil
import tempfile
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

pytestmark = pytest.mark.integration

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

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

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

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

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

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

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


# ── the registry write and the Windows transient lock ─────────

# `_save_registry` hand-rolled `mkstemp` + `os.replace`, with no retry. An antivirus or
# backup agent holding `drives.json` for a few milliseconds made `os.replace` raise
# `[WinError 5] Access is denied` straight out of `navig mount verify` as a traceback —
# seen in the full core suite on 2026-09-27 (`test_verify_all_dead`, one occurrence in
# 32,393 tests) and reproduced by holding a single read handle on the destination. The
# READ side of this registry had already been given the transient-lock treatment; the
# write half, the one that loses records, had not.


class TestRegistryWriteUnderALock:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_transient_lock_is_retried_not_raised(self, monkeypatch):
        """The write survives a lock that clears within the retry window."""
        _patch_registry(self.tmp, monkeypatch)
        from navig.commands.mount import _load_registry, _save_registry
        from navig.core import yaml_io

        real = yaml_io._atomic_replace
        calls: list[int] = []

        def locked_once(src, dst):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(5, "Access is denied")
            return real(src, dst)

        monkeypatch.setattr(yaml_io, "_atomic_replace", locked_once)

        _save_registry({"drives": {"kept": {"label": "kept"}}})

        assert len(calls) >= 2, "the first PermissionError was not retried"
        assert "kept" in _load_registry()["drives"], "the retried write did not land"

    def test_verify_on_startup_still_answers_when_the_registry_cannot_be_written(
        self, monkeypatch, navig_log_capture
    ):
        """A locked registry must cost the refresh, not the answer.

        `verify_on_startup` recomputes every `alive` flag itself, so the write-back is a
        cache. It is also the one caller of `_save_registry` that is NOT a CLI command, and
        `_save_registry` signals a persistent lock with `typer.Exit` — a CLI verb that must
        not escape into a daemon. Its docstring invites exactly that wiring, so the
        containment is pinned here rather than left to whoever wires it.
        """
        _patch_registry(self.tmp, monkeypatch)
        import navig.commands.mount as m

        m._save_registry(
            {"drives": {"gone": {"label": "gone", "source": "/s", "target": "/t", "alive": True}}}
        )
        monkeypatch.setattr(m, "_junction_alive", lambda t: False)
        monkeypatch.setattr(
            m, "_save_registry", lambda data: (_ for _ in ()).throw(typer.Exit(1))
        )

        dead = m.verify_on_startup()

        assert dead == ["gone"], "the dead list is the answer; a failed refresh must not eat it"
        # Best-effort is not the same as silent. navig_log_capture, not caplog: navig's
        # loggers set propagate=False, so caplog sees nothing and the assert would pass
        # vacuously.
        assert any("could not refresh junction health" in m for m in navig_log_capture), (
            "the discarded write left no trace"
        )

    def test_a_lock_that_persists_reports_it_and_leaves_the_file_intact(self, monkeypatch):
        """Anti-vacuity: a permanent lock must still FAIL — and not destroy what is there.

        Retrying forever, or swallowing the error, would turn a read-modify-write that did
        not happen into a silent success.
        """
        _patch_registry(self.tmp, monkeypatch)
        from navig.commands.mount import _registry_path, _save_registry
        from navig.core import yaml_io

        _save_registry({"drives": {"before": {"label": "before"}}})
        untouched = _registry_path().read_bytes()

        monkeypatch.setattr(
            yaml_io,
            "_atomic_replace",
            lambda src, dst: (_ for _ in ()).throw(PermissionError(5, "Access is denied")),
        )

        with pytest.raises(typer.Exit) as exc:
            _save_registry({"drives": {"after": {"label": "after"}}})

        assert exc.value.exit_code == 1
        assert _registry_path().read_bytes() == untouched, (
            "a write that failed must leave the previous registry byte-for-byte intact"
        )
