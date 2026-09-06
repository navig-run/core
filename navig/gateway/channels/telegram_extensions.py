"""Telegram Extensions — one catalog, one reader, for every bot feature toggle.

The bot has ~108 slash commands, ~34 callback-button prefixes, a dozen silent
message-pipeline behaviours and several background senders.  Before this module
they were gated by six unrelated mechanisms that did not know about each other
(``telegram.disabled_commands``, ~10 ad-hoc ``telegram.<x>_enabled`` keys, the
notify matrix, per-cron-job ``enabled``, ``monitors.*`` and ``modules.overrides``),
and none of them hid a disabled feature's *buttons and menus*.

An **extension** is a bundle of commands + callback prefixes + reply-keyword
actions + pipeline behaviours that a user switches on or off as one thing.  State
lives in ``modules.overrides`` via :mod:`navig.modules.registry` — there is no
``telegram.extensions.*`` config tree — so the Deck store page, ``navig store``,
the desktop OS and the bot all read one truth.

Every enforcement chokepoint calls THIS module and nothing else:

* ``command_enabled``  — setMyCommands, ``/help``, the Help Encyclopedia, dispatch
* ``callback_enabled`` — the callback router (upstream of ``slash:``)
* ``action_enabled``   — reply-keyword transforms
* ``filter_keyboard``  — the single outbound ``reply_markup`` filter in ``_api_call``

Why a catalog rather than a field on ``SlashCommandEntry``: a bare per-entry
string is fail-open by default (a new entry that forgets it silently escapes the
gate) and has nowhere to put a callback prefix, a reply action or a background
job — which are most of what an extension owns.  The catalog plus the category
fallback means a new command in an existing category is gated correctly with
zero author effort, and ``core/tests/quality/test_telegram_extension_coverage.py``
fails the build for anything that still escapes.

DELIBERATELY FAIL-OPEN.  An unmapped command, an unknown extension id or any
error resolves to ENABLED, and the resolution logs at most once.  A developer's
omission must never silence the operator's bot; the CI guards are what make that
safe.  Do not "fix" this into fail-closed.

Stdlib only at module scope — ``navig help`` must respond in under 50 ms, so
every navig import here is function-scoped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

# The resolver itself is not about Telegram, so it lives in `extension_gate` and a
# second channel can reuse it rather than copy it. Importing it at module scope
# costs nothing beyond the module object: it is stdlib-only at import time, and
# this file already lives inside `navig.gateway.channels`, so the package it would
# otherwise pull in has been executed before this line is reached.
from navig.gateway.channels.extension_gate import resolve_enabled, warn_unknown

logger = logging.getLogger(__name__)

_MODULE_PREFIX = "tg:"

_CHANNEL = "Telegram"


@dataclass(frozen=True)
class TelegramExtension:
    """One user-facing on/off bundle."""

    id: str
    label: str
    description: str
    group: str = "Systems"          # display grouping: Life/Systems/Comms/Create/Knowledge
    icon: str = "puzzle"
    default_enabled: bool = True
    locked: bool = False            # locked extensions are never registered as modules
    commands: frozenset[str] = frozenset()
    callback_prefixes: tuple[str, ...] = ()
    reply_actions: frozenset[str] = frozenset()
    legacy_key: str | None = None   # pre-existing config key this extension supersedes
    requires: tuple[str, ...] = ()  # ModuleDef.requires
    about: tuple[str, ...] = ()     # "switching it off" bullets, shown on the detail card
    features: tuple[str, ...] = ()

    @property
    def module_id(self) -> str:
        return f"{_MODULE_PREFIX}{self.id}"


# -- The catalog --------------------------------------------------------------
# `core` is LOCKED and is never emitted as a ModuleDef.  That single omission is
# the whole mechanism for "/start, /help, /status and plain chat can never be
# switched off": POST /api/deck/modules/toggle returns 404 for an id that is not
# in the registry, with no special-casing anywhere.

EXTENSIONS: tuple[TelegramExtension, ...] = (
    TelegramExtension(
        id="core",
        label="Core",
        description="The bot itself - chat, help, status, settings.",
        group="Core",
        locked=True,
        commands=frozenset({
            "start", "help", "helpme", "status", "ping", "about", "version",
            "user", "lang", "mode", "deck", "skill", "restart",
            # The switch for every other switch, and its alias.  Both are in
            # LOCKED_COMMANDS: if /extensions could be switched off the only
            # route back would be a terminal.
            "extensions", "ext",
            # `settings` is a LOCKED_COMMANDS name with no registry entry (the
            # surface moved to the Deck).  Claimed here so the "locked commands
            # are all Core" invariant holds without a special case.
            "settings",
        }),
        callback_prefixes=(
            # "xt:" is the extensions card itself - locked, so it can always
            # answer even when everything else is switched off.
            "xt:",
            "helpme", "help:", "nav:", "st_", "stfix:", "ask_followup:", "wf:",
            "ms_", "mdl_", "aitier_", "ai_close", "prov_", "open_providers",
            "pmv_", "pms_", "hyb_", "vis_", "pu_", "heard_",
            # NOTE: "slash:" is deliberately NOT listed here.  It re-dispatches
            # an arbitrary command, so it must resolve to the extension owning
            # that command - see the special case in extension_for_callback.
            # Binding it to always-on Core would reopen the exact bypass this
            # module exists to close.
        ),
    ),
    # -- Life -----------------------------------------------------------------
    TelegramExtension(
        id="habits",
        label="Habits",
        description="Daily check-in cards, streaks and the evening close.",
        group="Life", icon="heart-pulse",
        # `health` used to live here and now belongs to the Health extension
        # below, where the command's name matches what it reports. Every command
        # maps to exactly ONE extension (test_telegram_extension_coverage), so
        # the move has to happen on both sides in one edit.
        commands=frozenset({"habits", "workout", "stats", "card"}),
        callback_prefixes=("hb:",),
        about=(
            'those four commands stop working and leave /help and the "/" menu',
            "the check-in card's buttons stop answering",
            "the scheduled check-in reminders are not delivered",
            "the schedules and every logged row stay exactly as they are",
        ),
    ),
    TelegramExtension(
        id="health",
        label="Health",
        description="Weight, sleep and mood — the morning weigh-in and the weekly check-in.",
        group="Life", icon="activity",
        commands=frozenset({"health", "weigh", "body"}),
        callback_prefixes=("bm:",),
        about=(
            'those three commands stop working and leave /help and the "/" menu',
            "the morning weigh-in and the Sunday check-in are not delivered",
            "the weekly card's buttons stop answering",
            "every measurement already in metrics.csv stays exactly as it is",
        ),
    ),
    TelegramExtension(
        id="reminders",
        label="Reminders",
        description="One-off and recurring reminders delivered to this chat.",
        group="Life", icon="alarm-clock",
        commands=frozenset({"remindme", "myreminders", "reminders", "cancelreminder"}),
        about=(
            "those four commands stop working",
            "reminders already due are not delivered while it is off",
            "nothing you have scheduled is deleted",
        ),
    ),
    TelegramExtension(
        id="briefings",
        label="Briefings",
        description="Morning and evening summary cards.",
        group="Life", icon="sunrise",
        commands=frozenset({"briefing"}),
        callback_prefixes=("morn:", "eve:"),
        about=(
            "/briefing stops working",
            "the scheduled morning and evening cards are not sent",
        ),
    ),
    # -- Systems --------------------------------------------------------------
    TelegramExtension(
        id="monitoring",
        label="Machine watch",
        description="Disk, memory, CPU, services and ports, on demand.",
        group="Systems", icon="activity",
        commands=frozenset({
            "disk", "memory", "cpu", "uptime", "services", "ports", "kill",
            "top", "df", "cron",
        }),
        callback_prefixes=("kill_confirm:", "kill_cancel"),
    ),
    TelegramExtension(
        id="remote",
        label="Remote & Docker",
        description="SSH hosts, files, shell, containers and SQL.",
        group="Systems", icon="server",
        commands=frozenset({
            "hosts", "hosttest", "test", "use", "apps", "app", "files", "cat",
            "run", "backup", "tunnels", "vhosts", "flows",
            "docker", "logs", "exec", "db", "tables", "query",
        }),
        # Switching the active host is a Remote action, not a Core one.
        # "app_use:" is emitted by the /apps card and routed in telegram.py beside
        # "host_use:". Both switch the active target and re-render the card.
        callback_prefixes=("host_use:", "app_use:"),
        about=(
            'those commands stop working and leave /help and the "/" menu',
            "/restart keeps working - it belongs to Core",
            "no host, credential or connection is changed",
        ),
    ),
    TelegramExtension(
        id="nettools",
        label="Net tools",
        description="DNS, SSL, WHOIS and network lookups.",
        group="Systems", icon="globe",
        commands=frozenset({"ip", "dns", "ssl", "whois", "netstat"}),
    ),
    TelegramExtension(
        id="mesh",
        label="Mesh",
        description="LAN peers and node switching.",
        group="Systems", icon="network",
        commands=frozenset({"nodes", "leader", "mesh", "switch"}),
    ),
    TelegramExtension(
        id="diagnostics",
        label="Diagnostics",
        description="Debug traces and self-healing.",
        group="Systems", icon="stethoscope",
        commands=frozenset({"debug", "trace", "autoheal"}),
        callback_prefixes=("dbg_", "trace_", "heal_"),
    ),
    # -- Comms ----------------------------------------------------------------
    TelegramExtension(
        id="messaging",
        label="Messaging",
        description="Send across channels, threads and contacts.",
        group="Comms", icon="send",
        commands=frozenset({
            "send", "sms", "wa", "thread", "threads", "contact", "contacts",
            "reply", "messengers", "messenger",
        }),
        callback_prefixes=("msg:", "open_messengers"),
    ),
    TelegramExtension(
        id="business",
        label="Business inbox",
        description="Telegram Business conversations and auto-reply.",
        group="Comms", icon="briefcase",
        legacy_key="telegram.business.enabled",
        about=(
            "business messages are no longer catalogued",
            "deletion alerts stop",
            "auto-reply stops answering as you",
        ),
    ),
    TelegramExtension(
        id="groups",
        label="Groups & forums",
        description="Moderation, checklists, forum routing and reactions.",
        group="Comms", icon="users",
        # ON by default, deliberately. This bundle owns checklists and reactions,
        # which have ALWAYS defaulted to on — defaulting the extension to off
        # would silently remove two working features on upgrade. The moderation
        # commands it also owns stay safe regardless: the dispatcher already
        # requires a group chat AND group-admin rights for each of them, and
        # telegram.forum_routing_enabled still defaults to off on its own.
        default_enabled=True,
        commands=frozenset({"kick", "mute", "unmute", "search"}),
    ),
    TelegramExtension(
        id="inline",
        label="Inline mode",
        description="Answering @mentions inline in any chat.",
        group="Comms", icon="at-sign",
        legacy_key="telegram.inline_mode_enabled",
    ),
    # -- Create ---------------------------------------------------------------
    TelegramExtension(
        id="voice",
        label="Voice notes",
        description="Voice-note transcription and spoken replies.",
        group="Create", icon="mic",
        commands=frozenset({"voiceon", "voiceoff"}),
        callback_prefixes=("audio:", "audmsg:"),
    ),
    TelegramExtension(
        id="media",
        label="Link cards & media",
        description="TikTok cards, music links, image generation and the room catalog.",
        group="Create", icon="image",
        commands=frozenset({"music", "imagegen"}),
        callback_prefixes=("tk:",),
        reply_actions=frozenset({"tiktok", "music"}),
    ),
    # -- Knowledge ------------------------------------------------------------
    TelegramExtension(
        id="writing",
        label="Writing & reasoning",
        description="Rewrite, summarise, explain and refine - commands and reply keywords.",
        group="Knowledge", icon="pen-line",
        commands=frozenset({"format", "fmt", "think", "refine", "explain_ai"}),
        # "fmt:" is the /format settings card (telegram_formatter.py), routed by
        # CallbackHandler.handle -> telegram_formatter.handle_fmt_callback. It was
        # claimed here while still inert, so the surface was gated correctly before
        # it was live; test_every_emitted_prefix_is_actually_routed now keeps it live.
        callback_prefixes=("card:", "rfn:", "fmt:"),
        reply_actions=frozenset({
            "actions", "casual", "context", "debug", "expand", "explain", "fix",
            "improve", "keypoints", "outline", "persuasive", "professional",
            "refine", "rewrite", "save", "shorten", "summarize", "translate",
        }),
        about=(
            "those commands stop working",
            'replying "summarize" or "translate" to a message is treated as ordinary chat',
        ),
    ),
    TelegramExtension(
        id="utilities",
        label="Utilities",
        description="Time, weather, currency, pinning and profile odds and ends.",
        group="Knowledge", icon="wrench",
        commands=frozenset({
            "time", "weather", "currency", "crypto_list", "choice", "pin",
            "stats_global", "profile", "quote", "respect",
        }),
        reply_actions=frozenset({"pin", "unpin"}),
    ),
    TelegramExtension(
        id="planning",
        label="Spaces & plans",
        description="Spaces, plans and the intake queue.",
        group="Knowledge", icon="map",
        commands=frozenset({"plans", "plan", "space", "spaces", "intake"}),
        callback_prefixes=("task:",),
    ),
    TelegramExtension(
        id="autopilot",
        label="AI autopilot",
        description="Autonomous multi-step continuation.",
        group="Knowledge", icon="bot",
        commands=frozenset({
            "auto_start", "auto_stop", "auto_status", "continue", "pause", "skip",
        }),
        callback_prefixes=("nl_",),
    ),
)

GROUP_ORDER: tuple[str, ...] = ("Life", "Systems", "Comms", "Create", "Knowledge")

# Fallback for a command whose name is in no extension's `commands` set, keyed on
# SlashCommandEntry.category.  "tools" is DELIBERATELY absent: it mixes /run (SSH)
# with /think (LLM reasoning), so a new `category="tools"` command must be assigned
# explicitly in the catalog above or the coverage guard fails.  That is the
# intended pressure point, not an oversight.
CATEGORY_TO_EXTENSION: dict[str, str] = {
    "core": "core",
    "admin": "groups",
    "ai": "autopilot",
    "database": "remote",
    "diagnostics": "diagnostics",
    "docker": "remote",
    "habits": "habits",
    "health": "health",
    "media": "media",
    "mesh": "mesh",
    "messaging": "messaging",
    "monitoring": "monitoring",
    "social": "utilities",
    "utilities": "utilities",
    "voice": "voice",
}

# Callback-data prefixes that are deliberately NOT gated, each with its reason.
# The prefix guard fails on any routing/emitting literal that is neither claimed
# by an extension nor listed here.
UNGATED_PREFIXES: dict[str, str] = {
    # DELIBERATELY EMPTY. Every prefix in the tree is claimed by an extension,
    # and an empty exemption list is the strongest state this guard can be in.
    # Adding an entry asserts a button must answer even when its own feature is
    # switched off - which is nearly always wrong, because "off" is supposed to
    # mean off. `test_ungated_prefixes_have_no_ghosts` deletes stale entries, so
    # this cannot quietly become a graveyard for real findings.
}

_BY_ID: dict[str, TelegramExtension] = {e.id: e for e in EXTENSIONS}

# Longest-match-wins, so "stfix:" resolves before "st_" and a future "stX_"
# can never be shadowed by a shorter sibling.
_PREFIX_OWNER: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((p, e.id) for e in EXTENSIONS for p in e.callback_prefixes),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )
)
_COMMAND_OWNER: dict[str, str] = {c: e.id for e in EXTENSIONS for c in e.commands}
_ACTION_OWNER: dict[str, str] = {a: e.id for e in EXTENSIONS for a in e.reply_actions}


def get(ext_id: str) -> TelegramExtension | None:
    """Return the extension with this id (with or without the ``tg:`` prefix)."""
    key = ext_id[len(_MODULE_PREFIX):] if ext_id.startswith(_MODULE_PREFIX) else ext_id
    return _BY_ID.get(key)


def all_extensions(*, include_locked: bool = False) -> list[TelegramExtension]:
    """Catalog in stable display order - group, then label.

    Ordering is deliberately state-INDEPENDENT: a toggled row must not move out
    from under the operator's finger when the card re-renders.
    """
    items = [e for e in EXTENSIONS if include_locked or not e.locked]
    return sorted(
        items,
        key=lambda e: (
            GROUP_ORDER.index(e.group) if e.group in GROUP_ORDER else 99,
            e.label.lower(),
        ),
    )


def is_enabled(ext_id: str) -> bool:
    """Resolve one extension's on/off state.  Pure read.  Never raises.

    Order - first match wins::

        0. LOCKED    ext.locked                            -> True
        1. OPERATOR  modules.overrides["tg:<id>"] PRESENT  -> coerce_bool(v, default)
        2. LEGACY    ext.legacy_key PRESENT in config      -> coerce_bool(v, default)
        3. DEFAULT   ext.default_enabled
        4. UNKNOWN   not in the catalog                    -> True (fail open, logged once)

    "PRESENT" means the dotted key EXISTS, not that it is truthy.

    NOTHING IS EVER WRITTEN - no migration pass, no backfill.  A legacy key stays
    the operator's record until they touch the new toggle; from then on rule 1
    shadows it permanently.

    This gate is ADDITIVE and composes as AND with each feature's own existing
    inner check (``music_actions.enabled()``, ``catalog_enabled()``,
    ``_get_forum_config()`` ...).  Those all stay exactly where they are, so a
    feature that is off today stays off for two independent reasons and an
    extension with several sub-features needs no multi-key precedence rule.
    """
    ext = get(ext_id)
    if ext is None:
        warn_unknown(_CHANNEL, ext_id)
        return True
    return resolve_enabled(
        module_id=ext.module_id,
        default_enabled=ext.default_enabled,
        locked=ext.locked,
        legacy_key=ext.legacy_key,
    )


def enabled_ids() -> frozenset[str]:
    """Ids of every currently-enabled extension (locked ones included)."""
    return frozenset(e.id for e in EXTENSIONS if is_enabled(e.id))


# -- Reverse lookups ----------------------------------------------------------

def extension_for_command(command: str, category: str | None = None) -> str | None:
    """Resolve a bare command name to its extension id.

    Catalog membership first, then the category fallback.  Returns ``None`` when
    neither resolves - the coverage guard turns that into a build failure, and
    the runtime treats it as enabled.
    """
    name = command.lstrip("/").strip().lower().split("@", 1)[0]
    owner = _COMMAND_OWNER.get(name)
    if owner:
        return owner
    if category:
        return CATEGORY_TO_EXTENSION.get(category)
    return None


#: Callback prefix whose payload is a COMMAND to re-dispatch, not an opaque id.
#: ``slash:<cmd>`` resolves to whichever extension owns ``<cmd>``.
_SLASH_CB_PREFIX = "slash:"


def extension_for_callback(cb_data: str) -> str | None:
    """Resolve callback data to its extension id via longest-matching prefix.

    ``slash:<cmd>`` is special-cased: the generic re-dispatch button carries an
    arbitrary command name, so its owner is the command's owner.  Treating the
    prefix itself as owned by Core would let a button on an old card run a
    command whose extension is switched off - the very bypass this gate exists
    to close (``_handle_health`` emits ``slash:habits``, for one).
    """
    if not cb_data:
        return None
    if cb_data.startswith(_SLASH_CB_PREFIX):
        # Payloads seen in the tree: "slash:disk", "slash:cat:<path>",
        # "slash:run:<cmd>", "slash:files:<path>", "slash:close". The dispatcher
        # exact-matches the whole remainder against _SLASH_REGISTRY, so only the
        # bare form resolves today and the rest are inert. Take the first token
        # either way, so ownership is right whichever form is live.
        rest = cb_data[len(_SLASH_CB_PREFIX):].strip()
        target = rest.split(":", 1)[0].split()[0] if rest else ""
        owner = extension_for_command(target) if target else None
        # An unresolvable payload is an inert button on the generic dispatcher,
        # which is Core. Returning None here would read as "undeclared prefix"
        # and hide a real gap from the prefix guard.
        return owner or "core"
    for prefix, ext_id in _PREFIX_OWNER:
        if cb_data.startswith(prefix):
            return ext_id
    return None


def extension_for_action(action: str) -> str | None:
    """Resolve a canonical reply-keyword action to its extension id."""
    return _ACTION_OWNER.get(action)


# -- The four gate functions every chokepoint calls ---------------------------

def command_enabled(command: str, category: str | None = None) -> bool:
    """False only when the command's extension resolves and is switched off."""
    ext_id = extension_for_command(command, category)
    if ext_id is None:
        return True  # fail open - the coverage guard makes this unreachable in CI
    return is_enabled(ext_id)


