"""Turn heartbeat findings into an alert a person can read at a glance.

``HeartbeatResult.issues_found`` stays a MACHINE record — ``[HIGH] LLM mode
'big_tasks' → anthropic:claude-opus-4-8 is auth (auth failed (401 …)). Fix: …`` —
because three readers parse it: ``heartbeat.triage`` (which needs the "LLM mode"
subject and the 401/expired wording to keep provider conditions out of
remediate missions), ``navig heartbeat history``, and the deck's heartbeat route.
Rewriting it would silently change which findings become missions.

So the human wording is produced HERE, only for the notification: the status
code becomes a plain phrase (the old text read "is auth (auth failed …)"), the
``[HIGH]`` tag becomes a coloured dot, and identifiers are wrapped in backticks so
they render as code — and so the markdown renderer never reads the ``_`` in
``big_tasks`` as the start of italics.
"""

from __future__ import annotations

import html
import re
from typing import Any


def _tr(key: str, default: str, **fields: Any) -> str:
    """One string in the operator's language; *default* (English) when unresolved.

    Never raises: an alert must go out even when the locale layer cannot load."""
    try:
        from navig.core import i18n  # noqa: PLC0415

        text = i18n.t(key, **fields)
        if text != key:
            return text
    except Exception:  # noqa: BLE001
        pass
    try:
        return default.format(**fields)
    except (KeyError, IndexError, ValueError):
        return default

#: liveness status code → what it means to the operator.
_STATUS_PHRASE = {
    "auth": "key rejected",
    "nokey": "no API key configured",
    "dead": "model retired",
    "unreachable": "unreachable",
    "slow": "too slow to answer",
    "transient": "provider busy",
    "error": "check failed",
}

