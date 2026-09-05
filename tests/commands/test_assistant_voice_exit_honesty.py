"""`navig ai diagnose` / `navig voice` reported failure and exited 0.

These live in `commands/assistant.py`, which is NOT only the deprecated
`navig assistant *` surface: `commands/ai.py` delegates `ai diagnose` and
`ai show --context` straight to `analyze_cmd` / `context_cmd`. A scan of ai.py
reports zero sites precisely because the bugs are in the helper it calls — which
is why "module X is clean" is not the same as "that command surface is honest".

The sharpest one is not an exit code: `analyze_cmd` printed `✓ Analysis complete`
unconditionally, while both of its inner checks degrade to a warning on failure.
An analysis that collected no metrics AND read no issues still announced itself
as complete — a green tick over exactly nothing, in the command whose only job is
to look.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer

import navig.commands.assistant as a_mod

pytestmark = pytest.mark.integration


# ── assistant / ai diagnose ────────────────────────────────────────────


def _cfg(active="prod"):
    return SimpleNamespace(
        get_active_server=lambda: active,
        load_server_config=lambda _n: {"host": "h"},
    )


def _patch_env(monkeypatch, *, cfg=None, assistant=None, remote_ok=True):
    monkeypatch.setattr(a_mod, "get_config_manager", lambda: cfg or _cfg())
    monkeypatch.setattr(a_mod, "ProactiveAssistant", lambda _c: assistant or MagicMock())
    if remote_ok:
        monkeypatch.setattr(a_mod, "RemoteOperations", lambda _c: MagicMock())
    else:
        def _boom(_c):
            raise RuntimeError("ssh down")
        monkeypatch.setattr(a_mod, "RemoteOperations", _boom)


def test_analyze_with_no_active_server_exits_non_zero(monkeypatch):
    _patch_env(monkeypatch, cfg=_cfg(active=None))
    with pytest.raises(typer.Exit) as exc:
        a_mod.analyze_cmd({})
    assert exc.value.exit_code == 1


def test_analyze_with_unreachable_server_exits_non_zero(monkeypatch):
    _patch_env(monkeypatch, remote_ok=False)
    with pytest.raises(typer.Exit) as exc:
        a_mod.analyze_cmd({})
    assert exc.value.exit_code == 1


def test_analyze_that_checked_nothing_does_not_claim_completion(monkeypatch, tmp_path, capsys):
    """Both inner checks fail -> it must NOT print "✓ Analysis complete"."""
    assistant = MagicMock()
    assistant.auto_detection.collect_performance_metrics.side_effect = RuntimeError("no ssh")
    # The issues check must RAISE, not merely find nothing: an absent
    # detected_issues.json is a check that ran successfully and had nothing to
    # report (pinned by the next test). A directory in its place exists but
    # cannot be opened, which is a genuine read failure.
    ctx_dir = tmp_path / "ai"
    (ctx_dir / "detected_issues.json").mkdir(parents=True)
    assistant.ai_context_dir = ctx_dir
    _patch_env(monkeypatch, assistant=assistant)

    with pytest.raises(typer.Exit) as exc:
        a_mod.analyze_cmd({})

    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "Analysis complete" not in out, out
    assert "every check failed" in out


def test_analyze_that_partially_checked_says_which_were_skipped(monkeypatch, tmp_path, capsys):
    """Metrics succeed, issues cannot be read -> "incomplete", not "complete"."""
    assistant = MagicMock()
    assistant.auto_detection.collect_performance_metrics.return_value = {
        "cpu_percent": 1.0, "memory_percent": 2.0, "disk_percent": 3.0, "status": "ok",
    }
    assistant.ai_context_dir = tmp_path  # exists, but no detected_issues.json
    _patch_env(monkeypatch, assistant=assistant)

    a_mod.analyze_cmd({})  # must NOT raise — metrics did run

    out = capsys.readouterr().out
    # detected_issues.json is absent, which is not a failure: the check ran and
    # found nothing to report, so this is a complete analysis.
    assert "Analysis complete" in out, out


def test_context_that_cannot_be_generated_exits_non_zero(monkeypatch):
    assistant = MagicMock()
    assistant.context_generator.generate_context_summary.side_effect = RuntimeError("nope")
    _patch_env(monkeypatch, assistant=assistant)

    with pytest.raises(typer.Exit) as exc:
        a_mod.context_cmd({})
    assert exc.value.exit_code == 1


def test_context_that_cannot_be_saved_exits_non_zero(monkeypatch, tmp_path):
    """`ai show --context --file x.json && upload x.json` must not upload nothing."""
    assistant = MagicMock()
    assistant.context_generator.generate_context_summary.return_value = {"a": 1}
    _patch_env(monkeypatch, assistant=assistant)

    unwritable = tmp_path / "no-such-dir" / "ctx.json"
    with pytest.raises(typer.Exit) as exc:
        a_mod.context_cmd({}, file_path=str(unwritable))
    assert exc.value.exit_code == 1


def test_a_clipboard_failure_still_exits_zero_because_it_degrades(monkeypatch, capsys):
    """The caller still gets the context on stdout, so this is not a failure.

    Only the glyph was wrong — an ✗ over a zero exit is the same disagreement
    pointing the other way.
    """
    assistant = MagicMock()
    assistant.context_generator.generate_context_summary.return_value = {"a": 1}
    _patch_env(monkeypatch, assistant=assistant)
    monkeypatch.setattr(
        a_mod.pyperclip, "copy", lambda _t: (_ for _ in ()).throw(RuntimeError("no clip"))
    )

    a_mod.context_cmd({}, clipboard=True)  # must not raise

    out = capsys.readouterr().out
    assert '"a": 1' in out, "the context was not printed as a fallback"


def test_a_declined_reset_exits_non_zero(monkeypatch, capsys):
    """Consistent with `database restore`: the operation did not happen."""
    _patch_env(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *_a: "no")

    with pytest.raises(typer.Exit) as exc:
        a_mod.reset_cmd({})
    assert exc.value.exit_code == 1
    assert "nothing was deleted" in capsys.readouterr().out


def test_a_reset_that_fails_part_way_reports_what_it_cleared(monkeypatch, tmp_path, capsys):
    """The loop mutates file by file — a mid-loop failure leaves a mixed state."""
    assistant = MagicMock()
    ctx_dir = tmp_path / "ai"
    ctx_dir.mkdir()
    # Two of the five files exist; the second write blows up.
    for name in ("command_history.json", "error_log.json"):
        (ctx_dir / name).write_text("[1]", encoding="utf-8")
    assistant.ai_context_dir = ctx_dir
    assistant.navig_dir = tmp_path
    _patch_env(monkeypatch, assistant=assistant)

    real_open = open
    calls = {"n": 0}

    def _flaky(path, *a, **k):
        if str(path).endswith(".json") and "w" in (a[0] if a else k.get("mode", "")):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", _flaky)

    with pytest.raises(typer.Exit) as exc:
        a_mod.reset_cmd({"yes": True})

    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "Partially reset" in out, out
    assert "command_history.json" in out


# ── voice ──────────────────────────────────────────────────────────────


def _voice_app():
    from navig.commands.voice import voice_app

    return voice_app


def _runner():
    from typer.testing import CliRunner

    return CliRunner()


def _result(success: bool, **extra):
    r = MagicMock()
    r.success = success
    r.error = "engine unavailable"
    for k, v in extra.items():
        setattr(r, k, v)
    return r


def _stub_voice(monkeypatch, *, tts=None, stt=None):
    """Satisfy the voice callback's plugin probe and swap the engines."""
    import sys

    monkeypatch.setitem(sys.modules, "navig_audio", SimpleNamespace(voice=SimpleNamespace()))
    monkeypatch.setitem(sys.modules, "navig_audio.voice", SimpleNamespace())
    mod_tts = SimpleNamespace(
        TTSProvider=lambda v: (_ for _ in ()).throw(ValueError(v)) if v == "bogus" else v,
        get_tts=lambda: tts or MagicMock(),
    )
    mod_stt = SimpleNamespace(
        STTProvider=lambda v: (_ for _ in ()).throw(ValueError(v)) if v == "bogus" else v,
        get_stt=lambda: stt or MagicMock(),
    )
    monkeypatch.setitem(sys.modules, "navig.voice.tts", mod_tts)
    monkeypatch.setitem(sys.modules, "navig.voice.stt", mod_stt)


