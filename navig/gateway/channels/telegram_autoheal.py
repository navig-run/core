"""navig.gateway.channels.telegram_autoheal — Self-healing diagnostic mixin.

Pipeline: command failure → classify → heal (auto or guided) → report → retry.

Designed as a cooperative mixin following the same pattern as TelegramMeshMixin.
Must appear *before* TelegramCommandsMixin in TelegramChannel's MRO so that
this mixin's patched _handle_cli_command intercepts first.

Public Telegram API surface:
    /autoheal on           — enable silent auto-fix
    /autoheal off          — disable; use inline keyboard instead  (safe default)
    /autoheal status       — show config + last 5 events
    /autoheal hive on      — enable Hive Mind PR submissions
    /autoheal hive off     — disable Hive Mind

Inline keyboard actions (callback_data prefix "heal_"):
    heal_fix:<key>         — attempt programmatic repair
    heal_diag:<key>        — run diagnostics, return report
    heal_explain:<key>     — plain-English explanation

Key design decisions:
- _active_heals dedup: one heal task per (user_id, host) pair at a time
- Hard cap of 2 fix attempts; escalates on third
- DB_PERMISSION_DENY never auto-fixed (credential changes = human gate)
- UNKNOWN failure class triggers Hive Mind only when hive_enabled
- All SSH operations are async (asyncio.create_subprocess_exec)
- No raw stack traces to the user — all exception handling sanitizes output
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from navig.gateway.channels.telegram import TelegramChannel

# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------


class FailureClass(str, Enum):
    """Every command failure is assigned one of these before any action is taken."""
    SSH_AUTH_FAIL       = "SSH_AUTH_FAIL"        # publickey / password rejected
    SSH_HOSTKEY_UNKNOWN = "SSH_HOSTKEY_UNKNOWN"  # strict host key check failed
    SSH_TRANSPORT_FAIL  = "SSH_TRANSPORT_FAIL"   # exit 255, connection refused / hung
    DB_PERMISSION_DENY  = "DB_PERMISSION_DENY"   # DB auth / ACL failure
    CMD_NOT_FOUND       = "CMD_NOT_FOUND"        # missing binary on remote
    TIMEOUT             = "TIMEOUT"              # command hung or timed out
    UNKNOWN             = "UNKNOWN"              # unclassified — escalate


# Human-readable badge for each class
_CLASS_BADGE: Dict[FailureClass, str] = {
    FailureClass.SSH_AUTH_FAIL:       "🔐 SSH_AUTH_FAIL",
    FailureClass.SSH_HOSTKEY_UNKNOWN: "🔑 SSH_HOSTKEY_UNKNOWN",
    FailureClass.SSH_TRANSPORT_FAIL:  "📡 SSH_TRANSPORT_FAIL",
    FailureClass.DB_PERMISSION_DENY:  "🛢 DB_PERMISSION_DENY",
    FailureClass.CMD_NOT_FOUND:       "🔍 CMD_NOT_FOUND",
    FailureClass.TIMEOUT:             "⌛ TIMEOUT",
    FailureClass.UNKNOWN:             "❓ UNKNOWN",
}

# One-liner cause explanation shown to user in any mode
_CLASS_EXPLANATION: Dict[FailureClass, str] = {
    FailureClass.SSH_AUTH_FAIL: (
        "The server rejected your SSH credentials. "
        "Either the public key is not in `authorized_keys`, or password auth is disabled."
    ),
    FailureClass.SSH_HOSTKEY_UNKNOWN: (
        "SSH refused to connect because the server's host key is not in your `known_hosts` file. "
        "This is a safety check — you need to trust the host first."
    ),
    FailureClass.SSH_TRANSPORT_FAIL: (
        "The SSH connection itself failed to establish. "
        "The server may be unreachable, the port may be blocked, or the SSH daemon may be down."
    ),
    FailureClass.DB_PERMISSION_DENY: (
        "The database server denied access. "
        "Your credentials may be wrong or your user lacks permission for this database."
    ),
    FailureClass.CMD_NOT_FOUND: (
        "The command was not found on the remote server. "
        "The required binary may not be installed or is not in the PATH."
    ),
    FailureClass.TIMEOUT: (
        "The command did not respond within the allowed time limit. "
        "It may still be running on the server, or the connection dropped mid-execution."
    ),
    FailureClass.UNKNOWN: (
        "An unexpected failure occurred that doesn't match a known error pattern. "
        "More investigation is needed."
    ),
}


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------


@dataclass
class FailureContext:
    """All information captured at the point of failure."""
    original_cmd: str       # raw navig command string (without leading "navig ")
    chat_id: int
    user_id: int
    failure_class: FailureClass
    stderr: str             # raw stderr / error text from the failed operation
    exit_code: int
    host: Optional[str]     # extracted from cmd if parseable, else None
    attempt_count: int = 0  # number of fix attempts already made this session


@dataclass
class HealEvent:
    """Serialisable record stored in session.heal_history (last 5)."""
    timestamp: str
    failure_class: str
    status: str             # "resolved" | "partial" | "failed"
    cmd: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "failure_class": self.failure_class,
            "status": self.status,
            "cmd": self.cmd,
        }


# Re-export HealResult from ssh_healer so callers can import from one place
from navig.selfheal.ssh_healer import HealResult  # noqa: E402


# ---------------------------------------------------------------------------
# Classifier — pure function, no I/O
# ---------------------------------------------------------------------------


# Precompile patterns for speed (called on every failed command)
_PAT_AUTH = re.compile(
    r"Permission denied \((?:publickey|password|gssapi|keyboard-interactive)",
    re.IGNORECASE,
)
_PAT_HOSTKEY = re.compile(
    r"Host key verification failed|"
    r"no (?:ECDSA|ED25519|RSA) host key is known|"
    r"WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED",
    re.IGNORECASE,
)
_PAT_DB_DENY = re.compile(
    r"Access denied for user|"
    r"FATAL.*password authentication failed|"
    r"authentication failed for user",
    re.IGNORECASE,
)
_PAT_CMD_NOT_FOUND = re.compile(
    r"command not found|No such file or directory|"
    r"is not recognized as an internal or external command",
    re.IGNORECASE,
)
_PAT_TIMEOUT = re.compile(r"timed out|timeout expired", re.IGNORECASE)


def classify_failure(stderr: str, exit_code: int, cmd: str) -> FailureClass:
    """Classify a command failure into a FailureClass without any I/O.

    Evaluation order matters: more specific patterns first.

    Args:
        stderr: The raw error text returned by the failing command.
        exit_code: The numeric exit code (255 is special for SSH transport).
        cmd: The original navig command string (used for context only).

    Returns:
        The most specific FailureClass that matches the inputs.
    """
    if _PAT_AUTH.search(stderr):
        return FailureClass.SSH_AUTH_FAIL

    if _PAT_HOSTKEY.search(stderr):
        return FailureClass.SSH_HOSTKEY_UNKNOWN

    # SSH transport failures report exit code 255 even when stderr is minimal
    if exit_code == 255:
        return FailureClass.SSH_TRANSPORT_FAIL

    if _PAT_DB_DENY.search(stderr):
        return FailureClass.DB_PERMISSION_DENY

    if _PAT_CMD_NOT_FOUND.search(stderr):
        return FailureClass.CMD_NOT_FOUND

    if _PAT_TIMEOUT.search(stderr) or exit_code == 124:
        return FailureClass.TIMEOUT

    return FailureClass.UNKNOWN


# ---------------------------------------------------------------------------
# SSH response error detector — scan on_message output for known patterns
# ---------------------------------------------------------------------------

# These SSH error strings appear verbatim in navig's remote.py output
_SSH_ERROR_PATTERNS = [
    "Permission denied (publickey",
    "Permission denied (password",
    "Host key verification failed",
    "no ED25519 host key is known",
    "no ECDSA host key is known",
    "no RSA host key is known",
    "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED",
    "Connection refused",
    "ssh: connect to host",
    "Command exited with code: 255",
]

# Regex to extract --host or -h flag value from navig commands
_HOST_PATTERN = re.compile(
    r"""(?:--host|-h)\s+(['"]?)([^\s'"]+)\1""",
    re.IGNORECASE,
)
# Also try plain "host" word after known command prefixes
_HOST_ALT = re.compile(r"""(?:run|docker|db|file|web|backup)\s+(?:--host|-h)\s+(\S+)""")


def _extract_host(cmd: str) -> Optional[str]:
    """Best-effort extraction of the target host from a navig command string."""
    m = _HOST_PATTERN.search(cmd) or _HOST_ALT.search(cmd)
    return m.group(2) if m else None


def _exit_code_from_response(response: str) -> int:
    """Parse 'Command exited with code: N' from response text, or return 0."""
    m = re.search(r"[Cc]ommand exited with code[:\s]+(\d+)", response)
    return int(m.group(1)) if m else 0


def detect_failure_in_response(
    response: str, cmd: str
) -> Optional[FailureContext]:
    """Scan the output of on_message() for SSH / connection error signatures.

    Args:
        response: The text returned by on_message() for a failed navig command.
        cmd: The original navig command (without the "navig " prefix).

    Returns:
        A FailureContext if a known error is detected, else None.
    """
    if not response:
        return None

    matched = any(pat in response for pat in _SSH_ERROR_PATTERNS)
    exit_code = _exit_code_from_response(response)
    
    # Trigger autoheal if explicit ssh failure OR non-zero exit OR generic command failed
    has_failed = "Command failed" in response or "command not found" in response or "Permission denied" in response
    if not (matched or exit_code != 0 or has_failed):
        return None

    fc = classify_failure(response, exit_code, cmd)
    host = _extract_host(cmd)

    # chat_id / user_id filled in by the caller since we don't have them here
    return FailureContext(
        original_cmd=cmd,
        chat_id=0,
        user_id=0,
        failure_class=fc,
        stderr=response,
        exit_code=exit_code,
        host=host,
    )


# ---------------------------------------------------------------------------
# AutoHealMixin
# ---------------------------------------------------------------------------

# Maximum fix attempts per failure before escalating to the user
_MAX_FIX_ATTEMPTS = 2


class AutoHealMixin:
    """
    Cooperative mixin — plugs into TelegramChannel without modifying
    the existing command dispatch logic.

    State (all in-process, not persisted between restarts):
        _active_heals:    dict[str, asyncio.Task]  — dedup by "user_id:host"
        _pending_heal_ctx: dict[int, FailureContext] — user_id → pending context
                           (the context for which an inline keyboard was shown)
    """

    # These are populated on first use; defined here for linter satisfaction
    _active_heals: Dict[str, asyncio.Task]
    _pending_heal_ctx: Dict[int, FailureContext]

    def _init_autoheal_state(self) -> None:
        """Call from TelegramChannel.__init__ after super().__init__()."""
        self._active_heals = {}
        self._pending_heal_ctx = {}

    # ------------------------------------------------------------------ #
    #  /autoheal command handler                                           #
    # ------------------------------------------------------------------ #

    async def _handle_autoheal(
        self,
        chat_id: int,
        user_id: int,
        args: str,
    ) -> None:
        """Handle /autoheal [on|off|status|hive on|hive off]."""
        args = args.strip().lower()
        sm = self._get_session_manager_safe()

        if args in ("on", "enable"):
            if sm:
                sm.update_settings(chat_id, user_id, autoheal_enabled=True)
            await self.send_message(
                chat_id,
                "🔧 *Auto-Heal ON* — I'll silently attempt repairs when commands fail.",
                parse_mode="Markdown",
            )
            return

        if args in ("off", "disable"):
            if sm:
                sm.update_settings(chat_id, user_id, autoheal_enabled=False)
            await self.send_message(
                chat_id,
                "🔕 *Auto-Heal OFF* — I'll show you diagnostic options when commands fail.",
                parse_mode="Markdown",
            )
            return

        if args == "hive on":
            if sm:
                sm.update_settings(chat_id, user_id, autoheal_hive_enabled=True)
            await self.send_message(
                chat_id,
                "🐝 *Hive Mind ON* — unknown failures will open GitHub PRs automatically.",
                parse_mode="Markdown",
            )
            return

        if args == "hive off":
            if sm:
                sm.update_settings(chat_id, user_id, autoheal_hive_enabled=False)
            await self.send_message(
                chat_id,
                "🐝 *Hive Mind OFF* — local fixes only, no PR submissions.",
                parse_mode="Markdown",
            )
            return

        if args in ("status", ""):
            await self._send_autoheal_status(chat_id, user_id, sm)
            return

        await self.send_message(
            chat_id,
            "Usage: `/autoheal [on|off|status|hive on|hive off]`",
            parse_mode="Markdown",
        )

    async def _send_autoheal_status(
        self, chat_id: int, user_id: int, sm: Any
    ) -> None:
        """Render current settings and last 5 heal events."""
        autoheal_on = False
        hive_on = False
        history: List[Dict[str, Any]] = []

        if sm:
            session = sm.get_or_create_session(chat_id, user_id, False)
            autoheal_on = getattr(session, "autoheal_enabled", False)
            hive_on = getattr(session, "autoheal_hive_enabled", False)
            history = getattr(session, "heal_history", [])

        ah_icon = "🟢" if autoheal_on else "🔴"
        hm_icon = "🟢" if hive_on else "🔴"

        lines = [
            "⚙️ *Auto-Heal Status*",
            "",
            f"{ah_icon} Auto-Heal: `{'ON' if autoheal_on else 'OFF'}`",
            f"{hm_icon} Hive Mind:  `{'ON' if hive_on else 'OFF'}`",
        ]

        if history:
            lines += ["", "─── *Last heal events* ───"]
            for evt in history[-5:]:
                icon = {"resolved": "✅", "partial": "⚠️", "failed": "❌"}.get(
                    evt.get("status", ""), "•"
                )
                ts = evt.get("timestamp", "")[:16]
                fc = evt.get("failure_class", "?")
                cmd_preview = (evt.get("cmd", ""))[:40]
                lines.append(f"{icon} `{ts}` {fc} — `{cmd_preview}`")
        else:
            lines += ["", "_No heal events recorded yet._"]

        await self.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")

    # ------------------------------------------------------------------ #
    #  Main fail-dispatch entry point (called from _handle_cli_command)   #
    # ------------------------------------------------------------------ #

    async def _heal_failure(self, ctx: FailureContext) -> None:
        """Entry point: decide whether to auto-heal or show the keyboard.

        Must be awaited from within _handle_cli_command when a failure is
        detected in the command response.
        """
        # Initialise state lazily if _init_autoheal_state was not called
        if not hasattr(self, "_active_heals"):
            self._init_autoheal_state()

        # Dedup guard: avoid two concurrent heals for the same target
        dedup_key = f"{ctx.user_id}:{ctx.host or 'global'}"
        existing = self._active_heals.get(dedup_key)
        if existing and not existing.done():
            await self.send_message(
                ctx.chat_id,
                f"⏳ Already healing `{ctx.host or 'this target'}` — please wait.",
                parse_mode="Markdown",
            )
            return

        sm = self._get_session_manager_safe()
        autoheal_on = False
        if sm:
            session = sm.get_or_create_session(ctx.chat_id, ctx.user_id, False)
            autoheal_on = getattr(session, "autoheal_enabled", False)

        if autoheal_on:
            # Silent auto-fix: send progress message, run fix in background task
            progress_msg = await self._send_progress_message(
                ctx.chat_id,
                f"🔧 Auto-Heal running for *{_CLASS_BADGE[ctx.failure_class]}*…",
            )
            task = asyncio.create_task(
                self._autofix_with_report(ctx, progress_msg)
            )
            self._active_heals[dedup_key] = task
        else:
            # Manual mode: show error badge + 3-button keyboard
            self._pending_heal_ctx[ctx.user_id] = ctx
            await self._send_failure_with_keyboard(ctx)

    async def _autofix_with_report(
        self,
        ctx: FailureContext,
        progress_msg_id: Optional[int],
    ) -> None:
        """Run fix, edit the progress message with result, optionally retry."""
        try:
            result = await self._run_autofix(ctx)
            self._record_heal_event(ctx, result.status)
            await self._report_heal_result(ctx, result, progress_msg_id)
        except Exception:  # noqa: BLE001 — we truly want to catch everything here
            logger.exception("autoheal: unhandled exception in _autofix_with_report")
            await self._edit_or_send(
                ctx.chat_id,
                progress_msg_id,
                "❌ Auto-Heal encountered an internal error. Use `/autoheal status` for details.",
            )

    # ------------------------------------------------------------------ #
    #  Fix execution                                                       #
    # ------------------------------------------------------------------ #

    async def _run_autofix(self, ctx: FailureContext) -> HealResult:
        """Dispatch to the correct repair strategy based on failure class.

        Caps at _MAX_FIX_ATTEMPTS and escalates instead of infinite-looping.
        """
        if ctx.attempt_count >= _MAX_FIX_ATTEMPTS:
            return HealResult(
                status="failed",
                message=(
                    f"❌ Reached maximum fix attempts ({_MAX_FIX_ATTEMPTS}) for "
                    f"*{_CLASS_BADGE[ctx.failure_class]}*.\n"
                    "Diagnostic dump follows — please review manually."
                ),
                should_retry=False,
                detail="max_attempts_exceeded",
            )

        ctx.attempt_count += 1
        # Lazy-import to keep the mixin loading fast
        from navig.selfheal.ssh_healer import SSHHealer
        healer = SSHHealer()
        host = ctx.host or "unknown"

        fc = ctx.failure_class

        if fc == FailureClass.SSH_HOSTKEY_UNKNOWN:
            return await healer.keyscan_and_trust(host)

        if fc == FailureClass.SSH_AUTH_FAIL:
            return await healer.ensure_ssh_key(host)

        if fc == FailureClass.SSH_TRANSPORT_FAIL:
            return await healer.probe_ssh_transport(host)

        if fc == FailureClass.DB_PERMISSION_DENY:
            # Credential changes always require human review — never auto-fix
            cfg_hint = self._get_navig_config_path()
            return HealResult(
                status="partial",
                message=(
                    "🛢 *DB_PERMISSION_DENY* — Credentials must be reviewed manually.\n\n"
                    f"Check your database connection settings:\n`{cfg_hint}`\n\n"
                    "Verify the username / password and that the user has the "
                    "required grants on this database."
                ),
                should_retry=False,
            )

        if fc == FailureClass.CMD_NOT_FOUND:
            cmd_word = self._extract_missing_cmd(ctx.stderr)
            return HealResult(
                status="failed",
                message=(
                    f"🔍 `{cmd_word}` is not installed on `{host}`.\n\n"
                    "Install it on the remote server first, then retry your command."
                ),
                should_retry=False,
            )

        if fc == FailureClass.TIMEOUT:
            return HealResult(
                status="partial",
                message=(
                    "⌛ The command timed out. Options:\n"
                    "• Increase timeout: `NAVIG_SSH_TIMEOUT=60 navig run …`\n"
                    "• Check if the command is still running on the server\n"
                    "• Verify the server is not under heavy load"
                ),
                should_retry=False,
            )

        # UNKNOWN — optionally trigger Hive Mind
        return await self._handle_unknown_failure(ctx)

    async def _handle_unknown_failure(self, ctx: FailureContext) -> HealResult:
        """Handle FailureClass.UNKNOWN — optionally escalate to Hive Mind PR."""
        sm = self._get_session_manager_safe()
        hive_on = False
        if sm:
            session = sm.get_or_create_session(ctx.chat_id, ctx.user_id, False)
            hive_on = getattr(session, "autoheal_hive_enabled", False)

        if not hive_on:
            return HealResult(
                status="failed",
                message=(
                    "❓ *UNKNOWN failure* — could not classify this error.\n\n"
                    "Enable Hive Mind to open a GitHub fix PR:\n"
                    "`/autoheal hive on`\n\n"
                    f"Raw error:\n```\n{ctx.stderr[:600]}\n```"
                ),
                should_retry=False,
            )

        # Hive Mind enabled → attempt PR submission
        try:
            from navig.selfheal.heal_pr_submitter import HealPRSubmitter
            submitter = HealPRSubmitter()
            patch_text = f"# Observed error\n{ctx.stderr[:1000]}"
            pr_url = submitter.submit_heal_pr(
                failure_class=ctx.failure_class.value,
                original_cmd=ctx.original_cmd,
                stderr=ctx.stderr,
                exit_code=ctx.exit_code,
                patch_text=patch_text,
                host=ctx.host,
            )
            return HealResult(
                status="partial",
                message=(
                    "🐝 *Hive Mind* submitted a GitHub PR for this failure.\n\n"
                    f"[View PR]({pr_url})"
                ),
                pr_url=pr_url,
                should_retry=False,
            )
        except ValueError as exc:
            # NAVIG_GITHUB_TOKEN not set
            return HealResult(
                status="failed",
                message=f"🐝 *Hive Mind* is enabled but `NAVIG_GITHUB_TOKEN` is not set.\n_{exc}_",
                should_retry=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("autoheal: hive mind PR submission failed")
            # Fallback: store patch locally
            try:
                from navig.selfheal.heal_pr_submitter import HealPRSubmitter
                submitter = HealPRSubmitter()
                patch_path = submitter.store_pending_patch(
                    failure_class=ctx.failure_class.value,
                    original_cmd=ctx.original_cmd,
                    stderr=ctx.stderr,
                    exit_code=ctx.exit_code,
                    patch_text=f"# Observed error\n{ctx.stderr[:1000]}",
                    host=ctx.host,
                )
                return HealResult(
                    status="partial",
                    message=(
                        "🐝 *Hive Mind* could not reach GitHub (API may be down).\n"
                        f"Patch saved locally: `{patch_path.name}`\n"
                        "It will be retried on next bot restart."
                    ),
                    should_retry=False,
                    detail=str(exc),
                )
            except Exception:
                logger.exception("autoheal: local patch storage also failed")
                return HealResult(
                    status="failed",
                    message="🐝 *Hive Mind* failed and local storage also failed. See logs.",
                    should_retry=False,
                )

    # ------------------------------------------------------------------ #
    #  Investigate / Explain handlers                                      #
    # ------------------------------------------------------------------ #

    async def _run_investigate(self, ctx: FailureContext) -> str:
        """Run diagnostics and return a structured report string."""
        lines = [
            "🔍 *Diagnostic Report*",
            "",
            f"*Class:* {_CLASS_BADGE[ctx.failure_class]}",
            f"*Host:* `{ctx.host or 'unknown'}`",
            f"*Exit code:* `{ctx.exit_code}`",
            f"*Command:* `{ctx.original_cmd[:120]}`",
            "",
            "─── *Error output* ───",
            f"```\n{ctx.stderr[:600]}\n```",
        ]

        # Optionally enrich with ErrorResolution analysis
        try:
            from navig.modules.error_resolution import analyze_error
            solutions = analyze_error(ctx.original_cmd, ctx.exit_code, ctx.stderr)
            if solutions:
                lines += ["", "─── *Suggested solutions* ───"]
                for sol in solutions[:3]:
                    lines.append(f"• {getattr(sol, 'description', str(sol))}")
        except Exception:
            pass  # Best-effort enrichment — never block the report

        # Append TCP probe for SSH classes
        if ctx.failure_class in (
            FailureClass.SSH_AUTH_FAIL,
            FailureClass.SSH_HOSTKEY_UNKNOWN,
            FailureClass.SSH_TRANSPORT_FAIL,
        ) and ctx.host:
            try:
                from navig.selfheal.ssh_healer import SSHHealer
                healer = SSHHealer()
                reachable = await healer._tcp_probe(ctx.host, 22)
                ssh_status = "✅ reachable" if reachable else "❌ unreachable"
                lines += ["", f"*SSH port 22 on `{ctx.host}`:* {ssh_status}"]
            except Exception:
                pass

        return "\n".join(lines)

    async def _run_explain(self, ctx: FailureContext) -> str:
        """Return a plain-English explanation of the failure."""
        badge = _CLASS_BADGE[ctx.failure_class]
        explanation = _CLASS_EXPLANATION[ctx.failure_class]

        lines = [
            f"📖 *What happened — {badge}*",
            "",
            explanation,
            "",
        ]

        # Failure-class-specific advice
        if ctx.failure_class == FailureClass.SSH_HOSTKEY_UNKNOWN:
            host = ctx.host or "the server"
            lines += [
                "*How to fix:*",
                f"Tap 🔧 Auto-Fix to run `ssh-keyscan` and trust `{host}` automatically.",
                f"Or run manually:\n`ssh-keyscan -H {host} >> ~/.ssh/known_hosts`",
            ]
        elif ctx.failure_class == FailureClass.SSH_AUTH_FAIL:
            lines += [
                "*How to fix:*",
                "Tap 🔧 Auto-Fix — NAVIG will check your SSH key and guide you if one needs generating.",
            ]
        elif ctx.failure_class == FailureClass.SSH_TRANSPORT_FAIL:
            lines += [
                "*How to fix:*",
                "Tap 🔧 Auto-Fix to probe the SSH port and retry the connection.",
                "Or check that the SSH daemon is running on the server.",
            ]
        elif ctx.failure_class == FailureClass.DB_PERMISSION_DENY:
            cfg_hint = self._get_navig_config_path()
            lines += [
                "*How to fix:*",
                f"Check your database credentials at:\n`{cfg_hint}`",
            ]

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Keyboard building and callback dispatch                             #
    # ------------------------------------------------------------------ #

    async def _send_failure_with_keyboard(self, ctx: FailureContext) -> None:
        """Send the failure badge + 3-button heal keyboard as one message."""
        badge = _CLASS_BADGE[ctx.failure_class]
        explanation = _CLASS_EXPLANATION[ctx.failure_class]
        text = (
            f"❌ *Command failed — {badge}*\n\n"
            f"{explanation}\n\n"
            "Choose an action:"
        )

        keyboard = self._build_heal_keyboard(ctx)
        await self.send_message(ctx.chat_id, text, keyboard=keyboard, parse_mode="Markdown")

    def _build_heal_keyboard(
        self, ctx: FailureContext
    ) -> List[List[Dict[str, str]]]:
        """Build the 3-button heal keyboard using the CallbackStore pattern."""
        from navig.gateway.channels.telegram_keyboards import (
            _short_hash,
            CallbackEntry,
            get_callback_store,
        )

        store = get_callback_store()
        # Use ctx fields as hash input for deterministic short keys
        ctx_sig = f"{ctx.user_id}:{ctx.failure_class}:{ctx.original_cmd}"
        key = _short_hash(ctx_sig)

        # Store a minimal entry for each action (action prefix used for dispatch)
        for action_prefix in ("heal_fix", "heal_diag", "heal_explain"):
            cb_key = f"{action_prefix}:{key}"
            store.put(
                cb_key,
                CallbackEntry(
                    action=action_prefix,
                    user_message=ctx.original_cmd,
                    ai_response=ctx.stderr[:3000],  # reuse ai_response field for stderr
                    category="error",
                    extra={
                        "failure_class": ctx.failure_class.value,
                        "host": ctx.host or "",
                        "exit_code": ctx.exit_code,
                    },
                ),
            )

        return [
            [
                {"text": "🔧 Auto-Fix", "callback_data": f"heal_fix:{key}"},
                {"text": "🔍 Investigate", "callback_data": f"heal_diag:{key}"},
            ],
            [
                {"text": "📖 Explain", "callback_data": f"heal_explain:{key}"},
            ],
        ]

    async def _dispatch_heal_callback(
        self,
        action: str,         # "heal_fix", "heal_diag", or "heal_explain"
        cb_key: str,         # full "heal_fix:<hash>" string
        chat_id: int,
        user_id: int,
        cb_id: str,          # Telegram callback_query id for answering
    ) -> None:
        """Called by CallbackHandler.handle() for heal_* prefixed callbacks."""
        from navig.gateway.channels.telegram_keyboards import get_callback_store
        store = get_callback_store()
        entry = store.get(cb_key)

        if not entry:
            await self._answer_callback(cb_id, "⏳ Button expired — rerun the command.")
            return

        # Reconstitute FailureContext from stored data
        fc_str = entry.extra.get("failure_class", "UNKNOWN")
        try:
            fc = FailureClass(fc_str)
        except ValueError:
            fc = FailureClass.UNKNOWN

        ctx = FailureContext(
            original_cmd=entry.user_message,
            chat_id=chat_id,
            user_id=user_id,
            failure_class=fc,
            stderr=entry.ai_response,   # stored in ai_response field
            exit_code=entry.extra.get("exit_code", 0),
            host=entry.extra.get("host") or None,
        )

        if action == "heal_fix":
            await self._answer_callback(cb_id, "🔧 Running fix…")
            progress_msg = await self._send_progress_message(
                chat_id, f"🔧 Fixing *{_CLASS_BADGE[fc]}*…"
            )
            result = await self._run_autofix(ctx)
            self._record_heal_event(ctx, result.status)
            await self._report_heal_result(ctx, result, progress_msg)

        elif action == "heal_diag":
            await self._answer_callback(cb_id, "🔍 Running diagnostics…")
            report = await self._run_investigate(ctx)
            await self.send_message(chat_id, report, parse_mode="Markdown")

        elif action == "heal_explain":
            await self._answer_callback(cb_id, "📖 Loading explanation…")
            explanation = await self._run_explain(ctx)
            await self.send_message(chat_id, explanation, parse_mode="Markdown")

    # ------------------------------------------------------------------ #
    #  Result reporting                                                    #
    # ------------------------------------------------------------------ #

    async def _report_heal_result(
        self,
        ctx: FailureContext,
        result: HealResult,
        progress_msg_id: Optional[int],
    ) -> None:
        """Edit the in-progress message with the final result, then retry if resolved."""
        if result.status == "resolved":
            text = f"✅ *Fixed!* {result.message}"
            await self._edit_or_send(ctx.chat_id, progress_msg_id, text)
            if result.should_retry:
                # Retry the original command automatically
                await asyncio.sleep(0.5)  # brief pause so user sees the success message
                await self.send_message(
                    ctx.chat_id, "🔁 Retrying your command…", parse_mode=None
                )
                if hasattr(self, "on_message") and self.on_message:
                    try:
                        response = await self.on_message(
                            channel="telegram",
                            user_id=str(ctx.user_id),
                            message=f"navig {ctx.original_cmd}",
                            metadata=None,
                        )
                        if response:
                            await self.send_message(ctx.chat_id, response, parse_mode=None)
                    except Exception:
                        logger.exception("autoheal: retry failed")
                        await self.send_message(
                            ctx.chat_id,
                            "⚠️ Retry also failed — please try again manually.",
                            parse_mode=None,
                        )

        elif result.status == "partial":
            icon = "⚠️"
            text = f"{icon} *Partial fix.* {result.message}"
            if result.pr_url:
                text += f"\n\n🐝 [Hive Mind PR]({result.pr_url})"
            await self._edit_or_send(ctx.chat_id, progress_msg_id, text)

        else:  # "failed"
            text = f"❌ *Fix failed.* {result.message}"
            # Attach full diagnostic if hive is not enabled
            sm = self._get_session_manager_safe()
            hive_on = False
            if sm:
                session = sm.get_or_create_session(ctx.chat_id, ctx.user_id, False)
                hive_on = getattr(session, "autoheal_hive_enabled", False)
            if not hive_on and ctx.failure_class == FailureClass.UNKNOWN:
                text += "\n\nEnable Hive Mind to escalate: `/autoheal hive on`"
            await self._edit_or_send(ctx.chat_id, progress_msg_id, text)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _send_progress_message(self, chat_id: int, text: str) -> Optional[int]:
        """Send a message and return its message_id for later editing."""
        try:
            result = await self._api_call(
                "sendMessage",
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
            )
            if isinstance(result, dict):
                return result.get("message_id")
        except Exception:
            logger.exception("autoheal: _send_progress_message failed")
        return None

    async def _edit_or_send(
        self, chat_id: int, message_id: Optional[int], text: str
    ) -> None:
        """Edit an existing message or send a new one as fallback."""
        if message_id:
            try:
                await self._api_call(
                    "editMessageText",
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="Markdown",
                )
                return
            except Exception:
                pass  # fall back to a new message
        await self.send_message(chat_id, text, parse_mode="Markdown")

    async def _answer_callback(self, cb_id: str, text: str) -> None:
        """Answer a callback_query (shows toast notification)."""
        try:
            await self._api_call("answerCallbackQuery",
                                  callback_query_id=cb_id, text=text[:200])
        except Exception:
            pass

    def _get_session_manager_safe(self) -> Any:
        """Return the SessionManager if available, else None."""
        try:
            if hasattr(self, "_has_feature") and self._has_feature("sessions"):
                from navig.gateway.channels.telegram_features import get_session_manager
                return get_session_manager()
        except Exception:
            pass
        return None

    def _record_heal_event(self, ctx: FailureContext, status: str) -> None:
        """Persist a HealEvent to session.heal_history (last 5 only)."""
        sm = self._get_session_manager_safe()
        if not sm:
            return
        try:
            session = sm.get_or_create_session(ctx.chat_id, ctx.user_id, False)
            event = HealEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                failure_class=ctx.failure_class.value,
                status=status,
                cmd=ctx.original_cmd[:80],
            ).to_dict()
            history = getattr(session, "heal_history", [])
            history = (history + [event])[-5:]   # keep last 5
            sm.update_settings(ctx.chat_id, ctx.user_id, heal_history=history)
        except Exception:
            logger.exception("autoheal: _record_heal_event failed")

    @staticmethod
    def _get_navig_config_path() -> str:
        """Return a human-readable path hint for NAVIG config."""
        try:
            from navig.platform.paths import config_dir
            return str(config_dir() / "config.yaml")
        except Exception:
            return "config.yaml"

    @staticmethod
    def _extract_missing_cmd(stderr: str) -> str:
        """Try to extract the missing binary name from stderr."""
        m = re.search(r"(['\"`]?)(\S+)\1[:\s]+command not found", stderr)
        if m:
            return m.group(2)
        m = re.search(r"(['\"`])(\S+)\1.*No such file", stderr)
        if m:
            return m.group(2)
        return "command"