def callback_enabled(cb_data: str) -> bool:
    """False only when the button's extension resolves and is switched off."""
    if not cb_data:
        return True
    for prefix in UNGATED_PREFIXES:
        if cb_data.startswith(prefix):
            return True
    ext_id = extension_for_callback(cb_data)
    if ext_id is None:
        return True  # undeclared prefix - fail open; the prefix guard catches it
    return is_enabled(ext_id)


def action_enabled(action: str) -> bool:
    """False only when the reply-keyword action's extension is switched off."""
    ext_id = extension_for_action(action)
    if ext_id is None:
        return True
    return is_enabled(ext_id)


# -- The single outbound keyboard filter --------------------------------------

def filter_keyboard(markup: Any) -> Any:
    """Strip buttons belonging to disabled extensions from one ``reply_markup``.

    Applied once, in :meth:`TelegramChannel._api_call`, so it covers every
    keyboard the bot sends - including cards built by a separate CLI process and
    any keyboard a plugin adds later - without editing a single builder.

    Returns the filtered markup, or ``None`` when the whole keyboard emptied
    (the caller must then drop ``reply_markup`` entirely rather than send an
    empty ``inline_keyboard``).

    Conservative by construction: only ``callback_data`` matching a DECLARED
    prefix of a DISABLED extension is dropped.  ``url``, ``web_app``,
    ``switch_inline_query`` and any undeclared prefix are always kept, and
    reply/force-reply markups are returned untouched.
    """
    if not isinstance(markup, dict):
        return markup
    rows = markup.get("inline_keyboard")
    if not isinstance(rows, list):
        return markup  # ReplyKeyboardMarkup / ForceReply / RemoveKeyboard

    changed = False
    out_rows: list[Any] = []
    for row in rows:
        if not isinstance(row, list):
            out_rows.append(row)
            continue
        kept = []
        for btn in row:
            cb = btn.get("callback_data") if isinstance(btn, dict) else None
            if isinstance(cb, str) and not callback_enabled(cb):
                changed = True
                continue
            kept.append(btn)
        if kept:
            out_rows.append(kept)
        elif row:
            changed = True
    if not changed:
        return markup
    if not out_rows:
        return None
    return {**markup, "inline_keyboard": out_rows}


