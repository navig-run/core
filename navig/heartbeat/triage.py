"""Which heartbeat findings can an agent act on — and which are the operator's?

The heartbeat is an LLM run that reports free-text issues. The gateway used to
turn EVERY finding into a "Remediate health issues" mission, and the operator's
store shows what that bought: 84 missions, 233 issue lines, every one of them an
LLM-provider condition — an expired Anthropic key (158), a retired NVIDIA model
(75), an endpoint 503 (2). A shell agent cannot mint an API key or un-retire a
model, the router already falls back at request time, and the heartbeat alert
already told the operator. 80 of the 84 missions ended "approval denied or
timed out"; the 3 that ran rewrote the operator's LLM routing unasked.

So: provider/model conditions are INFORMATIONAL — reported once by the
heartbeat, never raised as a mission. Everything else (a host down, a disk
filling, a crashed service) stays ACTIONABLE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: The finding is ABOUT an LLM provider / model / route …
_SUBJECT_RE = re.compile(
    r"\b(llm mode|routing tier|provider|model|api key|api token|endpoint|credential)\b", re.I
)
#: … and describes a condition only the operator or the router can change.
_CONDITION_RE = re.compile(
    r"(unreachable|timed out|time-?out|connection (refused|reset|error)|"
    r"\b50[234]\b|\b40[134]\b|\b410\b|\b429\b|\beol\b|\bdead\b|\bgone\b|auth failed|unauthori[sz]ed|invalid or expired|"
    r"key/token|expired|retired|deprecated|not found|no longer|rate.?limit|quota|"
    r"insufficient (credit|balance|quota)|billing)",
    re.I,
)


@dataclass
class Triaged:
    actionable: list[str] = field(default_factory=list)
    informational: list[str] = field(default_factory=list)


def is_provider_condition(issue: str) -> bool:
    """A finding about an LLM provider/model that no shell agent can remedy."""
    text = str(issue or "")
    return bool(_SUBJECT_RE.search(text) and _CONDITION_RE.search(text))


def triage(issues: list[str]) -> Triaged:
    out = Triaged()
    for raw in issues:
        text = str(raw or "").strip()
        if not text:
            continue
        (out.informational if is_provider_condition(text) else out.actionable).append(text)
    return out
