"""navig.selfheal.heal_pr_submitter — Hive Mind PR creation for auto-heal patches.

Opens a GitHub Pull Request from a heal-context dict.  Wraps the existing
``git_manager`` / GitHub REST infrastructure that the ``navig contribute``
pipeline already uses.

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

from navig.platform.paths import config_dir as _navig_config_dir
from navig.selfheal.git_manager import (
    UPSTREAM_REPO,
    _github_request,
    create_branch,
    get_github_username,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEAL_PATCHES_DIR = _navig_config_dir() / "heal_patches"
_LABEL_AUTO_HEAL = "auto-heal"
_LABEL_NEEDS_REVIEW = "needs-review"
_PR_BASE_BRANCH = "main"


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
            ValueError: If ``NAVIG_GITHUB_TOKEN`` is not set.
            RuntimeError: If the GitHub API call fails unexpectedly.
        """
        token = self._get_token()
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        branch = f"fix/autoheal-{ts}"

        # Create branch in user's fork
        try:
            branch = create_branch(token)  # uses navig-selfheal/{date}-{hash} pattern
        except Exception as exc:
            logger.warning("heal_pr: create_branch failed, using timestamp branch: {}", exc)
            # Fall through — we'll attempt PR creation anyway

        username = get_github_username(token)
        head = f"{username}:{branch}"

        body = self._build_pr_body(
            failure_class=failure_class,
            original_cmd=original_cmd,
            stderr=stderr,
            exit_code=exit_code,
            host=host,
            patch_text=patch_text,
            ts=ts,
        )

        pr_data = _github_request(
            "POST",
            f"/repos/{UPSTREAM_REPO}/pulls",
            token=token,
            json={
                "title": f"fix(navig): auto-heal patch \u2014 {failure_class} @ {ts}",
                "body": body,
                "head": head,
                "base": _PR_BASE_BRANCH,
                "draft": False,
            },
        )

        pr_number = pr_data.get("number")
        pr_url = pr_data.get("html_url", "")

        # Attach labels (best-effort — label creation is idempotent)
        if pr_number:
            self._attach_labels(token, pr_number)

        logger.info("heal_pr: opened PR #{} — {}", pr_number, pr_url)
        return pr_url

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

        The file is stored under ``~/.navig/heal_patches/`` and will be picked
        up for retry on the next bot restart.

        Returns:
            Path to the written patch file.
        """
        _HEAL_PATCHES_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"{ts}_{failure_class.lower()}.patch.json"
        patch_path = _HEAL_PATCHES_DIR / fname

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
        patch_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("heal_pr: stored pending patch → {}", patch_path)
        return patch_path

    def list_pending_patches(self) -> list[Path]:
        """Return unsubmitted patch files stored locally."""
        if not _HEAL_PATCHES_DIR.exists():
            return []
        import json

        pending = []
        for p in sorted(_HEAL_PATCHES_DIR.glob("*.patch.json")):
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
            raise ValueError(
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