# -- User-facing copy ---------------------------------------------------------

def disabled_notice(command: str, category: str | None = None) -> str:
    """Message for a typed slash command whose extension is switched off."""
    ext = get(extension_for_command(command, category) or "")
    name = command.lstrip("/").split("@", 1)[0]
    if ext is None:
        return f"/{name} is switched off.\nTurn it back on: /extensions"
    return f"/{name} belongs to {ext.label}, which is off.\nTurn it back on: /extensions"


def stale_button_answer(cb_data: str) -> str:
    """Toast for a tapped button whose extension is switched off (<=200 chars)."""
    ext = get(extension_for_callback(cb_data) or "")
    label = ext.label if ext else "That feature"
    return f"{label} is off - this card no longer answers"


# -- Registry + surface payloads ----------------------------------------------

def module_defs() -> list[Any]:
    """Every non-locked extension as a ``ModuleDef``, for the module registry.

    ``core`` is omitted deliberately - see the catalog comment.
    """
    from navig.modules.registry import ModuleDef, ModuleKind  # noqa: PLC0415

    return [
        ModuleDef(
            id=e.module_id,
            label=e.label,
            description=e.description,
            kind=ModuleKind.SERVICE,
            category="telegram",
            icon=e.icon,
            capability=None,
            surfaces=[f"telegram:{e.id}"],
            requires=list(e.requires) or ["gateway"],
            default_enabled=e.default_enabled,
            hidden=True,
            app_category=None,
            source="builtin",
            about=list(e.about) or None,
            features=list(e.features) or None,
        )
        for e in EXTENSIONS
        if not e.locked
    ]


