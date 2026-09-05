"""`navig config export` must not overstate what it exported.

Three separate ways it did, all fixed together because they compound: the summary
counted the config *directory* rather than the exported data, per-item read failures
were warnings that scrolled off above the final green ✓, and a file that could not be
redacted was left in the archive with its plaintext secrets.

The last one is the sharp end. A single YAML typo in a hand-edited host file was
enough — `safe_load` raises, the old `except Exception: pass  # skip files that can't
be processed` swallowed it, and an export the user asked to be redacted shipped a real
password. Verified against the real function before the fix:

    _redact_secrets_in_dir(dir with a typo'd host file)
      before -> typo.yaml still present, 'SUPERSECRET' intact
      after  -> typo.yaml dropped, reported to the caller
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from navig.commands.config_backup import _collect_configs, _redact_secrets_in_dir


@pytest.fixture
def staging() -> Path:
    return Path(tempfile.mkdtemp())


def test_a_file_that_cannot_be_parsed_is_dropped_not_shipped(staging: Path) -> None:
    """The original bug: one unclosed bracket leaked a password."""
    (staging / "typo.yaml").write_text(
        "database:\n  password: SUPERSECRET\n  hosts: [unclosed\n", encoding="utf-8"
    )

    dropped = _redact_secrets_in_dir(staging)

    # Behaviour first, return value second: on the old code the leak is the thing that
    # should be named in the failure output, not the changed signature.
    assert not (staging / "typo.yaml").exists(), (
        "the unredactable file is still in the staging dir, so it would be tarred "
        "into an archive the user asked to be redacted"
    )
    assert dropped == ["typo.yaml"]


def test_a_normal_file_is_still_redacted_in_place(staging: Path) -> None:
    """The partner assertion. A 'fix' that dropped every file would pass the test
    above and destroy the feature."""
    (staging / "host.yaml").write_text(
        "database:\n  password: hunter2\n  port: 3306\n", encoding="utf-8"
    )

    _redact_secrets_in_dir(staging)

    # Deliberately asserts only the OBSERVABLE result, not the return value: this is
    # the partner that proves the feature still works, so it must hold both before and
    # after the change. Coupling it to the new return type would make it fail on the
    # old code for a reason that has nothing to do with the behaviour it guards.
    body = (staging / "host.yaml").read_text(encoding="utf-8")
    assert (staging / "host.yaml").exists()
    assert "hunter2" not in body
    assert "3306" in body, "redaction must not eat the non-sensitive config"


def test_an_empty_file_is_provably_clean_and_kept(staging: Path) -> None:
    (staging / "empty.yaml").write_text("", encoding="utf-8")
    _redact_secrets_in_dir(staging)
    assert (staging / "empty.yaml").exists()  # observable result only — see above


def test_a_non_mapping_document_cannot_be_proven_clean(staging: Path) -> None:
    """`redact_dict` only understands mappings. A top-level list parsed fine but was
    skipped silently, which is the same leak through a different door."""
    (staging / "list.yaml").write_text("- password: SUPERSECRET\n", encoding="utf-8")

    dropped = _redact_secrets_in_dir(staging)

    assert not (staging / "list.yaml").exists()
    assert dropped == ["list.yaml"]


def test_nested_files_are_reported_with_their_relative_path(staging: Path) -> None:
    """The archive stages hosts/ and apps/ as directories, so a bare filename would
    not tell the user which host was dropped."""
    (staging / "hosts").mkdir()
    (staging / "hosts" / "prod.yaml").write_text("bad: [\n", encoding="utf-8")

    assert _redact_secrets_in_dir(staging) == ["hosts/prod.yaml"]


def test_an_undeletable_file_fails_the_export_rather_than_leaking(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the file cannot even be removed from the staging copy, building the archive
    must fail. Shipping it would leak the secret, which is the one outcome that is
    both invisible and unrecoverable."""
    (staging / "stuck.yaml").write_text("bad: [\n", encoding="utf-8")

    def _no_unlink(self: Path, *a: object, **k: object) -> None:
        raise OSError("file is locked by another process")

    monkeypatch.setattr(Path, "unlink", _no_unlink)

    with pytest.raises(RuntimeError, match="Refusing to build a redacted export"):
        _redact_secrets_in_dir(staging)


