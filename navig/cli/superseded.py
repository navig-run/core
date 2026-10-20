"""Superseded CLI verbs — the ONE table of what replaced what.

Every verb that still answers but is no longer the way to do the thing is listed here,
with the command that replaced it. Three things read this table, so they cannot drift:

* the verb itself — :func:`hint` (it still works and points at the replacement) or
  :func:`moved` (it prints the replacement and exits 1);
* ``generated/deprecations.json`` — :func:`report_rows` is folded into
  :func:`navig.registry.manifest.deprecations_report`, so a regen publishes the table;
* ``tests/regression/test_command_surface_cleanup.py`` — resolves every ``replaced_by``
  through the real Click tree, verb AND flags, so a pointer at a command that does not
  exist fails the build instead of misleading the operator.

The CLI contract is "no breaking changes", so nothing listed here was unregistered: an
``alias`` is the same command under a second name (hidden from ``--help``), a ``hint``
verb keeps working, a ``redirect`` is a retired-engine shim that already explains itself,
and a ``moved`` verb never did its job (a stub, or an API nothing serves) and now says
where the real one is.

``replaced_by`` is written as a runnable command: ``<placeholders>`` stand for values the
operator supplies, and a token after a flag that takes a value is that flag's value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn

Kind = Literal["alias", "hint", "redirect", "moved"]

# The release these verbs were superseded in (core/pyproject.toml is 3.25.0 at the time).
SINCE = "3.26.0"


@dataclass(frozen=True)
class Superseded:
    path: str
    replaced_by: str
    kind: Kind
    note: str = ""
    since: str = SINCE


def _a(path: str, to: str, note: str = "", *, since: str = SINCE) -> Superseded:
    return Superseded(path, to, "alias", note, since)


def _h(path: str, to: str, note: str = "") -> Superseded:
    return Superseded(path, to, "hint", note)


def _r(path: str, to: str, note: str = "") -> Superseded:
    return Superseded(path, to, "redirect", note)


def _m(path: str, to: str, note: str = "") -> Superseded:
    return Superseded(path, to, "moved", note)


_WEBHOOK_NOTE = (
    "The webhook-management API these verbs called was never served by anything; "
    "triggers own webhook actions and inbound webhook triggers."
)
_FINANCE_NOTE = (
    "The finance CLI verbs were never implemented; finance lives in the NAVIG OS Finance app."
)
_BOOT_NOTE = "There is no boot sequence to configure; NAVIG starts as the background service."
_GOAL_NOTE = (
    "navig goal keeps standing goals in its own store; goals added with navig agent goal "
    "are not copied over and stay listed by navig agent goal list."
)


def _webhook_rows(group: str) -> list[Superseded]:
    return [
        _m(f"navig {group}", "navig trigger", _WEBHOOK_NOTE),
        _m(f"navig {group} list", "navig trigger list", _WEBHOOK_NOTE),
        _m(f"navig {group} add-inbound",
           "navig trigger add <name> --type webhook --action <command>", _WEBHOOK_NOTE),
        _m(f"navig {group} add-outbound",
           "navig trigger add <name> --action webhook:<url>", _WEBHOOK_NOTE),
        _m(f"navig {group} disable", "navig trigger disable <id>", _WEBHOOK_NOTE),
        _m(f"navig {group} delete", "navig trigger remove <id>", _WEBHOOK_NOTE),
        _m(f"navig {group} test", "navig trigger test <id>", _WEBHOOK_NOTE),
    ]


def _module_rows(group: str) -> list[Superseded]:
    return [
        _a(f"navig {group}", "navig store"),
        _h(f"navig {group} list", "navig store list --kind module"),
        _h(f"navig {group} enable", "navig store enable module:<module_id>"),
        _h(f"navig {group} disable", "navig store disable module:<module_id>"),
        _h(f"navig {group} info", "navig store info module:<module_id>"),
    ]


SUPERSEDED: tuple[Superseded, ...] = (
    # ── twin groups: one canonical name, the other a hidden alias of the same app ──
    _a("navig skills", "navig skill"),
    _a("navig spaces", "navig space"),
    _a("navig plugins", "navig plugin"),
    _a("navig signal", "navig signals"),
    _a("navig checkins", "navig checkin"),
    _a("navig bb", "navig blackbox"),
    _a("navig knowledge", "navig kg"),
    _a("navig dl", "navig download"),
    _a("navig tiktok", "navig download"),
    _a("navig tt", "navig download"),
    _a("navig fb", "navig facebook"),
    _a("navig media", "navig generate"),
    # Hidden by registration._SUPERSEDED_BY only while `navig content` is registered —
    # an install whose navig-pipeline predates that entry point keeps its only verb.
    _a("navig pipeline", "navig content",
       "Same app and verbs; content production is the canonical name (ADR-0002).",
       since="3.27.2"),
    # ── browser → cdp (gateway-backed Playwright → navig cdp's real browser) ──────
    _h("navig browser", "navig cdp"),
    _h("navig browser status", "navig cdp status"),
    _h("navig browser open", "navig cdp nav <url>"),
    _h("navig browser screenshot", "navig cdp screenshot --out <path>"),
    _h("navig browser click", "navig cdp click --ref <n>",
       "cdp clicks by accessibility ref: list them with navig cdp snapshot"),
    _h("navig browser fill", "navig cdp type <value>",
       "focus the field first: navig cdp click --ref <n>"),
    _h("navig browser stop", "navig cdp stop"),
    # ── agent goal → goal ──────────────────────────────────────────────────────────
    _h("navig agent goal", "navig goal", _GOAL_NOTE),
    # ── council → formation council ───────────────────────────────────────────────
    _a("navig council", "navig formation council"),
    _h("navig council run", "navig formation council <question>"),
    # ── agents → formation / agent run ─────────────────────────────────────────────
    _m("navig agents", "navig formation agents"),
    _m("navig agents list", "navig formation agents"),
    _m("navig agents run", "navig agent run <agent_id> --task <task>"),
    # ── webdash → deck (the gateway already serves the dashboard) ─────────────────
    _m("navig webdash", "navig deck open",
       "webdash started a second gateway; the running daemon already serves the deck"),
    # ── plugin lifecycle → store ───────────────────────────────────────────────────
    _h("navig plugin add", "navig store install <source>"),
    _h("navig plugin install", "navig store install <source>"),
    _h("navig plugin enable", "navig store enable plugin:<name>"),
    _h("navig plugin disable", "navig store disable plugin:<name>"),
    _h("navig plugin remove", "navig store remove <name>"),
    _h("navig plugin uninstall", "navig store remove <name>"),
    # ── module / modules → store ───────────────────────────────────────────────────
    *_module_rows("module"),
    *_module_rows("modules"),
    # ── boot / finance: stubs that never did anything ──────────────────────────────
    _m("navig boot", "navig service status", _BOOT_NOTE),
    _m("navig boot show", "navig service status", _BOOT_NOTE),
    _m("navig boot run", "navig service start", _BOOT_NOTE),
    _m("navig finance", "navig store info module:finance", _FINANCE_NOTE),
    _m("navig finance status", "navig store info module:finance", _FINANCE_NOTE),
    _m("navig finance balance", "navig store info module:finance", _FINANCE_NOTE),
    # ── snapshot → history files (+ backup for config snapshots) ──────────────────
    _a("navig snapshot", "navig history files"),
    _h("navig snapshot versions", "navig history files versions <filepath>"),
    _h("navig snapshot diff", "navig history files diff <filepath>"),
    _h("navig snapshot restore", "navig history files restore <filepath>"),
    _m("navig snapshot create", "navig backup export",
       "config snapshots were never implemented; a backup export captures the config"),
    # ── webhook / webhooks → trigger ───────────────────────────────────────────────
    *_webhook_rows("webhook"),
    *_webhook_rows("webhooks"),
    # ── pre-existing deprecation shims, now recorded ───────────────────────────────
    _h("navig ai ask", "navig ask <question>"),
    _h("navig ai explain", "navig ask <question>"),
    _h("navig ai diagnose", "navig ask <question>"),
    _h("navig ai suggest", "navig ask <question>"),
    _h("navig plans run", "navig plans add <goal>"),
    # ── the retired YAML workflow engine (task / flow / job) → Blocks ──────────────
    _r("navig task list", "navig block list"),
    _r("navig task show", "navig block show <name>"),
    _r("navig task run", "navig apply <name>"),
    _r("navig task test", "navig block verify <name>"),
    _r("navig task add", "navig block new <name>"),
    _r("navig task edit", "navig block show <name>"),
    _r("navig task remove", "navig block list"),
    _r("navig flow list", "navig block list"),
    _r("navig flow show", "navig block show <name>"),
    _r("navig flow run", "navig apply <name>"),
    _r("navig flow test", "navig block verify <name>"),
    _r("navig flow add", "navig block new <name>"),
    _r("navig flow edit", "navig block show <name>"),
    _r("navig flow remove", "navig block list"),
)

_BY_PATH: dict[str, Superseded] = {s.path: s for s in SUPERSEDED}


def get(path: str) -> Superseded:
    """The row for *path*. A KeyError here is a typo at a call site — fail loudly."""
    return _BY_PATH[path]


def _fill(template: str, values: dict[str, object]) -> str:
    out = template
    for key, value in values.items():
        if value is None or value == "":
            continue
        text = str(value)
        if any(c.isspace() for c in text):
            text = f'"{text}"'
        out = out.replace(f"<{key}>", text)
    return out


def hint(path: str, /, **values: object) -> None:
    """One line: this verb still works, and here is the command that replaced it.

    ``values`` fill the replacement's ``<placeholders>`` with what the operator typed,
    so the pointer is copy-pasteable (``navig browser open x.com`` → ``navig cdp nav x.com``).

    Written to STDERR: the verb still runs, and its stdout may be parsed (``--json``).
    """
    import typer  # noqa: PLC0415 — lazy, keeps the CLI fast

    row = get(path)
    typer.secho(
        f"! `{path}` is superseded — use `{_fill(row.replaced_by, values)}`",
        fg=typer.colors.YELLOW,
        err=True,
    )


def moved(path: str, /, **values: object) -> NoReturn:
    """Say where the real command is, then exit 1 — this verb does not do the job."""
    import typer  # noqa: PLC0415

    from navig import console_helper as ch  # noqa: PLC0415

    row = get(path)
    ch.error(f"`{path}` moved to `{_fill(row.replaced_by, values)}`", row.note or None)
    raise typer.Exit(1)


def report_rows() -> list[dict[str, str]]:
    """Rows for ``generated/deprecations.json`` (same shape as the manifest-derived rows)."""
    return [
        {
            "path": s.path,
            "since": s.since,
            "remove_after": "",
            "replaced_by": s.replaced_by,
            "kind": s.kind,
            "note": s.note,
        }
        for s in SUPERSEDED
    ]
