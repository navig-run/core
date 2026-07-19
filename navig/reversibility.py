"""
Reversibility taxonomy for recorded operations (T-068, plan-evidence-ledger.md).

Every operation the recorder appends to ``operations.jsonl`` carries a
``reversibility`` label:

- ``green``  — undoable: ``undo_data`` was captured at execution time and
  ``navig undo`` knows how to replay it.
- ``yellow`` — compensable / conditionally reversible: there is a manual
  counter-action (start the service again, delete the uploaded copy), but
  NAVIG cannot replay it mechanically.
- ``red``    — irreversible: the side effect cannot be taken back (data
  deleted, message sent, arbitrary remote command).
- ``none``   — not applicable: a pure read (``read_query``) has no side
  effect, so there is nothing to reverse. Rendered dim, never counted as
  undoable.

The label is HONEST, never optimistic: green requires all three of
(a) an operation type ``navig undo`` has a strategy for, (b) captured
``undo_data``, and (c) no secret material involved. Anything less degrades
to the static table below. Unknown types are red — "irreversible until
proven otherwise" is the only safe default for an operator's hands.

Pure stdlib on purpose — imported from the recorder's hot write path and
from the CLI without dragging in heavy modules. Never import
``navig.operation_recorder`` here (the recorder imports us).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import Enum

__all__ = [
    "GREEN_CAPABLE_TYPES",
    "Reversibility",
    "classify",
    "compensation_hint",
    "is_sensitive_config_key",
    "label_glyph",
]


class Reversibility(str, Enum):
    """The reversibility label (three colours + ``none`` for pure reads)."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    #: No side effect to reverse — reserved for read-only operation types.
    NONE = "none"


#: Operation types ``navig undo`` has a replay strategy for. Green is only
#: ever granted inside this set — a label must never promise an undo the
#: engine cannot perform.
GREEN_CAPABLE_TYPES: frozenset[str] = frozenset(
    {
        "config_change",
        "host_switch",
        "file_create",
        "file_modify",
        "file_delete",
    }
)

#: Static classification when NO usable undo_data was captured — the
#: plan's "top ~15 verbs" table, keyed by OperationType value. Degrades
#: gracefully: unknown/future types fall through to RED.
_POTENTIAL: dict[str, Reversibility] = {
    "config_change": Reversibility.YELLOW,  # previous value may be re-set by hand
    "host_switch": Reversibility.YELLOW,  # switch back
    "file_create": Reversibility.YELLOW,  # created path is usually named in the command
    "file_modify": Reversibility.RED,  # previous content gone without a backup
    "file_delete": Reversibility.RED,  # data gone without a backup
    "file_upload": Reversibility.YELLOW,  # delete the remote copy
    "file_download": Reversibility.YELLOW,  # delete the local copy
    "tunnel_start": Reversibility.YELLOW,  # tunnel remove
    "tunnel_stop": Reversibility.YELLOW,  # tunnel run
    "service_restart": Reversibility.YELLOW,  # service state can be re-managed
    "docker_command": Reversibility.YELLOW,  # start/stop compensable; rm/prune are not
    "database_dump": Reversibility.YELLOW,  # delete the dump file
    "database_query": Reversibility.RED,  # may be a write; unknowable
    "remote_command": Reversibility.RED,
    "local_command": Reversibility.RED,
    "workflow_run": Reversibility.RED,  # composite side effects
    "other": Reversibility.RED,
    "read_query": Reversibility.NONE,  # pure read — nothing happened to reverse
}

#: Compensation hints for yellow labels — shown by `navig ledger show` /
#: `navig undo` refusals so "compensable" comes with the counter-action.
_COMPENSATION: dict[str, str] = {
    "config_change": "re-set the previous value: navig config set <key> <old>",
    "host_switch": "switch back: navig config set active_host <old>",
    "file_create": "delete the created path by hand",
    "file_upload": "delete the uploaded remote copy",
    "file_download": "delete the downloaded local copy",
    "tunnel_start": "navig tunnel remove",
    "tunnel_stop": "navig tunnel run",
    "service_restart": "re-manage the service (start/stop/restart)",
    "docker_command": "depends on the subcommand — start/stop compensable, rm/prune are not",
    "database_dump": "delete the dump file",
}

#: Secret-bearing config-key detector (LAST dotted segment, deliberately
#: narrower than the PII regex in navig.tools.api_schema: `email.smtp_host`
#: is not a secret, `email.password` is).
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|apikey|secret|token|password|passwd|credential"
    r"|private[_-]?key|access[_-]?key|auth)"
)


def is_sensitive_config_key(key: str) -> bool:
    """True when *key* names secret material (checked on the last dotted segment).

    Used by the config-set capture seam (values must never enter
    ``undo_data``/``command`` in plaintext) and by the undo engine as a
    defense-in-depth refusal.
    """
    last = key.rsplit(".", 1)[-1]
    return bool(_SENSITIVE_KEY_RE.search(last))


def classify(
    op_type: str,
    undo_data: Mapping | None = None,
    tags: Sequence[str] | None = None,
) -> Reversibility:
    """Compute the honest reversibility label for one operation.

    Rules, in order:

    - an ``undo`` entry (tagged by the undo engine) is capped at YELLOW:
      its compensation is re-running the original command, and it must
      never itself become an undo candidate (double-undo protection);
    - ``undo_data`` marked ``sensitive`` is capped at YELLOW: the plaintext
      needed to replay it is deliberately NOT stored (vault reference only);
    - captured ``undo_data`` on a type the undo engine supports → GREEN;
    - otherwise the static table; unknown types are RED.
    """
    if tags and "undo" in tags:
        return Reversibility.YELLOW
    if undo_data and undo_data.get("sensitive"):
        return Reversibility.YELLOW
    if undo_data and op_type in GREEN_CAPABLE_TYPES:
        return Reversibility.GREEN
    return _POTENTIAL.get(op_type, Reversibility.RED)


def compensation_hint(op_type: str) -> str:
    """The manual counter-action for a yellow *op_type* ("" when none known)."""
    return _COMPENSATION.get(op_type, "")


def label_glyph(label: str) -> str:
    """Rich-markup glyph for a label string ("" → the legacy/unlabeled dash)."""
    return {
        Reversibility.GREEN.value: "[green]●[/green] green",
        Reversibility.YELLOW.value: "[yellow]●[/yellow] yellow",
        Reversibility.RED.value: "[red]●[/red] red",
        Reversibility.NONE.value: "[dim]○ read-only[/dim]",
    }.get(label, "[dim]○ —[/dim]")
