"""navig.bot.contribute_flow — Telegram approval flow for Self-Heal PRs.

Presents the user with grouped scan findings (Critical → High → Medium),
collects per-group approval/rejection via inline keyboard buttons, then
requests final confirmation before calling :func:`~navig.selfheal.pr_builder.submit_pr`.

Rejected severity groups are excluded from the patch before submission; the
diff is rebuilt from the approved subset.

Telegram MarkdownV2 is used throughout.  A CLI fallback is provided for
users without a Telegram bot configured (detected by absence of
``TELEGRAM_BOT_TOKEN``).

Usage (from the contribute command)::

    from navig.bot.contribute_flow import run_approval_flow

    pr_url = run_approval_flow(findings, branch, config, alias, version)
"""

from __future__ import annotations

import os
import re

from loguru import logger

from navig.selfheal.scanner import ScanFinding

# ---------------------------------------------------------------------------
# MarkdownV2 helpers
# ---------------------------------------------------------------------------

# Characters that must be escaped in Telegram MarkdownV2 bodies.
_MDV2_ESCAPE_CHARS = r"\_*[]()~`>#+-=|{}.!"


def _escape_md2(text: str) -> str:
    """Escape *text* for Telegram MarkdownV2.

    Args:
        text: Raw string to escape.

    Returns:
        String safe for use in a Telegram MarkdownV2 message.
    """
    return re.sub(r"([" + re.escape(_MDV2_ESCAPE_CHARS) + r"])", r"\\\1", text)


# ---------------------------------------------------------------------------
# Group findings by severity
# ---------------------------------------------------------------------------

_APPROVAL_SEVERITIES = ("critical", "high", "medium")
_SEVERITY_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
}


def _group_by_severity(
    findings: list[ScanFinding],
) -> dict[str, list[ScanFinding]]:
    """Group *findings* by severity level (critical/high/medium only).

    Args:
        findings: All scan findings (all severities accepted).

    Returns:
        Ordered dict keyed by severity; low-severity findings are dropped
        from the approval flow.
    """
    grouped: dict[str, list[ScanFinding]] = {}
    for sev in _APPROVAL_SEVERITIES:
        group = [f for f in findings if f.severity == sev]
        if group:
            grouped[sev] = group
    return grouped


