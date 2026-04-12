from __future__ import annotations

import io

import navig.main as main_mod
import pytest

pytestmark = pytest.mark.integration


def test_fast_path_handles_help_with_prefixed_global_flags(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(main_mod.sys, "stdout", stream)

    handled = main_mod._maybe_handle_fast_path(["navig", "--host", "prod", "--help"])

    assert handled is True
    assert "NAVIG" in stream.getvalue()


def test_fast_path_handles_version_with_prefixed_global_flags(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(main_mod.sys, "stdout", stream)

    handled = main_mod._maybe_handle_fast_path(["navig", "--host", "prod", "--version"])

    assert handled is True
    assert stream.getvalue().strip()


def test_fast_path_handles_global_only_invocation_as_help(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(main_mod.sys, "stdout", stream)

    handled = main_mod._maybe_handle_fast_path(["navig", "--host", "prod"])

    assert handled is True
    assert "NAVIG" in stream.getvalue()
