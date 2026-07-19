"""Unit tests for the CDP ``--load-extension`` flag builder (`_extension_args`),
the ``--window-size`` flag builder (`_window_size_args`), and the launch/new guards
that reject a bad extension path / window size *before* launching."""

from __future__ import annotations

import pytest

from navig.browser.cdp_actions import _extension_args, _window_size_args, launch, new


def test_no_extension_returns_empty():
    assert _extension_args(None) == []
    assert _extension_args("") == []
    assert _extension_args("  ,  ") == []  # only blanks → nothing to load


def test_valid_extension_builds_all_three_flags(tmp_path):
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "manifest.json").write_text("{}", encoding="utf-8")

    args = _extension_args(str(ext))
    target = str(ext)
    assert f"--load-extension={target}" in args
    assert f"--disable-extensions-except={target}" in args
    # Chrome 137+ stable ignores --load-extension without this feature disabled.
    assert "--disable-features=DisableLoadExtensionCommandLineSwitch" in args


def test_multiple_extensions_comma_joined(tmp_path):
    a = tmp_path / "a"; a.mkdir(); (a / "manifest.json").write_text("{}", encoding="utf-8")
    b = tmp_path / "b"; b.mkdir(); (b / "manifest.json").write_text("{}", encoding="utf-8")

    args = _extension_args(f"{a} , {b}")  # whitespace around the comma is tolerated
    load = next(x for x in args if x.startswith("--load-extension="))
    assert str(a) in load and str(b) in load


def test_missing_dir_raises(tmp_path):
    with pytest.raises(ValueError, match="not a directory"):
        _extension_args(str(tmp_path / "does-not-exist"))


def test_dir_without_manifest_raises(tmp_path):
    ext = tmp_path / "ext"
    ext.mkdir()
    with pytest.raises(ValueError, match="manifest.json"):
        _extension_args(str(ext))


def test_launch_bad_extension_errors_without_launching(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not launch when the extension path is invalid")

    monkeypatch.setattr("navig.browser.targets.launch_with_cdp", _boom)
    res = launch("chrome", port=9222, load_extension=str(tmp_path / "missing"))
    assert res["ok"] is False
    assert "not a directory" in res["error"]


def test_new_bad_extension_errors_without_launching(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not launch when the extension path is invalid")

    monkeypatch.setattr("navig.browser.targets.launch_with_cdp", _boom)
    res = new("chrome", load_extension=str(tmp_path / "missing"))
    assert res["ok"] is False
    assert "not a directory" in res["error"]


# ── --window-size flag builder ──────────────────────────────────────────────────

def test_no_window_size_returns_empty():
    assert _window_size_args(None) == []
    assert _window_size_args("") == []


def test_valid_window_size_builds_flag():
    assert _window_size_args("1440x900") == ["--window-size=1440,900"]
    # tolerant of case + surrounding/inner whitespace
    assert _window_size_args("  1280 X 720 ") == ["--window-size=1280,720"]


@pytest.mark.parametrize("bad", ["1440", "1440x", "x900", "axb", "1440*900", "1440,900", "0x900", "1440x0"])
def test_bad_window_size_raises(bad):
    with pytest.raises(ValueError, match="window-size"):
        _window_size_args(bad)


def test_new_bad_window_size_errors_without_launching(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not launch when the window size is invalid")

    monkeypatch.setattr("navig.browser.targets.launch_with_cdp", _boom)
    res = new("chrome", window_size="1440")
    assert res["ok"] is False
    assert "window-size" in res["error"]


def test_new_threads_headless_and_window_size_into_launch(monkeypatch):
    """`new()` must forward --headless=new AND --window-size=W,H as launch extra_args."""
    seen: dict = {}

    class _FakeTarget:
        def to_dict(self):
            return {"port": 9222}

    def _fake_launch(app, *, port, user_data_dir=None, profile_directory=None,
                     extra_args=None, wait=True):
        seen["extra_args"] = list(extra_args or [])
        return _FakeTarget()

    monkeypatch.setattr("navig.browser.targets.launch_with_cdp", _fake_launch)
    monkeypatch.setattr("navig.browser.targets.find_free_port", lambda: 9222)
    monkeypatch.setattr("navig.browser.targets.new_session_profile_dir", lambda name=None: "/tmp/p")

    res = new("chrome", headless=True, window_size="1440x900")
    assert res["ok"] is True
    assert res["headless"] is True
    assert res["window_size"] == "1440x900"
    assert "--headless=new" in seen["extra_args"]
    assert "--window-size=1440,900" in seen["extra_args"]
