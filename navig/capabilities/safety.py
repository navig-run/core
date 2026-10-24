"""Approval-gate level for one CLI command path, decided from its verbs.

``navig_run`` executes installed CLI commands for an agent. The MCP approval gate needs a
level per call, and the catalogue carries none, so the level is derived here from the
command's own words — conservatively:

* **dangerous** — the command reaches other people or removes something: publish, post,
  send, upload, delete, revoke, connect an account, run a payment, …
* **safe**      — the command only reads: status, list, show, info, doctor, …
* **moderate**  — everything else (it writes local files, spends provider credits,
  renders media). Moderate is still gated; it is never "safe".

A word anywhere in the path counts, so ``navig social relay post`` is dangerous even
though ``relay`` alone is not. Unknown verbs fall to moderate, never to safe.
"""

from __future__ import annotations

#: Any of these words in a command path → the command can reach people or destroy data.
DANGEROUS_WORDS: frozenset[str] = frozenset(
    {
        "approve", "broadcast", "buy", "connect", "delete", "delete-all", "deploy",
        "disconnect", "dm", "drain", "fan-out", "forward", "kill", "pay", "post", "prune",
        "publish", "purge", "remove", "reply", "revoke", "rm", "rotate", "run", "send",
        "submit", "transfer", "uninstall", "upload", "wipe",
    }
)  # fmt: skip

#: The LAST word of a command path that marks a pure read.
READ_LEAVES: frozenset[str] = frozenset(
    {
        "catalog", "channels", "check", "comments", "devices",
        "doctor", "history", "info", "inspect", "journal", "list", "loudness", "parity",
        "plan", "probe", "projects", "queue", "receipts", "report", "search", "show",
        "stats", "status", "styles", "subscription", "trend", "voices",
    }
)  # fmt: skip


def classify(path: str) -> str:
    """``safe`` | ``moderate`` | ``dangerous`` for a ``navig …`` command path."""
    words = str(path or "").lower().split()
    if words and words[0] == "navig":
        words = words[1:]
    if not words:
        return "dangerous"
    if any(word in DANGEROUS_WORDS for word in words):
        return "dangerous"
    if words[-1] in READ_LEAVES:
        return "safe"
    return "moderate"