_SEVERITY_DOT = {"CRITICAL": "🔴", "HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}

# The exact shape heartbeat.runner._check_llm_liveness writes.
_LIVENESS_RE = re.compile(
    r"^\[(?P<sev>[A-Z]+)\]\s*(?P<subject>.+?)\s*→\s*(?P<target>\S+)\s+is\s+"
    r"(?P<status>[a-z_]+)\s*\((?P<detail>.*)\)\.\s*Fix:\s*(?P<fix>.+?)\s*$",
    re.DOTALL,
)
_SEV_PREFIX_RE = re.compile(r"^\[(?P<sev>[A-Z]+)\]\s*")
_QUOTED_NAME_RE = re.compile(r"^(?P<kind>LLM mode|routing tier)\s+'(?P<name>[^']+)'$")
_CATALOG_RE = re.compile(r"^(?P<name>.+?)'s first catalog model$")


def _subject(subject: str) -> str:
    m = _QUOTED_NAME_RE.match(subject)
    if m:
        kind = (_tr("heartbeat.subject.mode", "Mode") if m.group("kind") == "LLM mode"
                else _tr("heartbeat.subject.tier", "Tier"))
        return f"{kind} `{m.group('name')}`"
    m = _CATALOG_RE.match(subject)
    if m:
        return _tr("heartbeat.subject.catalog", "`{name}` catalog", name=m.group("name"))
    return subject


def _condition(status: str, detail: str) -> str:
    phrase = _tr(f"heartbeat.status.{status}", _STATUS_PHRASE.get(status, status))
    detail = detail.strip()
    if not detail:
        return phrase
    # "auth failed (401 — key/token invalid or expired)" → keep only the specific part.
    inner = re.search(r"\(([^()]*)\)\s*$", detail)
    specific = inner.group(1).strip() if inner else detail
    if specific.lower() == phrase.lower():
        return phrase
    return f"{phrase} ({specific})"


def humanize_issue(issue: str) -> str:
    """One finding as a markdown line (or two: the problem, then the fix)."""
    text = str(issue or "").strip()
    m = _LIVENESS_RE.match(text)
    if m:
        dot = _SEVERITY_DOT.get(m.group("sev"), "🔴")
        line = (
            f"{dot} {_subject(m.group('subject'))} → `{m.group('target')}` — "
            f"{_condition(m.group('status'), m.group('detail'))}"
        )
        return f"{line}\n{_tr('heartbeat.fix', 'Fix:')} `{m.group('fix').strip()}`"
    # Free-text finding from the heartbeat agent: keep its words, swap the tag.
    sev = _SEV_PREFIX_RE.match(text)
    if sev:
        dot = _SEVERITY_DOT.get(sev.group("sev"), "•")
        return f"{dot} {text[sev.end():].strip()}"
    return f"• {text}"


def issues_alert(issues: list[str]) -> tuple[str, str]:
    """``(title, markdown body)`` for an alert about *issues*."""
    items = [i for i in (str(x or "").strip() for x in issues) if i]
    n = len(items)
    title = (_tr("heartbeat.alert.title_one", "⚠️ Health check: {n} problem", n=n) if n == 1
             else _tr("heartbeat.alert.title_many", "⚠️ Health check: {n} problems", n=n))
    lines = [humanize_issue(i) for i in items]
    # Two-line findings (problem + fix) read better with air between them.
    sep = "\n\n" if any("\n" in ln for ln in lines) else "\n"
    return title, sep.join(lines)


#: Liveness statuses a model swap actually fixes. "slow"/"transient" are passing
#: conditions; "unreachable" is often the operator's own network.
_REPAIRABLE_STATUSES = frozenset({"auth", "nokey", "dead"})

#: Callback prefix the Telegram keyboard handler dispatches on.
REPAIR_CALLBACK_PREFIX = "hbfix:"


def repairable_modes(issues: list[str]) -> list[str]:
    """Mode names (never tiers or catalogs) whose finding a model swap fixes.

    Parsed from the same machine record the alert is built from, so the button
    can only ever offer to fix a problem the message lists.
    """
    out: list[str] = []
    for issue in issues or []:
        m = _LIVENESS_RE.match(str(issue or "").strip())
        if not m or m.group("status") not in _REPAIRABLE_STATUSES:
            continue
        q = _QUOTED_NAME_RE.match(m.group("subject"))
        if q and q.group("kind") == "LLM mode" and q.group("name") not in out:
            out.append(q.group("name"))
    return out


def repair_keyboard(issues: list[str]) -> list[list[dict[str, str]]] | None:
    """The one-tap fix button for *issues*, or ``None`` when nothing is fixable."""
    modes = repairable_modes(issues)
    if not modes:
        return None
    # Telegram caps callback_data at 64 bytes; keep as many whole names as fit.
    while len((REPAIR_CALLBACK_PREFIX + ",".join(modes)).encode()) > 64 and len(modes) > 1:
        modes = modes[:-1]
    data = REPAIR_CALLBACK_PREFIX + ",".join(modes)
    label = (_tr("heartbeat.button.switch_one", "🔁 Switch to a working model") if len(modes) == 1
             else _tr("heartbeat.button.switch_many", "🔁 Switch these to working models"))
    return [[{"text": label, "callback_data": data}]]


# ── "How to fix" + "Check again" ─────────────────────────────────────────────
#
# The switch button fixes the ROUTE; it cannot fix the KEY. An expired Anthropic
# key needs a new key, and the alert only ever said "key rejected" — so the two
# buttons below say what to do, and confirm afterwards whether it worked.

HOWTO_CALLBACK_PREFIX = "hbhow:"
RECHECK_CALLBACK_PREFIX = "hbchk:"


def _liveness_findings(issues: list[str]) -> list[dict[str, str]]:
    """``[{mode, provider, model, status}]`` for each MODE finding in *issues*."""
    out: list[dict[str, str]] = []
    for issue in issues or []:
        m = _LIVENESS_RE.match(str(issue or "").strip())
        if not m:
            continue
        q = _QUOTED_NAME_RE.match(m.group("subject"))
        if not q or q.group("kind") != "LLM mode":
            continue
        provider, _, model = m.group("target").partition(":")
        out.append({"mode": q.group("name"), "provider": provider, "model": model,
                    "status": m.group("status")})
    return out


def _fit(prefix: str, items: list[str]) -> str:
    """``prefix + ",".join(items)`` trimmed to Telegram's 64-byte callback cap."""
    items = list(items)
    while len((prefix + ",".join(items)).encode()) > 64 and len(items) > 1:
        items = items[:-1]
    return prefix + ",".join(items)


def alert_keyboard(issues: list[str]) -> list[list[dict[str, str]]] | None:
    """Every button the alert carries: switch (when a swap helps), then how-to-fix
    and check-again (whenever a mode finding is listed)."""
    rows = list(repair_keyboard(issues) or [])
    found = _liveness_findings(issues)
    if found:
        pairs = sorted({f"{f['status']}.{f['provider']}" for f in found})
        modes = list(dict.fromkeys(f["mode"] for f in found))
        rows.append([
            {"text": _tr("heartbeat.button.howto", "🔑 How to fix"),
             "callback_data": _fit(HOWTO_CALLBACK_PREFIX, pairs)},
            {"text": _tr("heartbeat.button.recheck", "🔄 Check again"),
             "callback_data": _fit(RECHECK_CALLBACK_PREFIX, modes)},
        ])
    return rows or None


#: status → (locale key, English steps). ``{provider}`` is the provider id.
_HOWTO_STEPS = {
    "auth": ("heartbeat.howto.auth",
             "The {provider} key was rejected (expired, revoked or out of credit).\n"
             "1. Create a new key in your {provider} account.\n"
             "2. Save it: <code>navig vault set {provider} &lt;new-key&gt;</code>\n"
             "3. Test it: <code>navig vault test {provider}</code>\n"
             "Then tap 🔄 Check again."),
    "nokey": ("heartbeat.howto.nokey",
              "No {provider} key is configured.\n"
              "1. Save one: <code>navig vault set {provider} &lt;key&gt;</code>\n"
              "2. Test it: <code>navig vault test {provider}</code>\n"
              "Or move the mode to a provider you have: tap 🔁 Switch."),
    "dead": ("heartbeat.howto.dead",
             "The {provider} model is retired — no key will bring it back.\n"
             "Tap 🔁 Switch, or pick one yourself: "
             "<code>navig mode set &lt;mode&gt; --provider &lt;p&gt; --model &lt;m&gt;</code>"),
}


def howto_text(callback_data: str) -> str:
    """HTML steps for the ``hbhow:`` button: one block per status+provider."""
    raw = callback_data[len(HOWTO_CALLBACK_PREFIX):] if callback_data.startswith(
        HOWTO_CALLBACK_PREFIX) else callback_data
    blocks: list[str] = []
    for pair in raw.split(","):
        status, _, provider = pair.partition(".")
        key, default = _HOWTO_STEPS.get(status, (
            "heartbeat.howto.other",
            "{provider}: run <code>navig vault test {provider}</code> and <code>navig heartbeat run</code> to see the detail.",
        ))
        blocks.append(_tr(key, default, provider=html.escape(provider or "?")))
    title = _tr("heartbeat.howto.title", "🔑 How to fix")
    return f"<b>{html.escape(title)}</b>\n\n" + "\n\n".join(blocks)


def recheck_text(results: list[dict[str, Any]]) -> tuple[str, bool]:
    """``(html, all_ok)`` for the ``hbchk:`` button, from ``probe_modes`` rows."""
    lines: list[str] = []
    all_ok = True
    for r in results:
        route = f"{r.get('provider')}:{r.get('model')}"
        target = f"<code>{html.escape(route)}</code>"
        name = f"{html.escape(_tr('heartbeat.subject.mode', 'Mode'))} <code>{html.escape(str(r.get('mode')))}</code>"
        if r.get("status") == "live":
            lines.append(f"✅ {name} → {target} — " + html.escape(_tr("heartbeat.recheck.ok", "working")))
        else:
            all_ok = False
            cond = _condition(str(r.get("status")), str(r.get("detail") or ""))
            lines.append(f"🔴 {name} → {target} — {html.escape(cond)}")
    head = (_tr("heartbeat.recheck.all_ok", "✅ Fixed — every mode answers again.") if all_ok and lines
            else _tr("heartbeat.recheck.still", "Still failing:") if lines
            else _tr("heartbeat.recheck.none", "Nothing to check."))
    return html.escape(head) + ("\n" + "\n".join(lines) if lines else ""), all_ok


def failure_alert(error: object) -> tuple[str, str]:
    """``(title, body)`` for a heartbeat run that could not complete."""
    return "⚠️ Health check could not run", str(error or "unknown error").strip()