def test_a_failed_tts_exits_non_zero(monkeypatch):
    """`voice speak "x" -o out.mp3 && upload out.mp3` must not upload nothing."""
    async def _synth(*_a, **_k):
        return _result(False)

    tts = MagicMock()
    tts.synthesize = _synth
    _stub_voice(monkeypatch, tts=tts)

    res = _runner().invoke(_voice_app(), ["speak", "hello", "--no-play"])
    assert res.exit_code == 1, res.output
    assert "TTS Failed" in res.output


def test_a_failed_transcription_exits_non_zero(monkeypatch, tmp_path):
    async def _tr(*_a, **_k):
        return _result(False)

    stt = MagicMock()
    stt.transcribe = _tr
    _stub_voice(monkeypatch, stt=stt)
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")

    res = _runner().invoke(_voice_app(), ["transcribe", str(f)])
    assert res.exit_code == 1, res.output
    assert "Transcription failed" in res.output


def test_an_unknown_provider_is_a_usage_error(monkeypatch, tmp_path):
    _stub_voice(monkeypatch)
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")

    res = _runner().invoke(_voice_app(), ["transcribe", str(f), "--provider", "bogus"])
    assert res.exit_code == 2, res.output
    assert "Unknown provider" in res.output


def test_a_successful_tts_still_exits_zero(monkeypatch, tmp_path):
    """Anti-vacuity for the voice half."""
    async def _synth(*_a, **_k):
        return _result(True, audio_path=str(tmp_path / "o.mp3"), provider=None, voice="v")

    tts = MagicMock()
    tts.synthesize = _synth
    _stub_voice(monkeypatch, tts=tts)

    res = _runner().invoke(_voice_app(), ["speak", "hello", "--no-play"])
    assert res.exit_code == 0, res.output
