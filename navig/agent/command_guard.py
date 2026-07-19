"""Defense-in-depth guard for the agent's ``command`` shell action.

The agent can emit a ``command`` action whose ``cmd`` is executed via ``subprocess.run(...,
shell=True)`` (dispatched in ``action_registry`` and ``conversational_legacy``). Because the
agent processes untrusted input — incoming Telegram messages, fetched web content — a
prompt-injected or simply mistaken agent could run a catastrophic command on the operator's
machine, with no gate today (PRODUCTION_AUDIT P-C).

This is **not** a complete sandbox. The full fix — routing every command through the Telegram
approval gate and/or an allowlist — is a security-*policy* decision for the operator. This
module provides the two universally-safe layers that need no policy call and never block a
legitimate command:

1. **Kill-switch** — ``executor.command_enabled`` (default ``true``). A security-conscious
   operator can set it ``false`` to disable agent shell execution entirely.
2. **Catastrophe block** — a small set of NEVER-legitimate patterns (root-filesystem wipe,
   raw block-device overwrite, filesystem format, fork bomb). Each is precise enough that no
   real command trips it; obfuscated variants can still slip past — that is what the approval
   gate is for.

Both dispatch sites call :func:`guard_agent_command` immediately before executing.
"""

from __future__ import annotations

import re

__all__ = ["CommandBlocked", "guard_agent_command"]


class CommandBlocked(RuntimeError):
    """Raised when the agent's ``command`` action is refused before execution."""


# Block devices whose raw overwrite / format destroys a disk.
_BLOCK_DEV = r"/dev/(?:sd|nvme|vd|hd|disk|mmcblk|xvd)"

# Never-legitimate, high-precision. Order-independent; the first match wins.
_CATASTROPHIC: list[tuple[re.Pattern[str], str]] = [
    # classic fork bomb  :(){ :|:& };:
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    # dd if=... of=/dev/sdX  — raw write straight to a disk
    (re.compile(rf"\bdd\b[^\n]*\bof=\s*{_BLOCK_DEV}", re.IGNORECASE), "raw write to a block device"),
    # mkfs.ext4 /dev/sdX  — reformat a device
    (re.compile(rf"\bmkfs(?:\.\w+)?\b[^\n]*\s{_BLOCK_DEV}", re.IGNORECASE), "format of a block device"),
    # > /dev/sdX  — clobber a disk via redirect
    (re.compile(rf">\s*{_BLOCK_DEV}", re.IGNORECASE), "redirect over a block device"),
]


def _is_root_rm(cmd: str) -> bool:
    """True for a recursive+force ``rm`` that targets the root filesystem (``/`` or ``/*``)."""
    if not re.search(r"\brm\b", cmd):
        return False
    has_recursive = re.search(r"(?:^|\s)-[a-zA-Z]*r|--recursive|--no-preserve-root", cmd) is not None
    has_force = re.search(r"(?:^|\s)-[a-zA-Z]*f|--force", cmd) is not None
    # a bare "/" or "/*" argument (optionally quoted) — NOT "/home", "/tmp/x", etc. The
    # terminator allows a comment / next-token / redirect so `rm -rf / #x` can't sneak past.
    targets_root = re.search(r"""(?:^|\s)['"]?/['"]?\s*\*?\s*(?:$|[\s;&|#])""", cmd) is not None
    return has_recursive and has_force and targets_root


def _shell_enabled() -> bool:
    """Read the ``executor.command_enabled`` kill-switch (default ON; coerces string configs)."""
    try:
        from navig.config import get_config_manager

        executor = get_config_manager().global_config.get("executor", {}) or {}
    except Exception:  # noqa: BLE001 — never let a config hiccup crash the guard open OR closed
        return True
    value = executor.get("command_enabled", True)
    if isinstance(value, str):
        # `navig config set` stores raw strings; treat the usual falsey words as off.
        return value.strip().lower() not in {"false", "0", "no", "off", "disabled", ""}
    return bool(value)


def guard_agent_command(cmd: str) -> None:
    """Refuse the command (raise :class:`CommandBlocked`) if shell exec is disabled or the
    command matches a never-legitimate catastrophic pattern. Otherwise return ``None``."""
    if not _shell_enabled():
        raise CommandBlocked(
            "Agent shell execution is disabled (set executor.command_enabled=true to allow)."
        )
    stripped = (cmd or "").strip()
    if not stripped:
        return
    if _is_root_rm(stripped):
        raise CommandBlocked(f"Refused a catastrophic command (recursive delete of /): {stripped[:120]!r}")
    for pattern, label in _CATASTROPHIC:
        if pattern.search(stripped):
            raise CommandBlocked(f"Refused a catastrophic command ({label}): {stripped[:120]!r}")
