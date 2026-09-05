"""navig.selfheal.heal_pr_submitter — Hive Mind PR creation for auto-heal patches.

STATUS: PR creation is NOT wired. ``submit_heal_pr`` raises :class:`HealPRNotWiredError` — it
never cloned, applied, committed or pushed anything, so GitHub had no branch to open a PR from
and could only answer 422. What *is* finished and used today: ``store_pending_patch`` (a durable
local record of the failure) plus the PR body/label builders, ready for whoever wires the git
side. See :class:`HealPRNotWiredError` for exactly what that requires.

Activation policy (enforced by the *caller*, not this module):
- Only triggered for FailureClass.UNKNOWN or when a primary fix attempt fails
- Never triggered for DB_PERMISSION_DENY (credential changes need human review)
- Requires ``autoheal_hive_enabled: True`` in the user's session
- Requires ``NAVIG_GITHUB_TOKEN`` env var to be set

Fallback: if GitHub is unreachable the patch is stored locally under
~/.navig/heal_patches/ and the caller is notified.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from navig.core.yaml_io import atomic_write_text
from navig.platform.paths import config_dir as _navig_config_dir
from navig.selfheal.git_manager import (
    UPSTREAM_REPO,
    _github_request,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Test seam — when ``None`` (the normal state), ``_heal_patches_dir()`` resolves
# at CALL time so NAVIG_CONFIG_DIR isolation set after import still applies
# (see navig/vault/migrate.py:_legacy_db_path).
_HEAL_PATCHES_DIR: Path | None = None


def _heal_patches_dir() -> Path:
    return (
        _HEAL_PATCHES_DIR if _HEAL_PATCHES_DIR is not None else _navig_config_dir() / "heal_patches"
    )


_LABEL_AUTO_HEAL = "auto-heal"
_LABEL_NEEDS_REVIEW = "needs-review"
_PR_BASE_BRANCH = "main"


class HealPRNotWiredError(RuntimeError):
    """PR submission cannot succeed because the git half of the flow was never wired.

    ``git_manager`` has every piece needed (``fork_repo`` · ``clone_or_update`` · ``sync_fork``
    · ``create_branch`` · ``apply_patch`` · ``commit_and_push``) but ``submit_heal_pr`` calls
    NONE of them: nothing is cloned, applied, committed or pushed, so the ``head`` branch never
    exists on GitHub and ``POST /pulls`` can only answer 422. It also called
    ``create_branch(token)`` while that function takes a *repo path* — the token became ``cwd``,
    so the call raised on every invocation and silently fell through to a branch name that was
    never created anywhere.

    Raised BEFORE any network call so the caller stores the patch and reports the real reason
    instead of burning two authenticated requests on a request that cannot be granted.

    Wiring this for real means auto-forking the upstream repo into the user's GitHub account and
    pushing branches to it — outward-facing actions that need an explicit owner decision — and it
    needs a real patch generator: the only caller passes prose (``"# Observed error\\n<stderr>"``),
    not a diff, so ``git apply`` would reject it anyway.
    """


class MissingGitHubTokenError(ValueError):
    """``NAVIG_GITHUB_TOKEN`` is absent — the one failure a user can fix by setting a variable.

    It subclasses ``ValueError`` so existing ``except ValueError`` handlers keep working, but it
    is a *distinct* type because ``git_manager._github_request`` also raises plain ``ValueError``
    for EVERY non-2xx GitHub response (401, 403 rate-limit, 404, 422 …). A caller that catches
    ``ValueError`` alone cannot tell "you have no token" from "GitHub said no", and reporting the
    second as the first sends the user to fix something that isn't broken.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class HealPRSubmitter:
    """Submit a GitHub Pull Request for a Hive Mind auto-heal patch.

    Instances are stateless — create one per submission or reuse freely.
    """

    def __init__(self) -> None:
        # Token resolved lazily so this class can be imported without env vars.
        self._token: str | None = None

    # ------------------------------------------------------------------ #
    #  Public methods                                                      #
    # ------------------------------------------------------------------ #

    def submit_heal_pr(
        self,
        failure_class: str,
        original_cmd: str,
        stderr: str,
        exit_code: int,
        patch_text: str,
        host: str | None = None,
    ) -> str:
        """Create a GitHub PR for an auto-heal patch.

        Args:
            failure_class: String name of the FailureClass enum value.
            original_cmd: The navig command that triggered the failure.
            stderr: Raw error output (will be truncated before inclusion).
            exit_code: Exit code of the failed command.
            patch_text: Unified diff or descriptive fix summary.
            host: Optional remote host the command was targeting.

        Returns:
            The HTML URL of the created PR.

        Raises:
            MissingGitHubTokenError: If ``NAVIG_GITHUB_TOKEN`` is not set.
            HealPRNotWiredError: Always, after the token check — the git half of this flow was
                never implemented, so a PR cannot be created. See that class for the details and
                for what wiring it would require.
            ValueError: If any GitHub API call returns a non-2xx response (raised by
                ``git_manager._github_request``) — catch this SEPARATELY from the token error,
                they mean different things to the user.
        """
        # The token check stays first so a missing token is still reported as a missing token
        # (the one thing the user can fix) rather than as the wiring gap.
        self._get_token()

        # Then fail, before spending two authenticated requests on a PR GitHub must refuse.
        # What used to follow assumed a pushed branch that nothing in this flow ever creates:
        # it called create_branch(token) — that function takes a repo PATH, so the token became
        # cwd and the call raised every time — and then POSTed a PR whose head branch did not
        # exist. _build_pr_body() and _attach_labels() are kept: they are the finished half,
        # ready for whoever wires the git side (fork -> clone -> apply -> commit -> push -> PR).
        raise HealPRNotWiredError(
            "Hive Mind cannot open a PR: the patch is never committed or pushed, so GitHub has "
            "no branch to open one from. The failure details are kept locally instead."
        )

    def store_pending_patch(
        self,
        failure_class: str,
        original_cmd: str,
        stderr: str,
        exit_code: int,
        patch_text: str,
        host: str | None = None,
    ) -> Path:
        """Persist a heal patch locally when GitHub is unreachable.

        The file is stored under ``~/.navig/heal_patches/`` with ``submitted: False``.

        NOTE: nothing resubmits these automatically. ``list_pending_patches()`` exists to read
        them back, but it has no production caller — so this is a durable record, not a retry
        queue. The user-facing message must not promise a retry that no code performs.

        Returns:
            Path to the written patch file.
        """
        patches_dir = _heal_patches_dir()
        patches_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"{ts}_{failure_class.lower()}.patch.json"
        patch_path = patches_dir / fname

        import json

        payload: dict[str, Any] = {
            "ts": ts,
            "failure_class": failure_class,
            "original_cmd": original_cmd,
            "stderr": stderr[:2000],
            "exit_code": exit_code,
            "host": host,
            "patch_text": patch_text,
            "submitted": False,
        }
        atomic_write_text(patch_path, json.dumps(payload, indent=2))
        logger.info("heal_pr: stored pending patch → {}", patch_path)
        return patch_path

    def list_pending_patches(self) -> list[Path]:
        """Return unsubmitted patch files stored locally."""
        patches_dir = _heal_patches_dir()
        if not patches_dir.exists():
            return []
        import json

        pending = []
        for p in sorted(patches_dir.glob("*.patch.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if not data.get("submitted", False):
                    pending.append(p)
            except (OSError, ValueError):
                continue
        return pending

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _get_token(self) -> str:
        if self._token:
            return self._token
        token = os.environ.get("NAVIG_GITHUB_TOKEN", "").strip()
        if not token:
            raise MissingGitHubTokenError(
                "NAVIG_GITHUB_TOKEN is not set. "
                "Hive Mind PR submission requires a GitHub Personal Access Token."
            )
        self._token = token
        return token

    def _attach_labels(self, token: str, pr_number: int) -> None:
        """Attach auto-heal labels to an open PR (best-effort)."""
        try:
            _github_request(
                "POST",
                f"/repos/{UPSTREAM_REPO}/issues/{pr_number}/labels",
                token=token,
                json={"labels": [_LABEL_AUTO_HEAL, _LABEL_NEEDS_REVIEW]},
            )
        except Exception as exc:
            # Non-fatal — PR is open even if labels fail
            logger.warning("heal_pr: could not attach labels to #{}: {}", pr_number, exc)

    @staticmethod
    def _build_pr_body(
        failure_class: str,
        original_cmd: str,
        stderr: str,
        exit_code: int,
        host: str | None,
        patch_text: str,
        ts: str,
    ) -> str:
        """Render a structured markdown PR body."""
        host_line = f"**Host:** `{host}`\n" if host else ""
        stderr_truncated = stderr[:800] + "\u2026" if len(stderr) > 800 else stderr

        return f"""## \U0001f9ec Auto-Heal Patch

> Generated by NAVIG Hive Mind on `{ts} UTC`
> Failure class: **{failure_class}**

{host_line}

---

## \U0001f4cb Error Log

**Command:** `navig {original_cmd}`
**Exit code:** `{exit_code}`

```
{stderr_truncated}
```

---

## \U0001f50d Root Cause

Automated classification identified this as a **{failure_class}** failure.
See the patch section below for the proposed resolution.

---

## \U0001f527 Fix Applied

```diff
{patch_text}
```

---

## \U0001f9ea Test Plan

- [ ] Reproduce the failure locally using the command above
- [ ] Apply the patch and confirm the failure class resolves
- [ ] Run `pytest tests/ --no-cov -q` — zero new failures
- [ ] Run `tsc --noEmit` in affected TS packages

---

*This PR was opened automatically by NAVIG Auto-Heal.
Assign for human review before merging.*
"""
