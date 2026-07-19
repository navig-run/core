"""Default AI personality prompt: accurate breadth + safe self-heal.

`navig ask` speaks with the ``ai_system_prompt.txt`` personality. The default
must describe NAVIG's REAL breadth (an operator's hands across live systems) —
a narrow default made "who are you?" undersell the product. A superseded default
that was never edited self-heals to the current one; a customised file is never
touched.

Run: cd core && python -m pytest tests/config/test_ai_prompt_default.py -q
"""

from __future__ import annotations

import pytest

from navig.config import (
    _DEFAULT_AI_PROMPT,
    _STALE_DEFAULT_AI_PROMPTS,
    ConfigManager,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def cfg(tmp_path):
    """Isolated ConfigManager — its ai_prompt_file lives under tmp_path."""
    return ConfigManager(config_dir=tmp_path / ".navig")


# ── the default itself ────────────────────────────────────────────────


def test_fresh_default_describes_real_breadth(cfg):
    prompt = cfg.get_ai_system_prompt()
    # Real capabilities the old narrow default omitted.
    for marker in (
        "Browse and operate real websites",
        "servers over SSH",
        "Deploy and manage infrastructure",
        "parallel worktrees",
        "coordinating several sub-agents",
        "Life-OS",
    ):
        assert marker in prompt, marker
    # The narrow framing is gone.
    assert "three domains" not in prompt


def test_default_prompt_keeps_safety_and_honesty_rules(cfg):
    prompt = cfg.get_ai_system_prompt()
    assert "Never invent file paths" in prompt
    assert "Warn about potential risks" in prompt
    assert "never fabricate" in prompt
    # Don't lead with a capability dump unless asked.
    assert "unless asked" in prompt


# ── self-heal matrix ──────────────────────────────────────────────────


def test_stale_default_is_self_healed_on_disk(cfg):
    stale = next(iter(_STALE_DEFAULT_AI_PROMPTS))
    cfg.ai_prompt_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.ai_prompt_file.write_text(stale, encoding="utf-8")

    prompt = cfg.get_ai_system_prompt()

    # Returned prompt is the upgraded one…
    assert "Browse and operate real websites" in prompt
    assert "three domains" not in prompt
    # …and the file on disk was rewritten (so the next read is fast + consistent).
    on_disk = cfg.ai_prompt_file.read_text(encoding="utf-8")
    assert on_disk.strip() == _DEFAULT_AI_PROMPT.strip()


def test_customised_prompt_is_never_overwritten(cfg):
    custom = "You are Bob. You only answer in haiku.\n"
    cfg.ai_prompt_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.ai_prompt_file.write_text(custom, encoding="utf-8")

    prompt = cfg.get_ai_system_prompt()

    # A file the operator authored is returned verbatim and left on disk as-is.
    assert prompt.strip() == custom.strip()
    assert cfg.ai_prompt_file.read_text(encoding="utf-8") == custom


def test_current_default_is_not_flagged_stale():
    # Guard against a future edit accidentally adding the live default to the
    # stale set — that would make the file rewrite itself on every read.
    assert _DEFAULT_AI_PROMPT.strip() not in {s.strip() for s in _STALE_DEFAULT_AI_PROMPTS}


def test_stale_default_self_heals_despite_crlf_line_endings(cfg):
    # Regression: on Windows, atomic_write_text writes CRLF (text mode) and git
    # may check files out CRLF, but the stale fingerprints use LF. The self-heal
    # only works because read_text normalises CRLF→LF before the membership test.
    # Pin that: a CRLF-on-disk stale default MUST still be detected + upgraded.
    stale = next(iter(_STALE_DEFAULT_AI_PROMPTS))
    cfg.ai_prompt_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.ai_prompt_file.write_bytes(stale.replace("\n", "\r\n").encode("utf-8"))
    assert b"\r\n" in cfg.ai_prompt_file.read_bytes()  # sanity: really CRLF on disk

    prompt = cfg.get_ai_system_prompt()

    assert "Browse and operate real websites" in prompt
    assert "three domains" not in prompt
    assert cfg.ai_prompt_file.read_text(encoding="utf-8").strip() == _DEFAULT_AI_PROMPT.strip()
