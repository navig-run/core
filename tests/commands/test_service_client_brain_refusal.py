"""A client must refuse to become a brain — not merely report it.

Why this test exists
--------------------
`navig doctor` now detects the contradiction (commit 7795274462): a brain-link means "my
brain lives elsewhere", a local daemon means "my brain lives here", both at once is the
2026-10-22 two-brain state. Detection fired, and the state still happened, because
`navig service start` did what it was told.

Reporting is for the person reading the report. A refusal is for the machine that is about
to create a second authority while nobody is reading anything. The operator keeps a way
out: `--force`, which says "I know, I mean it".
"""

from __future__ import annotations

import json

import pytest

from navig.commands import service as svc

pytestmark = pytest.mark.unit

LINK = {"brainName": "outpost-server", "deviceId": "83d011d16b16", "url": "https://example/os/abc"}


def test_a_client_refuses_to_start_a_local_brain():
    refusal = svc._client_brain_refusal(LINK, force=False)
    assert refusal is not None, "a client must not start a brain silently"
    assert "outpost-server" in refusal
    assert "--force" in refusal, "the refusal must name the override, or it is a dead end"


def test_the_override_is_honoured():
    assert svc._client_brain_refusal(LINK, force=True) is None


def test_a_standalone_install_is_not_blocked():
    assert svc._client_brain_refusal(None, force=False) is None


def test_an_unreadable_link_does_not_block_a_start():
    """A corrupt file is not evidence of intent; blocking on it would strand the machine."""
    assert svc._client_brain_refusal({"__corrupt__": True}, force=False) is None


def test_the_live_reader_finds_the_link(monkeypatch, tmp_path):
    p = tmp_path / "brain-link.json"
    p.write_text(json.dumps(LINK), encoding="utf-8")
    monkeypatch.setattr(svc, "_brain_link_path", lambda: p, raising=False)
    refusal = svc._client_brain_refusal(svc._read_brain_link(), force=False)
    assert refusal is not None and "outpost-server" in refusal


def test_a_missing_link_file_reads_as_no_link(monkeypatch, tmp_path):
    monkeypatch.setattr(svc, "_brain_link_path", lambda: tmp_path / "nope.json", raising=False)
    assert svc._read_brain_link() is None


def test_service_start_accepts_the_force_flag():
    """The CLI surface must expose the override, or the refusal has no escape hatch."""
    import inspect

    params = inspect.signature(svc.service_start).parameters
    assert "force" in params, f"service_start has no --force override: {list(params)}"
