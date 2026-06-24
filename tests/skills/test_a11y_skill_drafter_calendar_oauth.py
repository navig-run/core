"""Tests for browser/a11y.py, agent/skill_drafter.py, connectors/google_calendar/oauth_config.py — batch 54."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# browser/a11y — annotate_a11y_snapshot
# ---------------------------------------------------------------------------


def test_annotate_empty_string():
    from navig.browser.a11y import annotate_a11y_snapshot

    text, refs = annotate_a11y_snapshot("")
    assert text == ""
    assert refs == {}


def test_annotate_single_role_line():
    from navig.browser.a11y import annotate_a11y_snapshot

    raw = "- button \"Submit\""
    text, refs = annotate_a11y_snapshot(raw)
    assert "[0]" in text
    assert refs[0]["role"] == "button"
    assert refs[0]["name"] == "Submit"


def test_annotate_increments_ref_ids():
    from navig.browser.a11y import annotate_a11y_snapshot

    raw = "- button \"A\"\n- link \"B\""
    text, refs = annotate_a11y_snapshot(raw)
    assert 0 in refs
    assert 1 in refs


def test_annotate_skips_path_lines():
    from navig.browser.a11y import annotate_a11y_snapshot

    raw = "- /some/path\n- button \"OK\""
    text, refs = annotate_a11y_snapshot(raw)
    assert "[0]" in text
    # path line gets no ref — only one ref assigned
    assert len(refs) == 1


def test_annotate_non_list_lines_passed_through():
    from navig.browser.a11y import annotate_a11y_snapshot

    raw = "header text\n- button \"Go\""
    text, refs = annotate_a11y_snapshot(raw)
    assert "header text" in text
    assert len(refs) == 1


def test_annotate_bracket_name():
    from navig.browser.a11y import annotate_a11y_snapshot

    raw = "- input [Search]"
    text, refs = annotate_a11y_snapshot(raw)
    assert refs[0]["role"] == "input"
    assert refs[0]["name"] == "Search"


def test_annotate_raw_line_stored():
    from navig.browser.a11y import annotate_a11y_snapshot

    raw = "  - checkbox \"Accept\""
    text, refs = annotate_a11y_snapshot(raw)
    assert refs[0]["raw_line"] == "  - checkbox \"Accept\""


def test_annotate_preserves_indentation_in_output():
    from navig.browser.a11y import annotate_a11y_snapshot

    raw = "  - link \"Home\""
    text, _ = annotate_a11y_snapshot(raw)
    assert text.startswith("  -")


# ---------------------------------------------------------------------------
# agent/skill_drafter — SkillDrafter, SkillDraft
# ---------------------------------------------------------------------------


def _fake_pattern(cmd: str):
    p = MagicMock()
    p.sequence = (cmd,)
    return p


def test_skill_drafter_draft_returns_skill_draft():
    from navig.agent.skill_drafter import SkillDrafter, SkillDraft

    sd = SkillDrafter(output_dir="/tmp/skills_test")
    draft = sd.draft(_fake_pattern("ls -la"))
    assert isinstance(draft, SkillDraft)


def test_skill_drafter_draft_name_is_slugified():
    from navig.agent.skill_drafter import SkillDrafter

    sd = SkillDrafter(output_dir="/tmp/skills_test")
    draft = sd.draft(_fake_pattern("git status"))
    assert draft.name == "git-status"


def test_skill_drafter_draft_safe_true_for_safe_cmd():
    from navig.agent.skill_drafter import SkillDrafter

    sd = SkillDrafter(output_dir="/tmp/skills_test")
    draft = sd.draft(_fake_pattern("ls -la"))
    assert draft.safe is True


def test_skill_drafter_draft_safe_false_for_rm():
    from navig.agent.skill_drafter import SkillDrafter

    sd = SkillDrafter(output_dir="/tmp/skills_test")
    draft = sd.draft(_fake_pattern("rm -rf /tmp/something"))
    assert draft.safe is False


def test_skill_drafter_draft_safe_false_for_force():
    from navig.agent.skill_drafter import SkillDrafter

    sd = SkillDrafter(output_dir="/tmp/skills_test")
    draft = sd.draft(_fake_pattern("some cmd --force"))
    assert draft.safe is False


def test_skill_drafter_draft_yaml_contains_cmd():
    from navig.agent.skill_drafter import SkillDrafter

    sd = SkillDrafter(output_dir="/tmp/skills_test")
    draft = sd.draft(_fake_pattern("docker ps"))
    assert "docker ps" in draft.yaml_text
    assert "name:" in draft.yaml_text
    assert "steps:" in draft.yaml_text


def test_skill_drafter_draft_empty_sequence():
    from navig.agent.skill_drafter import SkillDrafter

    sd = SkillDrafter(output_dir="/tmp/skills_test")
    p = MagicMock()
    p.sequence = ()
    draft = sd.draft(p)
    assert draft.name == "command"


def test_skill_drafter_apply_writes_file(tmp_path):
    from navig.agent.skill_drafter import SkillDrafter, SkillDraft

    sd = SkillDrafter(output_dir=tmp_path)
    draft = SkillDraft(name="my-skill", safe=True, yaml_text="name: my-skill\n")
    with patch("navig.agent.skill_drafter.atomic_write_text") as mock_write:
        path = sd.apply(draft)
    mock_write.assert_called_once()
    assert path == tmp_path / "my-skill.yaml"


def test_slugify_removes_special_chars():
    from navig.agent.skill_drafter import SkillDrafter

    assert SkillDrafter._slugify("hello world!") == "hello-world"


def test_slugify_truncates_to_64():
    from navig.agent.skill_drafter import SkillDrafter

    long_text = "a" * 100
    result = SkillDrafter._slugify(long_text)
    assert len(result) <= 64


def test_slugify_lowercase():
    from navig.agent.skill_drafter import SkillDrafter

    assert SkillDrafter._slugify("GIT STATUS") == "git-status"


# ---------------------------------------------------------------------------
# connectors/google_calendar/oauth_config
# ---------------------------------------------------------------------------


def test_calendar_scopes_contains_calendar():
    from navig.connectors.google_calendar.oauth_config import CALENDAR_SCOPES

    assert any("calendar" in s for s in CALENDAR_SCOPES)


def test_calendar_scopes_contains_email():
    from navig.connectors.google_calendar.oauth_config import CALENDAR_SCOPES

    assert "email" in CALENDAR_SCOPES


def test_calendar_scopes_contains_openid():
    from navig.connectors.google_calendar.oauth_config import CALENDAR_SCOPES

    assert "openid" in CALENDAR_SCOPES


def test_build_calendar_oauth_config_returns_config():
    from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config

    cfg = build_calendar_oauth_config(client_id="test-client-id")
    assert cfg is not None


def test_build_calendar_oauth_config_name():
    from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config

    cfg = build_calendar_oauth_config(client_id="cid")
    assert cfg.name == "Google Calendar"


def test_build_calendar_oauth_config_client_id():
    from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config

    cfg = build_calendar_oauth_config(client_id="my-id")
    assert cfg.client_id == "my-id"


def test_build_calendar_oauth_config_scopes():
    from navig.connectors.google_calendar.oauth_config import (
        build_calendar_oauth_config,
        CALENDAR_SCOPES,
    )

    cfg = build_calendar_oauth_config(client_id="cid")
    assert cfg.scopes == CALENDAR_SCOPES


def test_build_calendar_oauth_config_authorize_url_is_google():
    from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config

    cfg = build_calendar_oauth_config(client_id="cid")
    assert "google" in cfg.authorize_url.lower() or "accounts" in cfg.authorize_url.lower()


def test_build_calendar_oauth_config_no_secret():
    from navig.connectors.google_calendar.oauth_config import build_calendar_oauth_config

    cfg = build_calendar_oauth_config(client_id="cid")
    assert cfg.client_secret is None
