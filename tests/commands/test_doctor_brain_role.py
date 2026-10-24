"""An install cannot be a client of one brain and a brain itself.

Why this test exists
--------------------
On 2026-10-22 NEURON held `~/.navig-os/brain-link.json` pointing at `outpost-server`
(device `83d011d1…`, redeemed 2026-10-20) **and** ran a local NAVIG gateway on 8789.
Both machines answered `/health` with `"canonical": true`. Nothing in `navig doctor`
noticed, because every row described one side of the contradiction on its own:
the port row said "bound", the daemon row said "not running", the identity row said
"linked".

The contradiction is computable without any network call: a brain-link file means
"my brain lives elsewhere"; a local daemon means "my brain lives here". Both at once is
the defect. This row states it once, in one place.
"""

from __future__ import annotations

import json

import pytest

from navig.commands import doctor as doc

pytestmark = pytest.mark.unit

LINK = {"brainName": "outpost-server", "deviceId": "83d011d16b16", "url": "https://example/os/abc"}


def test_client_with_a_local_brain_running_is_RED():
    row = doc._classify_brain_role(LINK, local_running=True)
    assert row[1] is False, "client + local brain is the two-brain state"
    assert row[0] == doc._ERR
    assert "outpost-server" in row.detail
    assert "two" in row.detail.lower() or "second" in row.detail.lower()


def test_client_without_a_local_brain_is_green_and_names_the_brain():
    row = doc._classify_brain_role(LINK, local_running=False)
    assert row[1] is True, row[2]
    assert "outpost-server" in row.detail


def test_standalone_brain_without_a_link_is_green():
    row = doc._classify_brain_role(None, local_running=True)
    assert row[1] is True, row[2]


def test_nothing_linked_and_nothing_running_says_so():
    row = doc._classify_brain_role(None, local_running=False)
    assert row[1] is True
    assert "no" in row.detail.lower()


def test_corrupt_link_is_not_read_as_absent():
    """A file that exists but cannot be parsed is 'cannot verify', never 'no link'."""
    row = doc._classify_brain_role({"__corrupt__": True}, local_running=False)
    assert row[1] is False
    assert row[0] == doc._WARN


def test_live_reader_tolerates_a_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(doc, "_brain_link_path", lambda: tmp_path / "nope.json", raising=False)
    monkeypatch.setattr(doc, "_local_brain_running", lambda: False, raising=False)
    row = doc._brain_role_row()
    assert row[1] is True


def test_live_reader_reads_a_real_link(monkeypatch, tmp_path):
    p = tmp_path / "brain-link.json"
    p.write_text(json.dumps(LINK), encoding="utf-8")
    monkeypatch.setattr(doc, "_brain_link_path", lambda: p, raising=False)
    monkeypatch.setattr(doc, "_local_brain_running", lambda: True, raising=False)
    row = doc._brain_role_row()
    assert row[1] is False, "the live row must reach the same verdict as the pure one"
    assert "outpost-server" in row.detail