def _format_group_message(sev: str, group: list[ScanFinding]) -> str:
    """Build a MarkdownV2 summary message for one severity group.

    Args:
        sev: Severity label (``"critical"`` / ``"high"`` / ``"medium"``).
        group: Findings in this group.

    Returns:
        MarkdownV2 formatted string ready for Telegram.
    """
    emoji = _SEVERITY_EMOJI.get(sev, "•")
    header = _escape_md2(f"{emoji} {sev.upper()} — {len(group)} finding(s)")
    lines = [f"*{header}*\n"]
    for f in group:
        file_part = _escape_md2(f.file)
        desc_part = _escape_md2(f.description[:100])
        fix_part = _escape_md2(f.suggested_fix[:100])
        lines.append(
            f"• `{file_part}:{f.line}` \\({f.confidence:.2f}\\)\n"
            f"  _{desc_part}_\n"
            f"  Fix: `{fix_part}`\n"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI fallback approval
# ---------------------------------------------------------------------------


def _cli_approve_group(sev: str, group: list[ScanFinding]) -> bool:
    """Prompt the user via CLI to approve or reject a severity group.

    Args:
        sev: Severity label.
        group: Findings in this group.

    Returns:
        True when the user approves the group.
    """
    import typer  # noqa: PLC0415 — deferred; typer already a hard dep

    emoji = _SEVERITY_EMOJI.get(sev, "•")
    typer.echo(f"\n{emoji} {sev.upper()} — {len(group)} finding(s):")
    for f in group:
        typer.echo(f"  [{f.confidence:.2f}] {f.file}:{f.line} — {f.description}")
        typer.echo(f"    Fix: {f.suggested_fix[:120]}")
    return typer.confirm(f"Include {sev.upper()} group in the patch?", default=True)


def cli_review_and_approve(
    findings: list[ScanFinding],
    branch: str,
    config: dict | None = None,
    alias: str = "",
    version: str | None = None,
) -> str | None:
    """Full CLI-based approval flow (fallback when Telegram is not configured).

    Groups findings by severity, prompts for per-group approval, shows a
    diff summary, then submits the PR on final confirmation.

    Args:
        findings: All scan findings from the scanner.
        branch: Git branch name for the PR head.
        config: ``contribute`` config dict from ``navig.yaml``.
        alias: Contributor alias.
        version: NAVIG version string.

    Returns:
        PR HTML URL on success; ``None`` if the user cancels.
    """
    import typer  # noqa: PLC0415

    from navig.selfheal.git_manager import _CORE_REPO_DIR  # noqa: PLC0415
    from navig.selfheal.patcher import build_patch  # noqa: PLC0415
    from navig.selfheal.pr_builder import submit_pr  # noqa: PLC0415

    grouped = _group_by_severity(findings)
    if not grouped:
        typer.echo("No findings to review.")
        return None

    approved_findings: list[ScanFinding] = []

    for sev in _APPROVAL_SEVERITIES:
        group = grouped.get(sev, [])
        if not group:
            continue
        if _cli_approve_group(sev, group):
            approved_findings.extend(group)
        else:
            typer.echo(f"  ↳ {sev.upper()} group excluded from patch.")

    if not approved_findings:
        typer.echo("\nNo groups approved — nothing to submit.")
        return None

    # Rebuild patch from approved subset only.
    repo_path = _CORE_REPO_DIR
    patch_str = build_patch(approved_findings, repo_path)
    if not patch_str:
        typer.echo("\nNo effective changes after filtering — nothing to patch.")
        return None

    typer.echo(f"\n--- Patch preview ({len(patch_str)} bytes) ---")
    # Show first 1500 chars of diff for readability.
    typer.echo(patch_str[:1500] + ("...\n[truncated]" if len(patch_str) > 1500 else ""))
    typer.echo("--- End of patch preview ---\n")

    if not typer.confirm("Submit this patch as a GitHub Pull Request?", default=False):
        typer.echo("Cancelled.")
        return None

    typer.echo("Applying patch and submitting PR…")
    try:
        from navig.selfheal.git_manager import (  # noqa: PLC0415
            apply_patch,
            commit_and_push,
        )

        apply_patch(repo_path, patch_str)
        summary = f"{len(approved_findings)} finding(s)"
        commit_and_push(repo_path, branch, summary)
        pr_url = submit_pr(
            branch=branch,
            findings=approved_findings,
            config=config,
            alias=alias,
            version=version,
        )
        typer.echo(f"\n✅ PR submitted: {pr_url}")
        return pr_url
    except Exception as exc:  # noqa: BLE001
        logger.error("PR submission failed: {}", exc)
        typer.echo(f"\n❌ PR submission failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Telegram approval flow
# ---------------------------------------------------------------------------


class ContributeFlow:
    """Telegram conversation handler for the self-heal approval flow.

    Drives the user through per-severity approval, shows a diff summary, and
    on final confirmation applies the patch and submits the PR.

    This class is framework-agnostic: it generates message text / keyboard
    specs rather than making Telegram API calls directly.  The caller is
    responsible for sending messages and routing callback queries back.

    Args:
        findings: All scan findings returned by the scanner.
        branch: Git branch name for the PR head.
        config: ``contribute`` config dict from ``navig.yaml``.
        alias: Contributor alias.
        version: NAVIG version string.
    """

    def __init__(
        self,
        findings: list[ScanFinding],
        branch: str,
        config: dict | None = None,
        alias: str = "",
        version: str | None = None,
    ) -> None:
        self._all_findings = findings
        self._branch = branch
        self._config = config or {}
        self._alias = alias
        self._version = version
        self._grouped = _group_by_severity(findings)
        self._pending_severities = list(self._grouped.keys())
        self._approved: list[ScanFinding] = []
        self._rejected_severities: list[str] = []

    # ------------------------------------------------------------------
    # State machine helpers (called by the Telegram worker)
    # ------------------------------------------------------------------

    def start_messages(self) -> list[dict]:
        """Return the opening messages for the approval flow.

        Returns:
            List of ``{"text": str, "keyboard": list[list[dict]]|None}`` dicts.
        """
        total = len(self._all_findings)
        severity_counts = {sev: len(grp) for sev, grp in self._grouped.items()}
        summary = ", ".join(f"{cnt} {sev}" for sev, cnt in severity_counts.items())
        intro = _escape_md2(
            f"🧠 Self-Heal scan complete: {total} finding(s) — {summary}\n"
            "Review each severity group and approve or reject."
        )
        messages = [{"text": intro, "keyboard": None}]
        if self._pending_severities:
            messages.append(self._group_prompt(self._pending_severities[0]))
        return messages

    def _group_prompt(self, sev: str) -> dict:
        """Build the prompt message for a single severity group.

        Args:
            sev: Severity to prompt for.

        Returns:
            Message dict with ``text`` and ``keyboard``.
        """
        group = self._grouped[sev]
        text = _format_group_message(sev, group)
        keyboard = [
            [
                {"text": "Approve ✅", "callback_data": f"selfheal:approve:{sev}"},
                {"text": "Reject ❌", "callback_data": f"selfheal:reject:{sev}"},
            ]
        ]
        return {"text": text, "keyboard": keyboard, "parse_mode": "MarkdownV2"}

    def handle_callback(self, callback_data: str) -> dict:
        """Process an inline keyboard callback and return the next message.

        Args:
            callback_data: The ``callback_data`` field from the Telegram
                callback query (e.g. ``"selfheal:approve:critical"``).

        Returns:
            ``{"text": str, "keyboard": ..., "done": bool, "pr_url": str|None}``
            dict.  ``done=True`` means the flow has completed.
        """
        parts = callback_data.split(":", 2)
        if len(parts) != 3 or parts[0] != "selfheal":
            return {
                "text": "Unexpected callback.",
                "keyboard": None,
                "done": False,
                "pr_url": None,
            }

        _, action, sev = parts

        # Record decision
        if action == "approve" and sev in self._grouped:
            self._approved.extend(self._grouped[sev])
        elif action == "reject":
            self._rejected_severities.append(sev)

        # Advance to next pending severity
        if sev in self._pending_severities:
            self._pending_severities.remove(sev)

        if self._pending_severities:
            next_msg = self._group_prompt(self._pending_severities[0])
            next_msg["done"] = False
            next_msg["pr_url"] = None
            return next_msg

        # All groups resolved — show final diff summary
        return self._final_summary_message()

    def _final_summary_message(self) -> dict:
        """Build the final confirmation message.

        Returns:
            Final message dict asking for PR submission confirmation.
        """
        if not self._approved:
            return {
                "text": _escape_md2("No groups approved — nothing to submit."),
                "keyboard": None,
                "done": True,
                "pr_url": None,
                "parse_mode": "MarkdownV2",
            }

        approved_count = len(self._approved)
        rejected_str = ", ".join(self._rejected_severities) or "none"
        text = _escape_md2(
            f"✅ {approved_count} finding(s) approved for patching.\n"
            f"Rejected groups: {rejected_str}\n\n"
            "Submit as GitHub Pull Request?"
        )
        keyboard = [
            [
                {"text": "Confirm & Submit PR ✅", "callback_data": "selfheal:submit"},
                {"text": "Cancel ❌", "callback_data": "selfheal:cancel"},
            ]
        ]
        return {
            "text": text,
            "keyboard": keyboard,
            "done": False,
            "pr_url": None,
            "parse_mode": "MarkdownV2",
        }

    def handle_submit(self) -> dict:
        """Apply the patch and submit the PR after final user confirmation.

        Returns:
            Message dict with ``pr_url`` set on success and ``done=True``.
        """
        from navig.selfheal.git_manager import (  # noqa: PLC0415
            _CORE_REPO_DIR,
            apply_patch,
            commit_and_push,
        )
        from navig.selfheal.patcher import build_patch  # noqa: PLC0415
        from navig.selfheal.pr_builder import submit_pr  # noqa: PLC0415

        repo_path = _CORE_REPO_DIR
        try:
            patch_str = build_patch(self._approved, repo_path)
            if not patch_str:
                return {
                    "text": _escape_md2(
                        "⚠️ No effective patch changes. Nothing submitted."
                    ),
                    "keyboard": None,
                    "done": True,
                    "pr_url": None,
                    "parse_mode": "MarkdownV2",
                }
            apply_patch(repo_path, patch_str)
            summary = f"{len(self._approved)} finding(s)"
            commit_and_push(repo_path, self._branch, summary)
            pr_url = submit_pr(
                branch=self._branch,
                findings=self._approved,
                config=self._config,
                alias=self._alias,
                version=self._version,
            )
            text = _escape_md2(f"🚀 PR submitted successfully!\n{pr_url}")
            return {
                "text": text,
                "keyboard": None,
                "done": True,
                "pr_url": pr_url,
                "parse_mode": "MarkdownV2",
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Telegram flow PR submission failed: {}", exc)
            return {
                "text": _escape_md2(f"❌ Submission failed: {exc}"),
                "keyboard": None,
                "done": True,
                "pr_url": None,
                "parse_mode": "MarkdownV2",
            }


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def run_approval_flow(
    findings: list[ScanFinding],
    branch: str,
    config: dict | None = None,
    alias: str = "",
    version: str | None = None,
) -> str | None:
    """Route to Telegram or CLI approval flow based on bot configuration.

    Checks ``TELEGRAM_BOT_TOKEN`` environment variable.  If set, logs a
    message indicating the flow must be driven via the Telegram worker;
    otherwise falls back to the interactive CLI flow.

    In the Telegram case, this function returns ``None`` immediately — the
    actual PR submission happens asynchronously via :class:`ContributeFlow`
    callback handling in the bot worker.

    Args:
        findings: All scan findings from the scanner.
        branch: Git branch name.
        config: ``contribute`` config dict.
        alias: Contributor alias.
        version: NAVIG version string.

    Returns:
        PR URL string (CLI path) or ``None`` (Telegram path / user cancel).
    """
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if tg_token:
        # Telegram bot is configured — the approval flow runs asynchronously
        # through the bot worker.  Callers should instantiate ContributeFlow
        # and wire its messages into the active Telegram session.
        logger.info(
            "Telegram bot detected — contribute flow will run in bot session. "
            "Use ContributeFlow class to drive the conversation."
        )
        return None

    # No Telegram configured → interactive CLI fallback.
    logger.info("No Telegram bot configured; falling back to CLI approval flow")
    return cli_review_and_approve(
        findings=findings,
        branch=branch,
        config=config,
        alias=alias,
        version=version,
    )
