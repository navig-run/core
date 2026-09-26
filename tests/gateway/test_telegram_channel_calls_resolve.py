"""Every ``self.channel._x`` in the Telegram layer must exist on TelegramChannel.

The shape this exists for
-------------------------
``CallbackHandler`` (telegram_keyboards.py) is constructed as ``CallbackHandler(self)`` from
telegram.py, so ``self.channel`` IS the live ``TelegramChannel``. But TelegramChannel does
**not** inherit its "mixins" — the runtime MRO is ``[TelegramChannel, object]``, the mixin
classes have zero subclasses, and the ``class TelegramChannel(TelegramVoiceMixin, ...)`` lines
in those modules are inside DOCSTRINGS. Mixin methods are reached by explicit unbound calls or
``functools.partial(mixin_fn, self)`` at dispatch time, neither of which puts an attribute on
the instance.

So ``self.channel._handle_providers(...)`` looks like ordinary delegation and is an
``AttributeError`` when the button is pressed. Nothing catches it at import time, no type
checker sees it (each module is internally consistent), and the failure only appears when a
user clicks.

This was not hypothetical: the Telegram transcribe action silently produced nothing because
``_get_file_path`` and ``_build_file_url`` were missing exactly this way — the mixin caught the
AttributeError, logged it at WARN and returned. That one is fixed; this guard exists so the
next one fails the build instead of a user's click.

Why the baseline is EMPTY
-------------------------
Every cross-object call now resolves, so ``KNOWN_BROKEN`` is empty and an entry means a real
regression rather than a known gap.

It got there by fixing two things and RETRACTING a third. ``_pending_api_key_input`` was
carried as unresolvable, described as needing "an owner and an initialisation point" -- it had
both. It is a lazily-created per-chat store: the writer guards with ``hasattr`` and creates it,
the reader defaults with ``getattr(..., {})``, and driving the real handler on a real channel
returns cleanly with nothing pending and lets ``/cancel`` through while pending. The bug was in
this GUARD, which could not tell a missing binding from a store created on first use -- see
``_lazily_initialised_on_the_channel``. A baseline entry for a non-problem is worse than none:
it teaches the next reader that a green run has known holes in it.

A correction worth keeping, because the wrong version survived a whole review cycle: this
docstring used to claim ``_edit_audio_and_reply`` was blocked by two disagreeing
``_api_call_multipart`` signatures. There is exactly ONE definition of that method in the tree
— ``telegram_api.TelegramApiMixin``, taking ``(method, data, files)`` — and all three call
sites match it. The "other signature" was a local variable named ``content_type`` in telegram.py
that classifies an INCOMING message and has nothing to do with uploads. The real blocker was
mundane: four names to bind, not a conflict to resolve. **Read the definition before recording
an incompatibility.**

The baseline compares by NAME, so fixing one is green and adding a new one is red.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CHANNELS = Path(__file__).resolve().parents[2] / "navig" / "gateway" / "channels"
GATEWAY = CHANNELS.parent

#: A TelegramChannel is stored under BOTH names, and one holder lives outside ``channels/``
#: (``gateway/notifications.py``). Scoping the scan to ``self.channel`` inside ``channels/``
#: -- which it was -- protects a PATH rather than the surface, and this layer's bug class does
#: not respect either boundary. Verified before widening: all six holders under ``gateway/``
#: hold a Telegram channel, and neither discord.py nor matrix.py has a ``self.channel`` at
#: all, so there is no cross-channel noise to import.
_CHANNEL_HOLDER_NAMES = ("channel", "_channel")


def _scanned_files() -> list[Path]:
    """Every gateway module that could hold a channel -- the channel itself excepted."""
    return [f for f in sorted(GATEWAY.rglob("*.py")) if f.name != "telegram.py"]

#: Unresolvable today. Each entry says what a user loses, so nobody deletes a line to go green.
KNOWN_BROKEN: dict[str, str] = {}


def _channel_attributes() -> set[str]:
    """Everything a live TelegramChannel instance can answer to.

    Class attributes alone are not enough: ``_session`` and friends are assigned in
    ``__init__``, so a class-level ``hasattr`` reports them missing and would manufacture
    false findings. Anything ever assigned as ``self.X`` in the channel module counts, as does
    anything bound with ``setattr``.
    """
    from navig.gateway.channels.telegram import TelegramChannel

    src = (CHANNELS / "telegram.py").read_text(encoding="utf-8", errors="replace")
    attrs = set(dir(TelegramChannel))
    attrs |= set(re.findall(r"self\.([A-Za-z_][A-Za-z0-9_]*)\s*[:=]", src))
    attrs |= set(re.findall(r"setattr\(\s*self\s*,\s*[\"']([A-Za-z_][A-Za-z0-9_]*)", src))
    attrs |= _lazily_initialised_on_the_channel()
    return attrs


def _lazily_initialised_on_the_channel() -> set[str]:
    """Attributes some consumer CREATES on the channel: ``self.channel.X = ...``.

    A name with an assignment like that is not a missing binding -- it is a lazily-created
    per-chat store, and reporting it as unresolvable is a false finding. The house pattern
    guards the write with ``hasattr`` and defaults the read with ``getattr(..., {})``, so the
    attribute simply does not exist until the flow that owns it starts:

        if not hasattr(self.channel, "_pending_api_key_input"):   # telegram_keyboards.py
            self.channel._pending_api_key_input = {}

    Without this, the guard cannot tell "nobody bound this method" from "this store is created
    on first use", and the only way to go green is a baseline entry -- which is how a baseline
    stops meaning anything. ``_pending_api_key_input`` sat in KNOWN_BROKEN for exactly that
    reason, described as needing "an owner and an initialisation point" when it had both.

    The subject here is a MISSING BINDING. Read-before-write ordering is a different bug and
    this does not claim to catch it: a bare read of a lazily-created attribute before the
    write still raises, and nothing in this scan would see it.
    """
    found: set[str] = set()
    for f in _scanned_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.ctx, ast.Store)
                and isinstance(n.value, ast.Attribute)
                and n.value.attr in _CHANNEL_HOLDER_NAMES
                and isinstance(n.value.value, ast.Name)
                and n.value.value.id == "self"
            ):
                found.add(n.attr)
    return found


def _cross_object_calls() -> dict[str, list[str]]:
    """{attribute -> ['file:line', ...]} for every ``self.<holder>._private`` reference."""
    found: dict[str, list[str]] = {}
    for f in _scanned_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Attribute)
                and n.attr.startswith("_")
                and isinstance(n.value, ast.Attribute)
                and n.value.attr in _CHANNEL_HOLDER_NAMES
                and isinstance(n.value.value, ast.Name)
                and n.value.value.id == "self"
            ):
                found.setdefault(n.attr, []).append(f"{f.name}:{n.lineno}")
    return found


def _mixin_methods() -> set[str]:
    """Every method defined on a ``*Mixin`` class in this package.

    The discriminator for the ``getattr`` form below. A name defined on a mixin was written to
    be reached FROM the channel; a name defined on ``CallbackHandler`` itself was not.
    """
    found: set[str] = set()
    for f in sorted(CHANNELS.glob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            if not cls.name.endswith("Mixin"):
                continue
            for fn in cls.body:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.add(fn.name)
    return found


def _getattr_channel_targets() -> dict[str, list[str]]:
    """``getattr(self.channel, "name", None)`` -- the form the attribute scan CANNOT see.

    ``self.channel._x`` is an Attribute node; ``getattr(self.channel, "_x", None)`` is a
    string, so the main scan is blind to it. That blindness is the more dangerous half: an
    Attribute access RAISES when the name is missing, while this form returns ``None`` and the
    house pattern is ``if handler:`` -- which falls through and returns, silently.

    Two live cases when this was written, both after the callback had already been answered:
    the kill-confirm button said "Killing…" and killed nothing, and the /ai panel never
    re-rendered after a tier pick, so the choice looked like it had not registered.
    """
    found: dict[str, list[str]] = {}
    for f in _scanned_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "getattr"
                and len(n.args) >= 2
                and isinstance(n.args[0], ast.Attribute)
                and n.args[0].attr in _CHANNEL_HOLDER_NAMES
                and isinstance(n.args[0].value, ast.Name)
                and n.args[0].value.id == "self"
                and isinstance(n.args[1], ast.Constant)
                and isinstance(n.args[1].value, str)
            ):
                found.setdefault(n.args[1].value, []).append(f"{f.name}:{n.lineno}")
    return found


def test_no_silently_degrading_getattr_handler() -> None:
    """A mixin method reached by ``getattr`` must be bound, or the button does nothing.

    Scoped to names defined on a ``*Mixin``, and that scope is the whole reason this is
    readable rather than noisy. Measured when written: 17 getattr sites, 3 unresolvable, and
    the third -- ``_handle_task_callback`` -- is a DELIBERATE optional override. Its docstring
    says so ("delegating when channel-level handler exists") and a complete fallback
    implementation follows it. It lives on ``CallbackHandler``, not a mixin, so this rule
    excludes it structurally rather than by an allowlist that would need maintaining.
    """
    have = _channel_attributes()
    mixin = _mixin_methods()
    broken = {
        name: where
        for name, where in _getattr_channel_targets().items()
        if name not in have and name in mixin
    }
    detail = "\n".join(f"    getattr(self.channel, {n!r}) <- {', '.join(w)}"
                        for n, w in sorted(broken.items()))
    assert not broken, (
        "these resolve to None, so `if handler:` falls through and the button silently does "
        "nothing -- after the user has already been told it is working:\n" + detail + "\n\n"
        "Bind them in _CALLBACK_REACHABLE_COMMAND_METHODS in telegram.py."
    )


def test_the_getattr_scan_finds_something() -> None:
    """Anti-vacuity for the scan above, and a check that its scope stays honest."""
    targets = _getattr_channel_targets()
    assert len(targets) >= 10, (
        f"only {len(targets)} getattr targets found — the walk is broken"
    )
    assert "_handle_task_callback" in targets, (
        "the deliberate optional override must still be SEEN by the scan and excluded by the "
        "mixin rule — if it stops being seen, the rule is no longer what excludes it"
    )
    assert "_handle_task_callback" not in _mixin_methods(), (
        "_handle_task_callback moved onto a Mixin; it is an optional override and would now "
        "be reported as a broken button"
    )


def _dispatch_table_targets() -> dict[str, list[str]]:
    """Method names held in a literal dict and resolved through a VARIABLE.

    The third form, and the one no earlier scan could follow::

        _NAV = {"st_goto_settings": "_handle_settings_hub", ...}
        method = getattr(self.channel, _NAV[cb_data], None)
        if method:                       # <- falls through, after _answer() already fired

    The name never appears next to ``self.channel``, so neither the attribute scan nor the
    getattr-literal scan sees it. Resolved here by reading the dict LITERALS instead: any
    ``{str: str}`` mapping in this package whose values look like private handler names.

    Four live, rendered buttons were dead this way -- "⚙️ All settings", "🤖 Providers &
    Models", "🎤 Voice API Keys" and "🎯 Focus". Three more in the same table were already
    bound, by an earlier fix that did not know this table existed.
    """
    found: dict[str, list[str]] = {}
    for f in _scanned_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            pairs = [
                v.value
                for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
                and isinstance(v, ast.Constant) and isinstance(v.value, str)
                and v.value.startswith("_handle_")
            ]
            # Two or more entries: one lone pair is as likely to be prose as a dispatch table.
            if len(pairs) >= 2:
                for name in pairs:
                    found.setdefault(name, []).append(f"{f.name}:{node.lineno}")
    return found


def test_no_dead_button_in_a_dispatch_table() -> None:
    """A handler named in a dispatch table must exist on the channel."""
    have = _channel_attributes()
    broken = {n: w for n, w in _dispatch_table_targets().items() if n not in have}
    detail = "\n".join(f"    {n}  <- table at {', '.join(w)}"
                        for n, w in sorted(broken.items()))
    assert not broken, (
        "these are named in a callback dispatch table but do not exist on TelegramChannel, so "
        "`if method:` falls through and the button does nothing -- after the spinner has "
        "already been dismissed:\n" + detail + "\n\n"
        "Bind them in _CALLBACK_REACHABLE_COMMAND_METHODS in telegram.py."
    )


def test_the_dispatch_table_scan_finds_the_nav_table() -> None:
    """Anti-vacuity, as a presence: name entries this scan must be seeing."""
    targets = _dispatch_table_targets()
    for required in ("_handle_settings_hub", "_handle_providers_and_models", "_handle_mode"):
        assert required in targets, (
            f"{required} is in the settings _NAV table but the dict scan no longer sees it"
        )
    assert len(targets) >= 5, f"only {len(targets)} dispatch targets found — the walk is broken"


def test_the_scan_finds_something() -> None:
    """Anti-vacuity: a scan that matched nothing would pass every assertion below."""
    calls = _cross_object_calls()
    assert len(calls) >= 20, (
        f"only {len(calls)} cross-object calls found — the walk is broken, and a guard that "
        f"sees nothing reports nothing"
    )


def test_the_scan_reaches_the_whole_surface_not_one_directory() -> None:
    """A scope floor, stated as a PRESENCE.

    This guard used to read ``self.channel`` inside ``channels/`` only. Both halves of that
    were wrong: a TelegramChannel is also held as ``self._channel`` (telegram_progress,
    telegram_renderer), and one holder lives a directory up in ``gateway/notifications.py``.
    Neither hole had a finding in it, so this is a floor placed BEFORE something falls
    through -- not a fix.

    Asserted as "these specific files are in scope", never as "no file is missing": a floor
    phrased as an absence passes trivially the moment the walk is misrooted and returns
    nothing at all.
    """
    scanned = {f.name for f in _scanned_files()}
    for required in ("notifications.py", "telegram_progress.py", "telegram_renderer.py",
                     "telegram_keyboards.py"):
        assert required in scanned, (
            f"{required} holds a TelegramChannel but the scan does not reach it"
        )
    assert "telegram.py" not in scanned, "the channel itself is not a consumer of itself"
    assert any(f.parent.name != "channels" for f in _scanned_files()), (
        "every scanned file is inside channels/ — the walk was re-narrowed to one directory"
    )


def test_no_new_unresolvable_channel_call() -> None:
    """The one that matters: an eleventh broken button fails the build."""
    have = _channel_attributes()
    calls = _cross_object_calls()
    broken = {name: where for name, where in calls.items() if name not in have}
    new = {n: w for n, w in broken.items() if n not in KNOWN_BROKEN}
    detail = "\n".join(f"    self.channel.{n}  <- {', '.join(w)}" for n, w in sorted(new.items()))
    assert not new, (
        "these call TelegramChannel attributes that do not exist, so the button raises "
        "AttributeError when pressed:\n" + detail + "\n\n"
        "TelegramChannel does NOT inherit its mixins (MRO is [TelegramChannel, object]); a "
        "mixin method needs an explicit binding on the channel. See _get_file_path in "
        "telegram.py for the pattern."
    )


def test_the_baseline_does_not_rot() -> None:
    """A name that got fixed must leave the list, or it stops meaning anything."""
    have = _channel_attributes()
    calls = _cross_object_calls()
    fixed = sorted(n for n in KNOWN_BROKEN if n in have)
    assert not fixed, (
        f"{fixed} now resolve — remove them from KNOWN_BROKEN so the list keeps describing "
        f"reality"
    )
    gone = sorted(n for n in KNOWN_BROKEN if n not in calls)
    assert not gone, (
        f"{gone} are no longer called at all — drop them from KNOWN_BROKEN rather than "
        f"carrying a entry for code that does not exist"
    )


def test_every_baselined_entry_says_what_it_breaks() -> None:
    """A bare name is how a baseline becomes a place to hide things.

    Not parametrized: over an empty KNOWN_BROKEN a parametrized test collects zero cases and
    is SKIPPED, which reads in the run output exactly like a check that ran. The baseline is
    empty today, and a test that silently stops running the moment it succeeds is the shape
    this file exists to catch.
    """
    undescribed = sorted(n for n, why in KNOWN_BROKEN.items() if not why.strip())
    assert not undescribed, f"{undescribed} have no description of what a user loses"


def test_the_lazy_init_recogniser_actually_recognises_something() -> None:
    """A floor. An empty baseline proves nothing if the exemption swallowed everything.

    ``_lazily_initialised_on_the_channel`` is what lets KNOWN_BROKEN be empty, so it is now
    load-bearing: a version that returned every name would make this guard permanently green
    and silent. Pin both ends -- it finds the one real lazy store, and it does NOT absorb the
    method names the guard exists to check.
    """
    lazy = _lazily_initialised_on_the_channel()
    assert "_pending_api_key_input" in lazy, (
        "the recogniser no longer sees the api-key store's `self.channel.X = {}` "
        "initialisation; KNOWN_BROKEN would grow an entry for a non-problem"
    )
    assert len(lazy) < 5, (
        f"the recogniser matched {len(lazy)} names ({sorted(lazy)}) — it is too broad and "
        f"would exempt real missing bindings"
    )
    for method in ("_handle_providers", "_edit_audio_and_reply", "_handle_voice_menu"):
        assert method not in lazy, (
            f"{method} is a bound METHOD, not a lazily-created store; the recogniser must "
            f"not exempt it or removing its binding would go unnoticed"
        )
