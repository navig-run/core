"""Per-tool rights for the Telegram Business conversation layer.

The owner controls, for EACH AI tool, who may trigger it in a business chat:

    "owner"  — only you (the business-account owner)   [default]
    "both"   — you AND the counterparty (the other person)
    "off"    — disabled entirely

⚠️ HARD INVARIANT (never configurable): these policies govern ONLY the
**sandboxed, no-tools text/AI helpers** (translate / summarize / context /
explain / ocr / transcribe / link-download). System/CLI/deck/skill control is
**always owner-only** and is NOT in this table — a counterparty can never reach
the system no matter what these toggles say. "both" simply lets the other person
run, say, a translation on a message; it grants zero system access.

Config keys (non-secret, global):
    telegram.business.enabled                  master on/off
    telegram.business.tools.<tool>.who         owner | both | off
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The tools a business conversation can run. All are sandboxed text ops or
# read-only media extraction — none can touch the system.
BUSINESS_TOOLS: tuple[str, ...] = (
    "translate",   # 🌍 translate the replied/marked message
    "summarize",   # 📋 summarize
    "context",     # 🤔 explain / analyze context
    "explain",     # explain simply
    "ocr",         # read text from an image
    "transcribe",  # voice/video → text
    "download",    # ⬇️ fetch a tiktok/youtube link
)

_WHO = ("owner", "both", "off")
_DEFAULT_WHO = "owner"

CFG_MASTER = "telegram.business.enabled"
_CFG_TOOL = "telegram.business.tools.{tool}.who"


def _cfg():
    from navig.core import Config
    return Config()


# ── Master gate ──────────────────────────────────────────────────────────────


def business_enabled() -> bool:
    try:
        return bool(_cfg().get(CFG_MASTER, False))
    except Exception:  # noqa: BLE001
        return False


def set_business_enabled(value: bool) -> None:
    cfg = _cfg()
    cfg.set(CFG_MASTER, bool(value), scope="global")
    cfg.save(scope="global")


def arming_blocked_reason() -> str | None:
    """Refuse-to-arm guard. Returns a reason string if the Business/AI features
    must NOT run (auth not enforced → anyone could control the bot), else None.

    Mirrors the bot channel's owner gate: require_auth ON + allowed_users non-empty.
    """
    try:
        cfg = _cfg()
        tg = cfg.get("telegram", {}) or {}
        require_auth = bool(tg.get("require_auth", True))
        allowed = tg.get("allowed_users") or []
        if not require_auth:
            return "telegram.require_auth is OFF — anyone could control the bot. Enable it first."
        if not allowed:
            return "telegram.allowed_users is empty — set your own user id as the owner first."
        return None
    except Exception:  # noqa: BLE001
        return "could not read telegram auth config"


# ── Per-tool policy ──────────────────────────────────────────────────────────


def tool_policy(tool: str) -> str:
    """'owner' | 'both' | 'off' for *tool* (default owner-only)."""
    try:
        v = str(_cfg().get(_CFG_TOOL.format(tool=tool), _DEFAULT_WHO) or _DEFAULT_WHO).lower()
        return v if v in _WHO else _DEFAULT_WHO
    except Exception:  # noqa: BLE001
        return _DEFAULT_WHO


def set_tool_policy(tool: str, who: str) -> None:
    who = (who or "").lower()
    if tool not in BUSINESS_TOOLS:
        raise ValueError(f"unknown tool {tool!r}; one of {BUSINESS_TOOLS}")
    if who not in _WHO:
        raise ValueError(f"who must be one of {_WHO}, got {who!r}")
    cfg = _cfg()
    cfg.set(_CFG_TOOL.format(tool=tool), who, scope="global")
    cfg.save(scope="global")


def is_tool_enabled(tool: str) -> bool:
    return tool_policy(tool) != "off"


def can_use(tool: str, *, is_owner: bool) -> bool:
    """Whether *tool* may be triggered by this actor.

    The system/control invariant is enforced elsewhere — this only gates the
    sandboxed AI helpers. A non-owner is allowed ONLY when the policy is 'both'.
    """
    policy = tool_policy(tool)
    if policy == "off":
        return False
    if policy == "both":
        return True
    return is_owner  # 'owner'


def all_policies() -> dict[str, str]:
    """Snapshot for the deck/CLI: ``{tool: who}`` for every business tool."""
    return {t: tool_policy(t) for t in BUSINESS_TOOLS}
