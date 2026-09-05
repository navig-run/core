"""A handler nothing calls is a feature nobody has.

`_handle_audio_file_message` drew a complete Telegram action card — Transcribe ·
Identify · Info · Dismiss — with the `audmsg:` callbacks implemented on the other side,
and **nothing ever invoked it**. Every .mp3 and .wav a user sent landed on the generic
"can't read files through Telegram yet" ack instead, for as long as the code existed.
Its own test rebuilt the button rows inline rather than calling it, so it stayed green
while dead (#993 wired it, #1002 finished it).

Nothing in the build could see that. `check_module_attrs.py` catches the opposite shape —
a call to a function that does not exist — and an unreferenced *definition* raises no
warning in any linter the gate runs, because it is perfectly valid Python.

So: every private method in the gateway package — the Telegram channels, the server and
the route modules — must be referenced somewhere,
or be listed below with a reason. The listing is the point — "unwired" is then a decision
someone made, not a thing nobody noticed.

What counts as a reference is the whole guard, and it took three tries to get right:

1. `.name(` plus `"name"` — reported 25, of which **7 were `@property` forwarders** read
   as `self._NL_X`. A bare attribute read matches neither pattern, so acting on that list
   would have deleted live code.
2. Any word anywhere in the file text — no false positives, but it counts **comments**,
   and `_handle_audio_file_message` was named in exactly one ("Populated in
   _handle_audio_file_message …"). Deleting its call left the guard GREEN: it would not
   have caught the bug it exists for.
3. The AST, counting **attribute reads and string literals only**. Comments do not appear
   in an AST at all, docstrings are skipped, and both real ways to reach a METHOD survive:
   `self._foo` / `Cls._foo` (which includes a bare `@property` read), and
   `handler="_handle_start"` in the slash registry.

   ⚠ Counting `ast.Name` and `ast.FunctionDef` too — as this did at first — lets an
   unrelated module-level `def` of the SAME NAME anywhere in `core/navig` mask a dead
   method. Measured across the gateway package: exactly one was hidden that way,
   `TelegramChannel._handle_settings_menu`, a superseded copy of the live `/settings`
   handler. A bare name is never a reference to a method, so dropping those two node
   types is strictly sharper — it removed no legitimate reference (measured: zero other
   entries changed state).

Teeth: remove the call `#993` added and this fails, naming
`telegram_voice.py TelegramVoiceMixin._handle_audio_file_message`. Version 2 passed that
same mutation, which is why the text scan is not good enough.

Names reached only through a constructed `getattr` are exempt by pattern (see
CONSTRUCTED); anything else unreferenced must be listed in KNOWN_UNWIRED with a reason.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
REPO = Path(__file__).resolve().parents[3]


def _plugin_roots() -> list[Path]:
    """Every first-party plugin package, discovered rather than listed.

    `plugins/navig-*` plus `private/harbor` — the paid one, which lives outside
    `plugins/` and has been missed by address-based scans before. A checkout without
    `private/` simply contributes nothing.
    """
    roots = sorted(p for p in (REPO / "plugins").glob("navig-*") if p.is_dir())
    harbor = REPO / "private" / "harbor"
    if harbor.is_dir():
        roots.append(harbor)
    return roots


# core/navig plus every plugin. A plugin is scanned because the class this guard exists
# for — a complete handler nothing calls — is no less likely there; core is in the
# REFERENCE corpus because a plugin subclasses a core base and CORE calls the override
# (`store/base.py` → `self._migrate(...)`). Measured with the plugin alone as the corpus,
# six framework hooks looked dead; with core included, zero do.
SCANNED_ROOTS = [CORE, *_plugin_roots()]
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Names reached ONLY through a constructed lookup, which no scan of any kind can see.
# Two such idioms exist in core/navig, and both are load-bearing:
#   telegram.py   getattr(self, f"_handle_{_sys_cmd}_cmd", None)
#   store/base.py getattr(self, f"_migrate_v{v}_to_v{v + 1}")  ← the schema migrations.
# The second matters: a v1 database really is upgraded by a method nothing names, and
# "cleaning up" one would silently strand old data on an old schema.
CONSTRUCTED = re.compile(r"^(_handle_\w+_cmd|_migrate_v\d+_to_v\d+)$")

# Private methods that are genuinely unreferenced today, each with the reason it is
# acceptable. An entry here is a decision; delete it the moment the method is wired or
# removed. A stale entry is caught by test_no_stale_exemptions below.
KNOWN_UNWIRED: dict[str, str] = {
    # Deliberately dormant and documented as such in the source: the live dispatcher is
    # in TelegramChannel, and `test_slash_dispatch_parity` pins the two in step so the
    # day someone wires this one it cannot already have diverged.
    "_build_slash_handlers": "deliberately unwired; parity pinned by test_slash_dispatch_parity",
    # Telegram Bot API verbs implemented ahead of a caller. Harmless and cheap to keep:
    # each is a thin wrapper over _api_call whose absence would be re-added verbatim.
    "_pin_message": "Bot API wrapper kept ahead of a caller",
    "_unpin_message": "Bot API wrapper kept ahead of a caller",
    "_answer_inline_query": "Bot API wrapper kept ahead of a caller",
    # Left in place rather than deleted, unlike the 11 superseded route handlers removed
    # alongside this: neither is a stale copy of live logic.
    #   _on_config_reload would hot-reload gateway config (restarting the heartbeat when
    #   the interval changes). `config_watcher` emits a `config_reloaded` EVENT and
    #   nothing hooks this method to it. Wiring it would change what the daemon does at
    #   runtime, which is a behaviour decision, not a cleanup.
    "_on_config_reload": "hot-reload handler never hooked to the config_watcher event",
    # Reaction Intelligence, retired on purpose: `_process_update` now does
    # `if update.get("message_reaction"): return` with "replaced by reply keywords
    # (see navig.telegram.reply_actions)". The module's own header asserted both
    # integration points as fact long after they were removed — corrected there.
    "_on_message_reaction": "reactions retired as an action trigger; see reply_actions",
    "_record_bot_reply": "reactions retired as an action trigger; see reply_actions",
    # Reachable-looking helpers with no caller. Left in place rather than deleted
    # because each is small, correct, and the surrounding feature is still evolving.
    "_send_smart_reply": "checklist smart-reply path not surfaced",
    "_get_thread_for_command": "forum per-command threading not surfaced",
    "_edit_checklist_task": "checklist edit path not yet surfaced",
    "_handle_tier_override": "tier override not surfaced as a command",
    "_handle_persona": "persona command not surfaced",
    "_send_md_with_fallback": "exercised by tests only",
    "_handle_models_reset": "exercised by tests only",
    "_invalidate_forum_cache": "exercised by tests only",
    # ── outside the gateway ────────────────────────────────────────────────────
    # Each of these is unreferenced AND undecorated across all of core/navig. None is a
    # bug on its own; every one was read before being listed.
    "_redact_sensitive_data": (
        "documented public alias for _redact, which IS called on both write paths — "
        "secrets are redacted; only the alias is spare"
    ),
    "_embed_chunks": "superseded by _process_embedding_batch, which the live indexer uses",
    "_load_sync": "sync variant of the soul loader; the async path is the one in use",
    "_attach_labels": "self-heal PR labelling never surfaced",
    "_run_navig": "tray helper for launching a CLI command; no menu item calls it",
    "_deep_merge": "template merge helper with no caller",
    "_write_script": "BaseStore multi-statement helper; every store uses _write/_execute",
}


def _is_test_file(path: Path) -> bool:
    """Tests are excluded from BOTH the scan and the reference corpus.

    "It has tests" is not evidence anything runs it — #1014 found eleven dead endpoint
    handlers kept alive-looking by 17 test call sites. Counting a test as a reference
    would hide exactly the class this guard exists to catch.
    """
    posix = path.as_posix()
    return "/tests/" in posix or "/test/" in posix or path.name.startswith("test_")


def _scanned_files() -> list[Path]:
    out: list[Path] = []
    for root in SCANNED_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or _is_test_file(path):
                continue
            out.append(path)
    return out


def _display_path(path: Path) -> str:
    """Repo-relative where possible, so a failure names something greppable."""
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return path.name


@lru_cache(maxsize=1)
def _identifier_counts() -> Counter:
    """Every identifier the live package actually REFERENCES, counted once.

    ⚠ This walks the AST rather than the text, and the difference is the whole guard.
    A first version tallied words with a regex, so a name mentioned in a COMMENT counted
    as a reference — and `_handle_audio_file_message`, the dead handler this exists for,
    was named in exactly one comment ("Populated in _handle_audio_file_message …").
    Deleting its call left the guard green. Comments do not appear in an AST at all.

    Docstrings are excluded for the same reason. String *literals* are kept, because a
    registry entry (`handler="_handle_start"`) is a real reference — the one form a
    call-only scan would miss.
    """
    counts: Counter = Counter()
    for path in _scanned_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:  # pragma: no cover
            continue
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    docstrings.add(id(value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):          # self._foo / Cls._foo
                counts[node.attr] += 1
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and WORD.fullmatch(node.value)
            ):
                counts[node.value] += 1                  # handler="_handle_start"
    return counts


@lru_cache(maxsize=1)
def _private_methods() -> tuple[tuple[str, int, str, str, bool], ...]:
    """(file, line, class, name) for every private method in the scanned package.

    Cached: three tests need it and the scan parses every module in the package.
    """
    found: list[tuple[str, int, str, str, bool]] = []
    for path in _scanned_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:  # pragma: no cover - a parse failure is another test's job
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = item.name
                    if name.startswith("_") and not name.startswith("__"):
                        rel = _display_path(path)
                        found.append((rel, item.lineno, node.name, name,
                                      bool(item.decorator_list)))
    return tuple(found)


def _unreferenced() -> list[tuple[str, int, str, str, bool]]:
    """Unreferenced AND undecorated.

    ⚠ A DECORATOR is a registration this scan cannot follow. `@on(Button.Pressed, "#id")`
    hands the method to Textual, `@property` turns it into an attribute read on an object
    whose type we do not know, `@app.route` gives it to a router. Measured across
    core/navig: **69** private methods are unreferenced-but-decorated, 40 of them Textual
    button handlers in `tui/screens/`. Every one is live, and calling them dead would have
    made this guard useless outside the gateway — the packages it most needed to cover.
    """
    counts = _identifier_counts()
    return [
        row for row in _private_methods()
        if counts[row[3]] == 0 and not CONSTRUCTED.match(row[3]) and not row[4]
    ]


def test_the_scanner_actually_reads_the_package():
    """Anti-vacuity. A scanner that finds nothing makes every assertion below pass."""
    methods = _private_methods()
    assert len(methods) > 1900, f"only {len(methods)} private methods parsed — the scan is broken"
    names = {m[3] for m in methods}
    assert "_handle_audio_file_message" in names, "the method this guard exists for is missing"


def test_the_plugins_are_actually_scanned():
    """A global method count can stay healthy while the plugin roots contribute nothing.

    "Zero findings in plugins" is only meaningful if plugin files were read at all — so
    assert per-root coverage, not just a total. `private/harbor` is included when present
    because a paid plugin outside `plugins/` has been missed by address-based scans before.
    """
    scanned = {f for f, _line, _cls, _name, _dec in _private_methods()}
    plugin_files = [f for f in scanned if f.startswith("plugins/") or f.startswith("private/")]
    assert len(plugin_files) >= 20, (
        f"only {len(plugin_files)} plugin files contributed methods — the plugin roots are "
        "not being read, so a clean result there means nothing"
    )
    roots = {f.split("/")[1] for f in plugin_files if f.startswith("plugins/")}
    assert len(roots) >= 8, f"only {len(roots)} plugin packages scanned: {sorted(roots)}"


def test_tests_are_not_treated_as_production_references():
    """A method called only by its own test must still count as unreferenced.

    #1014: eleven dead endpoint handlers looked maintained because 17 test call sites
    drove them. If a test counted as a reference, this guard would have been blind to
    precisely the thing it exists for.
    """
    scanned = {f for f, _line, _cls, _name, _dec in _private_methods()}
    assert not [f for f in scanned if "/tests/" in f or f.split("/")[-1].startswith("test_")], (
        "test files leaked into the scan"
    )


def test_a_wired_handler_is_not_flagged():
    """The other half of anti-vacuity: the scanner must be able to tell wired from not.

    `_handle_audio_file_message` is the exact method that WAS dead and is now called, so
    a guard that still flagged it would be measuring nothing.
    """
    flagged = {row[3] for row in _unreferenced()}
    assert "_handle_audio_file_message" not in flagged
    assert "_transcribe_voice_message" not in flagged


def test_every_private_channel_method_is_reachable_or_listed():
    surprises = [row for row in _unreferenced() if row[3] not in KNOWN_UNWIRED]
    # Unpacks FIVE — rows carry the is-decorated flag since the decorator rule landed.
    # This line only runs when there IS a finding, so it never executed and crashed with
    # "too many values to unpack" the first time the guard actually caught something: it
    # reported a ValueError instead of naming the dead method. An error path that only
    # runs on failure is an error path nobody has run.
    detail = "\n".join(f"    {f}:{line}  {cls}.{name}" for f, line, cls, name, _dec in surprises)
    assert not surprises, (
        "these private methods are referenced by nothing in core/navig or the plugins — "
        "a handler nothing calls is a feature nobody has. Wire it, delete it, or add it to "
        f"KNOWN_UNWIRED with the reason:\n{detail}"
    )


def test_no_stale_exemptions():
    """An entry that is now wired (or gone) is a note describing a world that moved on."""
    unreferenced = {row[3] for row in _unreferenced()}
    defined = {row[3] for row in _private_methods()}
    for name, reason in KNOWN_UNWIRED.items():
        assert name in defined, f"KNOWN_UNWIRED lists '{name}', which no longer exists — drop it"
        assert name in unreferenced, (
            f"KNOWN_UNWIRED lists '{name}', but something references it now — drop the entry"
        )
        assert len(reason) > 15, f"KNOWN_UNWIRED['{name}'] needs a real reason"