def list_extensions() -> dict[str, Any]:
    """The ONE payload shape rendered by the bot card, the CLI table and the Deck.

    Three surfaces built from one producer cannot disagree with each other.
    """
    try:
        from navig.gateway.channels.telegram_commands import (  # noqa: PLC0415
            get_disabled_commands,
        )

        disabled_cmds = get_disabled_commands()
    except Exception:  # noqa: BLE001
        disabled_cmds = set()

    rows: list[dict[str, Any]] = []
    on = 0
    for e in all_extensions():
        enabled = is_enabled(e.id)
        on += int(enabled)
        cmds = sorted(e.commands)
        # False when EVERY command this extension owns was individually switched
        # off in the Deck's Commands tab - an inconsistency that is otherwise
        # invisible (the extension reads "on" and nothing works).
        running: bool | None = None
        if cmds:
            running = bool(set(cmds) - disabled_cmds)
        rows.append({
            "id": e.module_id,
            "key": e.id,
            "label": e.label,
            "description": e.description,
            "group": e.group,
            "icon": e.icon,
            "enabled": enabled,
            "default_enabled": e.default_enabled,
            "locked": False,
            "capability": None,
            "min_tier": None,
            "available": True,
            "requirement": None,
            "running": running,
            "commands": cmds,
            "callbacks": list(e.callback_prefixes),
            "reply_actions": sorted(e.reply_actions),
            "legacy_key": e.legacy_key,
            "about": list(e.about),
        })
    return {
        "extensions": rows,
        "counts": {"total": len(rows), "on": on, "off": len(rows) - on},
        "group_order": list(GROUP_ORDER),
    }