class _FakeCM:
    """A config manager whose second host always fails to load."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self.global_config = {"openrouter_api_key": "sk-real"}
        # `_get_backup_dir()` mkdirs `<config_dir>/exports` when no --output is given;
        # point it at a tmp path so a test can never touch the real ~/.navig.
        self.config_dir = config_dir or Path(tempfile.mkdtemp())

    def list_hosts(self) -> list[str]:
        return ["good", "broken"]

    def list_apps(self, host: str) -> list[str]:
        return []

    def load_host_config(self, host: str) -> dict:
        if host == "broken":
            raise OSError("permission denied")
        return {"host": "1.2.3.4", "database": {"password": "p"}}


def test_collect_configs_reports_which_hosts_it_could_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure used to exist only as a warning printed mid-run; the summary was
    computed from `list_hosts()` afterwards and so reported every host as exported."""
    import navig.config as config_mod

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: _FakeCM())

    failures: list[str] = []
    data = _collect_configs(include_global=True, failures=failures)

    assert list(data["hosts"]) == ["good"], "the broken host must not appear as exported"
    assert len(failures) == 1
    assert "broken" in failures[0] and "permission denied" in failures[0]


def test_collect_configs_still_works_without_a_failures_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parameter is optional and additive — existing callers (and tests) pass
    nothing and must be unaffected."""
    import navig.config as config_mod

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: _FakeCM())

    data = _collect_configs()
    assert list(data["hosts"]) == ["good"]


def test_the_global_api_key_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the other half of the same promise: an export without --include-secrets
    must not carry the global key either."""
    import navig.config as config_mod

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: _FakeCM())

    data = _collect_configs(include_global=True)
    assert data["global_config"]["openrouter_api_key"] == "[REDACTED]"


# ── the summary itself ──────────────────────────────────────────────────────


def _run_export(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cm: object) -> dict:
    """Drive the real `export_config` in --json mode and return the parsed payload."""
    import json as _json

    import navig.config as config_mod
    from navig.commands import config_backup

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: cm)

    printed: list[str] = []
    monkeypatch.setattr(config_backup.ch, "raw_print", printed.append)

    config_backup.export_config(
        {
            "output": str(tmp_path / "out.json"),
            "format": "json",
            "json": True,
            "yes": True,
        }
    )
    assert printed, "export_config printed no --json payload"
    return _json.loads(printed[-1])


def test_the_json_summary_counts_what_was_exported_not_what_is_on_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The core lie: one of two hosts failed to load, and the summary reported both
    because it called `list_hosts()` again instead of counting the collected data."""
    payload = _run_export(monkeypatch, tmp_path, _FakeCM())

    assert payload["hosts"] == 1, "must count the host actually written"
    assert payload["hosts_found"] == 2, "and still say how many there were"
    assert payload["complete"] is False
    assert any("broken" in f for f in payload["failures"])


def test_a_partial_export_still_reports_success_and_the_file_is_usable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Partial is not failure — the file is real and the gaps are enumerated. Only a
    zero-host export is a failure (below). Same rule as backup_system_config."""
    payload = _run_export(monkeypatch, tmp_path, _FakeCM())

    assert payload["success"] is True
    assert (tmp_path / "out.json").exists()


class _AllBrokenCM(_FakeCM):
    def list_hosts(self) -> list[str]:
        return ["a", "b"]

    def load_host_config(self, host: str) -> dict:
        raise OSError("permission denied")


def test_an_export_that_wrote_no_host_at_all_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """There were hosts to write and none was written — that is not a success a
    script could act on, so it exits non-zero rather than printing a green tick."""
    import typer

    with pytest.raises(typer.Exit) as excinfo:
        _run_export(monkeypatch, tmp_path, _AllBrokenCM())
    assert excinfo.value.exit_code == 1


class _EmptyCM(_FakeCM):
    def list_hosts(self) -> list[str]:
        return []


def test_a_fresh_install_with_no_hosts_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Zero exported out of zero found is complete, not broken — the failure rule
    must key on 'had hosts and wrote none', never on a bare zero."""
    payload = _run_export(monkeypatch, tmp_path, _EmptyCM())

    assert payload["success"] is True
    assert payload["complete"] is True
    assert payload["hosts"] == 0 and payload["hosts_found"] == 0
