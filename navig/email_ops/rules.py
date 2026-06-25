"""Filter-rule matching for incoming email. A rule matches when ALL of its
specified conditions hold (case-insensitive)."""

from __future__ import annotations

from typing import Any


def needs_body(rules: list[dict[str, Any]]) -> bool:
    """True if any enabled rule inspects the body (so we fetch it)."""
    return any(r.get("enabled", True) and (r.get("body_words")) for r in rules)


def match(msg: dict[str, Any], rule: dict[str, Any]) -> bool:
    if not rule.get("enabled", True):
        return False
    frm = (msg.get("from") or "").lower()
    subject = (msg.get("subject") or "").lower()
    body = (msg.get("body") or msg.get("snippet") or "").lower()

    conds: list[bool] = []
    if rule.get("from"):
        conds.append(str(rule["from"]).lower() in frm)
    if rule.get("subject_contains"):
        conds.append(str(rule["subject_contains"]).lower() in subject)
    if rule.get("subject_exact"):
        conds.append(subject.strip() == str(rule["subject_exact"]).lower().strip())
    words = rule.get("body_words") or []
    if words:
        hay = subject + " " + body
        conds.append(any(str(w).lower() in hay for w in words if str(w).strip()))

    # A rule with no conditions never matches (avoids notifying on everything).
    return bool(conds) and all(conds)


def first_match(msg: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    for rule in rules:
        if match(msg, rule):
            return rule
    return None
