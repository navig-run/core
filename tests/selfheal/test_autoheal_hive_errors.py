"""Hive Mind must not misdiagnose a GitHub failure as a missing token — and must not promise
a retry that no code performs.

`git_manager._github_request` raises a plain `ValueError` for EVERY non-2xx GitHub response
(401, 403 rate-limit, 404, 422 bad head branch …). `_handle_unknown_failure` caught bare
`ValueError` under a comment saying "NAVIG_GITHUB_TOKEN not set", so a healthy install with a
valid token was told its token was missing — and, because that branch returns early, the
local-patch fallback never ran, so the patch was LOST.

`MissingGitHubTokenError` (a ValueError subclass, so existing handlers still work) now marks the
one case a user can actually fix.

The later tests cover the deeper finding: `submit_heal_pr` could never succeed at all — nothing
clones, commits or pushes, so the `head` branch never existed and GitHub could only answer 422.
It now raises `HealPRNotWiredError` before any network call, and the user is never told a PR was
opened.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.channels.telegram_autoheal import (
    AutoHealMixin,
    FailureClass,
    FailureContext,
)
from navig.selfheal.heal_pr_submitter import (
    HealPRNotWiredError,
    HealPRSubmitter,
    MissingGitHubTokenError,
)


def _ctx() -> FailureContext:
    return FailureContext(
        original_cmd="run --host box uptime",
        chat_id=1,
        user_id=2,
        failure_class=FailureClass.UNKNOWN,
        stderr="something exploded",
        exit_code=1,
        host="box",
    )


class _Session:
    autoheal_hive_enabled = True


class _SM:
    def get_or_create_session(self, *_a, **_k):
        return _Session()


class _Heal(AutoHealMixin):
    """Minimal host for the mixin — hive mind is ON so the PR path is reached."""

    def _get_session_manager_safe(self):
        return _SM()


def _run(monkeypatch, *, submit_raises: Exception, tmp_path):
    """Drive _handle_unknown_failure with submit_heal_pr raising *submit_raises*."""
    def boom(*_a, **_k):
        raise submit_raises

    monkeypatch.setattr(HealPRSubmitter, "submit_heal_pr", boom)
    monkeypatch.setattr(
        "navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", tmp_path / "heal_patches"
    )
    return asyncio.run(_Heal()._handle_unknown_failure(_ctx()))


def test_missing_token_error_is_a_valueerror():
    """Subclassing keeps every existing `except ValueError` handler working."""
    assert issubclass(MissingGitHubTokenError, ValueError)


def test_missing_token_reports_the_token(monkeypatch, tmp_path):
    result = _run(monkeypatch, submit_raises=MissingGitHubTokenError("no token"), tmp_path=tmp_path)
    assert "NAVIG_GITHUB_TOKEN" in result.message
    assert result.status == "failed"


@pytest.mark.parametrize(
    "api_error",
    [
        ValueError("GitHub API POST /repos/x/pulls → 403: rate limit exceeded"),
        ValueError("GitHub API POST /repos/x/pulls → 422: head branch not found"),
        ValueError("GitHub API GET /user → 401: bad credentials"),
    ],
)
def test_github_api_failure_is_not_reported_as_a_missing_token(monkeypatch, tmp_path, api_error):
    """The regression: any non-2xx used to say 'your token is not set'."""
    result = _run(monkeypatch, submit_raises=api_error, tmp_path=tmp_path)
    assert "NAVIG_GITHUB_TOKEN" not in result.message


def test_github_api_failure_still_stores_the_patch_locally(monkeypatch, tmp_path):
    """The costlier half: the bare-ValueError branch returned early, so the fallback that
    preserves the patch never ran and the work was lost."""
    result = _run(
        monkeypatch,
        submit_raises=ValueError("GitHub API POST /pulls → 403: rate limited"),
        tmp_path=tmp_path,
    )
    stored = list((tmp_path / "heal_patches").glob("*.patch.json"))
    assert stored, "the patch must be preserved when GitHub refuses the PR"
    assert result.status == "partial"
    assert stored[0].name in result.message


def test_fallback_does_not_promise_an_automatic_retry(monkeypatch, tmp_path):
    """`store_pending_patch` writes a record; NOTHING resubmits it (list_pending_patches has no
    production caller). The message must not claim a retry."""
    result = _run(
        monkeypatch,
        submit_raises=ValueError("GitHub API POST /pulls → 500: server error"),
        tmp_path=tmp_path,
    )
    assert "retried" not in result.message.lower()
    assert "restart" not in result.message.lower()


# ── submit_heal_pr itself: it can never succeed, so it must say so without calling GitHub ────


def test_submit_heal_pr_raises_not_wired_without_touching_github(monkeypatch):
    """It never cloned/committed/pushed, so the head branch never existed and POST /pulls could
    only 422. It also called create_branch(token) — that function takes a repo PATH — so the call
    raised on every invocation. Now it fails fast, before spending authenticated requests."""
    monkeypatch.setenv("NAVIG_GITHUB_TOKEN", "ghp_test")

    def explode(*_a, **_k):  # any GitHub call is a failure of this test
        raise AssertionError("submit_heal_pr must not reach the network")

    monkeypatch.setattr("navig.selfheal.git_manager._github_request", explode)
    with pytest.raises(HealPRNotWiredError):
        HealPRSubmitter().submit_heal_pr(
            failure_class="UNKNOWN", original_cmd="run x", stderr="boom",
            exit_code=1, patch_text="# not a diff",
        )


def test_missing_token_still_takes_precedence(monkeypatch):
    """A user with no token must be told about the token, not about the wiring gap."""
    monkeypatch.delenv("NAVIG_GITHUB_TOKEN", raising=False)
    with pytest.raises(MissingGitHubTokenError):
        HealPRSubmitter().submit_heal_pr(
            failure_class="UNKNOWN", original_cmd="run x", stderr="boom",
            exit_code=1, patch_text="# not a diff",
        )


def test_end_to_end_unknown_failure_preserves_patch_and_claims_no_pr(monkeypatch, tmp_path):
    """The real submitter (nothing mocked) through the real handler: the user must never be told
    a PR was opened, and the failure detail must survive on disk."""
    monkeypatch.setenv("NAVIG_GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(
        "navig.selfheal.heal_pr_submitter._HEAL_PATCHES_DIR", tmp_path / "heal_patches"
    )
    result = asyncio.run(_Heal()._handle_unknown_failure(_ctx()))

    assert result.status == "partial"
    assert "View PR" not in result.message  # never claim a PR exists
    assert not result.pr_url
    stored = list((tmp_path / "heal_patches").glob("*.patch.json"))
    assert stored, "the failure record must be kept locally"
    assert "retried" not in result.message.lower()
    assert "restart" not in result.message.lower()
