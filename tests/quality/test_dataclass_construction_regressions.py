"""Runtime regressions for the five wrong-shape constructions found tree-wide.

`tests/quality/test_dataclass_construction_contract.py` proves the *shapes* match statically.
These prove the affected code paths actually RUN — every one of them raised TypeError (or had its
TypeError swallowed into a wrong answer) before the fix, and none was covered by a test.

The Linux/macOS window adapters are driven with their shell/AppleScript calls mocked, so the real
code path executes on any OS — the bug was a constructor signature, not platform behaviour.
"""

from __future__ import annotations

from navig.adapters.automation.linux import LinuxAdapter
from navig.adapters.automation.macos import MacOSAdapter
from navig.adapters.automation.types import ExecutionResult, WindowInfo
from navig.selfheal.ssh_healer import HealResult

# ── Perplexity provider: super().__init__() had no config, and ProviderConfig had no env_key ──

def test_perplexity_client_constructs():
    """`create_perplexity_client()` raised TypeError — the whole provider was dead on arrival."""
    from navig.providers.perplexity import create_perplexity_client

    client = create_perplexity_client(api_key="pplx-test")
    assert client.name == "perplexity"
    assert client.base_url.startswith("http")
    assert client.api_key == "pplx-test"
    # ProviderConfig.api_key carries the ENV VAR NAME here (matches PERPLEXITY_PROVIDER).
    assert client.config.api_key == "PERPLEXITY_API_KEY"


# ── HealResult.pr_url: producer passed it, reply builder reads it, dataclass lacked it ────────

def test_heal_result_accepts_pr_url():
    """The Hive-Mind path passed pr_url= and raised TypeError right after opening a real PR."""
    r = HealResult(status="partial", message="m", pr_url="https://github.com/o/r/pull/1")
    assert r.pr_url.endswith("/pull/1")


def test_heal_result_pr_url_defaults_empty():
    """Every ssh_healer 'partial' result omits pr_url; the reply builder does `if result.pr_url`
    for exactly those, which used to raise AttributeError."""
    r = HealResult(status="partial", message="m")
    assert r.pr_url == ""
    assert not r.pr_url  # the falsy branch the reply builder relies on


# ── WindowInfo: class_name is REQUIRED and the Linux/macOS adapters never passed it ───────────

def _ok(stdout: str) -> ExecutionResult:
    return ExecutionResult(success=True, stdout=stdout)


def test_linux_get_window_info_returns_windowinfo(monkeypatch):
    adapter = LinuxAdapter()
    replies = {
        "getwindowgeometry": "  Position: 10,20\n  Geometry: 800x600",
        "getwindowname": "A Window",
        "getwindowpid": "4242",
    }

    def fake_run(cmd, capture_output=True):
        for key, out in replies.items():
            if key in cmd:
                return _ok(out)
        return _ok("")

    monkeypatch.setattr(adapter, "_run_command", fake_run)
    info = adapter._get_window_info("0x1")
    assert isinstance(info, WindowInfo)
    assert info.title == "A Window" and info.pid == 4242
    assert info.class_name == ""  # unknown, honestly reported (the ahk.py convention)


def test_linux_get_all_windows_returns_windowinfo(monkeypatch):
    adapter = LinuxAdapter()
    # `wmctrl -lG` style: id desktop x y w h host title
    line = "0x1 0 10 20 800 600 host My Title"
    monkeypatch.setattr(adapter, "_run_command", lambda *a, **k: _ok(line))
    windows = adapter.get_all_windows()
    assert len(windows) == 1
    assert isinstance(windows[0], WindowInfo)
    assert windows[0].class_name == ""


def test_macos_get_focused_window_returns_windowinfo(monkeypatch):
    adapter = MacOSAdapter()
    monkeypatch.setattr(
        adapter, "_run_applescript", lambda script: _ok("Safari|A Page|10|20|800|600")
    )
    info = adapter.get_focused_window()
    assert isinstance(info, WindowInfo)
    assert info.process_name == "Safari" and info.title == "A Page"
    assert info.class_name == ""  # AppleScript gives the app name, not a window class


# ── Onboarding key validation: Credential(...) missing 3 required fields, swallowed to False ──

def test_onboarding_validation_credential_shape():
    """`_validate_key` built a Credential without id/profile_id/label → TypeError → swallowed by
    `except Exception: return False`, so onboarding called EVERY api key invalid."""
    from navig.vault.types import Credential, CredentialType

    cred = Credential(
        id="",
        provider="openai",
        profile_id="default",
        credential_type=CredentialType.API_KEY,
        label="openai (onboarding validation)",
        data={"api_key": "sk-test"},
    )
    # Validators read only .data / .metadata — the rest are inert placeholders.
    assert cred.data["api_key"] == "sk-test"
    assert cred.metadata == {}
