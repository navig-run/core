"""The capture seam: the session decides, and the advice names a tool that works.

The defect these tests pin: the builtin screenshot tool offered `scrot`,
`gnome-screenshot` and `import` — three X11 clients — on every Linux host, so a Wayland
desktop (Fedora 40+, Ubuntu 24.04, GNOME 45+) failed every capture and then advised
installing `scrot`, which cannot capture Wayland even when it is installed.
"""

from __future__ import annotations

import pytest

from navig.platform import capture


def _which(present: set[str]):
    def find(program: str) -> str | None:
        return f"/usr/bin/{program}" if program in present else None

    return find


def test_a_wayland_session_is_detected_from_its_own_variable(monkeypatch):
    environ = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert capture.session(os_name="linux", environ=environ) == "wayland"


def test_an_x11_session_is_detected_from_display(monkeypatch):
    assert capture.session(os_name="linux", environ={"DISPLAY": ":0"}) == "x11"


def test_a_host_with_no_display_is_headless():
    assert capture.session(os_name="linux", environ={}) == "headless"


def test_wsl_with_wslg_counts_as_a_graphical_session():
    # WSLg exports both; the session is what matters, not the kernel underneath.
    assert capture.session(os_name="wsl", environ={"WAYLAND_DISPLAY": "wayland-0"}) == "wayland"


@pytest.mark.parametrize(
    ("os_name", "expected"),
    [("windows", "windows"), ("macos", "macos"), ("linux", "x11")],
)
def test_each_os_gets_its_own_session(os_name, expected):
    assert capture.session(os_name=os_name, environ={"DISPLAY": ":0"}) == expected


def test_wayland_never_offers_an_x11_only_tool():
    names = [backend.name for backend in capture.backends("wayland")]
    assert names == ["grim", "gnome-screenshot", "spectacle"]
    assert "scrot" not in names
    assert "import" not in names


def test_x11_offers_scrot_and_import():
    names = [backend.name for backend in capture.backends("x11")]
    assert names[0] == "scrot"
    assert "import" in names


def test_macos_offers_screencapture_only():
    assert [b.name for b in capture.backends("macos")] == ["screencapture"]


def test_windows_stays_in_process_because_the_os_has_no_capture_command():
    backends = capture.backends("windows")
    assert len(backends) == 1
    assert backends[0].kind == "in-process"
    assert backends[0].argv("x.png") is None


def test_choose_picks_the_first_present_backend():
    chosen = capture.choose("wayland", which=_which({"gnome-screenshot", "spectacle"}))
    assert chosen is not None
    assert chosen.name == "gnome-screenshot"


def test_choose_returns_none_when_the_session_has_nothing():
    assert capture.choose("wayland", which=_which(set())) is None
    assert capture.choose("headless", which=_which({"scrot"})) is None


def test_a_headless_session_has_no_backends_at_all():
    assert capture.backends("headless") == ()


def test_screencapture_argv_carries_the_display_and_the_region():
    backend = capture.choose("macos", which=_which({"screencapture"}))
    assert backend is not None
    assert backend.argv("/tmp/a.png", monitor=2) == [
        "screencapture",
        "-x",
        "-D",
        "2",
        "/tmp/a.png",
    ]
    assert backend.argv("/tmp/a.png", region=(10, 20, 110, 120)) == [
        "screencapture",
        "-x",
        "-R",
        "10,20,100,100",
        "/tmp/a.png",
    ]


def test_grim_takes_a_geometry_and_a_plain_path():
    backend = capture.choose("wayland", which=_which({"grim"}))
    assert backend is not None
    assert backend.argv("/tmp/b.png") == ["grim", "/tmp/b.png"]
    assert backend.argv("/tmp/b.png", region=(0, 0, 1920, 1080))[1:3] == [
        "-g",
        "0,0 1920x1080",
    ]


def test_scrot_and_image_magick_spell_a_region_differently():
    scrot = capture.choose("x11", which=_which({"scrot"}))
    assert scrot is not None
    assert scrot.argv("/tmp/c.png", region=(1, 2, 31, 42)) == [
        "scrot",
        "-a",
        "1,2,30,40",
        "/tmp/c.png",
    ]

    magick = next(b for b in capture.backends("x11") if b.name == "import")
    assert magick.argv("/tmp/c.png", region=(1, 2, 31, 42)) == [
        "import",
        "-window",
        "root",
        "-crop",
        "30x40+1+2",
        "/tmp/c.png",
    ]


def test_spectacle_writes_the_file_itself():
    backend = next(b for b in capture.backends("wayland") if b.name == "spectacle")
    assert backend.argv("/tmp/d.png") == ["spectacle", "-b", "-n", "-o", "/tmp/d.png"]


def test_the_wayland_hint_says_scrot_cannot_help():
    hint = capture.install_hint("wayland")
    assert "grim" in hint
    assert "Wayland" in hint
    assert "scrot" in hint  # named explicitly, as the thing that will not work


def test_the_headless_hint_names_xvfb():
    assert "xvfb-run" in capture.install_hint("headless")


def test_describe_reports_the_session_and_what_is_available(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr("navig.platform.capture.shutil.which", lambda _p: None)
    snapshot = capture.describe()
    assert snapshot["session"] == "wayland"
    assert "grim" in snapshot["backends"]
    assert snapshot["available"] == []
    assert "grim" in str(snapshot["hint"])


def test_an_unknown_backend_name_raises_instead_of_guessing():
    with pytest.raises(ValueError):
        capture.CaptureBackend("nope", "argv", program="nope").argv("x.png")
