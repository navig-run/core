"""A mutation of an approval journal aborts when the existing file cannot be read.

`journal._load()` / `resume._load()` degrade an unreadable file to `{}` — right for a view,
wrong for a read-modify-write. `record_pending` then saved `{new}` over the whole journal,
dropping every other pending approval in exactly the failure mode the durable journal
exists for. The mutating paths now load with `load_json_for_update`, whose raise the
best-effort handlers turn into "skip the write".
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    # setenv ONLY — see test_approval_is_durable_and_late_answers_are_named.py.
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    yield


def _lock_reads(monkeypatch):
    import navig.core.json_io as json_io

    def _locked(*_a, **_kw):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(json_io, "read_text_retrying", _locked)


def test_record_pending_leaves_an_unreadable_journal_untouched(monkeypatch):
    from navig.approval import journal

    journal.record_pending("old", command="a", channel="telegram", user_id="1", level="CONFIRM")
    path = journal._store_path()
    before = path.read_bytes()

    _lock_reads(monkeypatch)
    journal.record_pending("new", command="b", channel="telegram", user_id="1", level="CONFIRM")
    assert journal.clear_pending("old") is None
    journal.prune_expired(now=10**12)

    assert path.read_bytes() == before, "an unreadable journal was overwritten"


def test_resume_mutations_leave_an_unreadable_store_untouched(monkeypatch):
    from navig.approval import resume

    resume.record("old", session_key="s", channel="telegram", user_id="1", tool_name="t")
    path = resume._store_path()
    before = path.read_bytes()

    _lock_reads(monkeypatch)
    resume.record("new", session_key="s", channel="telegram", user_id="1", tool_name="t")
    assert resume.take("old") is None
    resume.prune_stale(now=10**12, keep_s=0)

    assert path.read_bytes() == before, "an unreadable resume store was overwritten"
