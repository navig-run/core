"""A documented `navig config set` toggle must be read through `coerce_bool`.

`navig config set <k> <v>` stores the CLI argument VERBATIM as a string, so
``navig config set browser.enabled false`` writes ``"false"`` — and ``bool("false")`` is
``True``. A consumer that tests the value directly therefore sees a *disabled* feature as
*enabled*. Proven through the real code path before this guard was written:

    BrowserConfig.from_config({"browser": {"enabled": "false"}})
      before -> enabled = 'false'   (the string: every `if cfg.enabled:` keeps it ON)
      after  -> enabled = False

Seventeen such reads existed across agent/, approval/, browser/, desktop/, gateway/,
memory/ and the telegram channels — every one a toggle the docs tell users to set.

SCOPE, and why it is drawn here. There are ~63 raw boolean config reads in total, but
most read keys that are only ever written programmatically as real bools, where the
coercion buys nothing. A guard at "all 63" could not be at zero today, and a guard with a
baseline allowlist is the shape that lets known holes read as covered. So this guards
exactly the subset where the bug is REACHABLE: keys a user is documented as being able to
set from the command line. That subset is at zero, and it grows itself — document a new
`navig config set x.enabled` and any raw read of it starts failing.

There is a SECOND rule below, for negative-polarity security controls, with a lower bar:
it matches on the key NAME and accepts the no-default form `cfg.get("trust_new_host")`.
That form carried three of that key's five sites and the narrow rule structurally cannot
see it — which is exactly why the worst instances of this bug hid from the first rule.

Both rules scan core AND every first-party plugin: plugins read the same config through
the same ConfigManager, so stopping at core/ would leave the bug live where a user is
just as likely to meet it.

COMPANION GUARD: `test_bool_coercion_canonical.py` guards the other half of this problem
— helper *definitions* (a new `_coerce_bool`/`_truthy`/`_as_bool` must delegate to
`coerce_bool` or be allowlisted with a written rationale). This file guards *read sites*.
Neither subsumes the other: a read can be raw while every helper delegates, and a helper
can fork a fourth truth table while every read is wrapped. Keep both wired.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "core" / "navig"
PLUGINS = ROOT / "plugins"
DOC_ROOTS = [ROOT / "docs", ROOT / "core", ROOT / ".github"]
_SKIP_PARTS = {"tests", "build", "dist", ".venv", "node_modules", "__pycache__"}


def _scanned_files() -> list[Path]:
    """Core plus every first-party plugin package.

    Plugins read the same config through the same ConfigManager, so a rule that
    stopped at core/ would leave `navig config set proactive.email.enabled false`
    broken in exactly the place a user is most likely to set it. Three such reads
    existed when this was widened; the security rule found none there.
    """
    files = [p for p in CORE.rglob("*.py") if not _SKIP_PARTS & set(p.parts)]
    if PLUGINS.is_dir():
        for pkg in sorted(PLUGINS.glob("navig-*")):
            files += [p for p in pkg.rglob("*.py") if not _SKIP_PARTS & set(p.parts)]
    return sorted(files)


def _rel(path: Path) -> str:
    try:
        return path.relative_to(CORE).as_posix()
    except ValueError:
        return path.relative_to(ROOT).as_posix()

_DOC_TOGGLE = re.compile(r"navig config set ([a-z_]+\.[a-z_.]+)")
# Matched against the receiver's RENDERED name, so `cfg`, `self.config` and `gw.settings`
# all qualify. It is anchored at the end because a tail-only match would let a mechanical
# fix rewrite `self.config.get(...)` into `self.coerce_bool(config.get(...))` — which is
# exactly how the first pass at this corrupted four files.
_RECEIVER = re.compile(r"(?:^|\.)\w*(?:cfg|config|conf|settings)$")


def _documented_toggles() -> set[str]:
    found: set[str] = set()
    for root in DOC_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".md", ".py"} or not path.is_file():
                continue
            # RELATIVE to the doc root, not absolute parts: a git worktree lives under
            # .dev/worktrees/<slug>, so excluding ".dev" by absolute path part excluded
            # every file and the scraper found nothing — passing vacuously in the main
            # checkout while protecting nothing from a worktree.
            if {"tests", "build", "dist", ".dev", "node_modules"} & set(
                path.relative_to(root).parts
            ):
                continue
            try:
                found |= set(_DOC_TOGGLE.findall(path.read_text(encoding="utf-8-sig")))
            except (OSError, UnicodeDecodeError):
                continue
    return {t.rstrip(".") for t in found}


def _raw_reads_in(tree: ast.AST) -> list[tuple[ast.Call, str, int]]:
    """Real `<cfg>.get("key", True|False)` CALLS, excluding ones already wrapped.

    AST, not a line regex: `gateway/server.py` DOCUMENTS this very footgun in a
    docstring, and a textual scan flagged that prose as an offender — then a mechanical
    "fix" rewrote the sentence into nonsense. Only executable code can have this bug.
    """
    wrapped: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "coerce_bool"
            and node.args
        ):
            wrapped.add(id(node.args[0]))

    out: list[tuple[ast.Call, str, int]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "get" or len(node.args) != 2 or id(node) in wrapped:
            continue
        key, default = node.args
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        # Only a BOOLEAN default is a boolean read — `get("timeout", 120)` is a number.
        #
        # This requirement also excludes the no-default form `cfg.get("enabled")`, and
        # that exclusion is DELIBERATE — do not "fix" it. Extending to one argument was
        # measured, twice:
        #   * matching by leaf name alone -> 76 hits, and the overwhelming majority were
        #     not booleans at all (`api_key`, `host`, `url`, `bot_token`, `mesh_token`),
        #     because a documented toggle path like `telegram.bot_token` contributes its
        #     leaf to the name set. The boolean default is what keeps that clean.
        #   * narrowing to keys that carry boolean-default evidence somewhere in the tree
        #     -> exactly 9 hits, of which EIGHT were false positives: every one read a
        #     dict that had already been normalised upstream (telegram_worker's
        #     `_deck_config()`/`_matrix_config()`, ears' literal `{"enabled": True}`,
        #     deck auth's `configure_deck_auth`, onboarding's in-memory wizard dict).
        #     The ninth was real and is fixed (messaging/channel_config.py).
        # Nothing in the AST distinguishes a raw config dict from a normalised one, so a
        # one-argument rule can only demand redundant coercion — which teaches people to
        # wrap reads that do not need it and devalues the wrap where it does.
        # The security rule below DOES accept the no-default form, and can afford to:
        # its three key names were verified to be read only from raw config dicts.
        if not (isinstance(default, ast.Constant) and isinstance(default.value, bool)):
            continue
        receiver = ast.unparse(node.func.value)
        if not _RECEIVER.search(receiver):
            continue
        out.append((node, key.value, node.lineno))
    return out


def _uncoerced_reads() -> tuple[list[str], int]:
    toggles = _documented_toggles()
    # A section sub-dict is read with the LEAF key (`cloud_cfg.get("enabled")`) while the
    # doc names the full path (`cloud.enabled`), so both spellings have to be recognised.
    leaves = {t.rsplit(".", 1)[-1] for t in toggles}
    offenders: list[str] = []
    for path in _scanned_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node, key, lineno in _raw_reads_in(tree):
            if key in toggles or key in leaves:
                offenders.append(
                    f"{_rel(path)}:{lineno}  "
                    f"{ast.unparse(node.func.value)}.get({key!r}, ...)"
                )
    return offenders, len(toggles)


def test_the_scan_actually_reaches_core_and_the_plugins() -> None:
    """Widening the scan is worthless if the glob quietly matches nothing. The
    plugin tree is the half most likely to break — it is found by a `navig-*` glob
    against a directory that does not exist in a wheel install."""
    scanned = _scanned_files()
    assert sum(1 for p in scanned if CORE in p.parents) > 300, "core scan looks empty"
    plugin_files = [p for p in scanned if PLUGINS in p.parents]
    assert len(plugin_files) > 50, (
        f"only {len(plugin_files)} plugin files scanned — the `navig-*` glob has "
        "stopped matching, so every plugin is silently unguarded."
    )


def test_documented_toggles_are_read_through_coerce_bool() -> None:
    offenders, toggle_count = _uncoerced_reads()

    assert toggle_count > 20, (
        f"only {toggle_count} documented `navig config set` toggles found — the docs "
        "carry far more. The scraper is looking in the wrong place, so this guard would "
        "silently protect nothing."
    )
    assert not offenders, (
        "These read a documented `navig config set` toggle as a raw value. The CLI stores "
        'it as a STRING, and bool("false") is True — so setting the toggle to false leaves '
        "the feature ON:\n  "
        + "\n  ".join(offenders)
        + "\n\nWrap the read: coerce_bool(<cfg>.get(\"<key>\", <default>), default=<default>)"
    )


# ─────────────────── negative-polarity security controls ───────────────────
#
# A SECOND rule, with a lower bar than "documented", because these fail OPEN: True
# is the UNSAFE state, so a string value ("false", "no", "off") turns the control
# off-on-paper and on-in-fact. Measured before this rule existed:
#
#     server_config = {"trust_new_host": "false"}
#       -> StrictHostKeyChecking=accept-new     (SSH MITM protection DISABLED)
#
# It also accepts the NO-DEFAULT form. `cfg.get("trust_new_host")` carried three of
# that key's five sites, and the documented-toggle rule above cannot see them: it
# requires a boolean default as its evidence that a read is boolean at all. Here the
# key name is the evidence, so the default is not needed — which is precisely why
# the narrow rule missed the worst instances of the bug.
SECURITY_KEYS = {
    "trust_new_host": "SSH host-key policy — True accepts unknown keys (MITM)",
    "ignore_https_errors": "browser TLS verification — True ignores bad certs",
    "allow_insecure": "https-only guard on credential autofill — True types a "
    "vaulted password into an http:// page",
}


def _uncoerced_security_reads() -> list[str]:
    offenders: list[str] = []
    for path in _scanned_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        wrapped: set[int] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "coerce_bool"
                and node.args
            ):
                wrapped.add(id(node.args[0]))

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "get" or not node.args or id(node) in wrapped:
                continue
            key = node.args[0]
            if not (isinstance(key, ast.Constant) and key.value in SECURITY_KEYS):
                continue
            offenders.append(
                f"{_rel(path)}:{node.lineno}  "
                f"{ast.unparse(node)}  [{SECURITY_KEYS[key.value]}]"
            )
    return offenders


def test_negative_polarity_security_controls_cannot_fail_open() -> None:
    offenders = _uncoerced_security_reads()
    assert not offenders, (
        "These read a security control whose True state is the UNSAFE one, without "
        'coercion. bool("false") is True, so the operator (or a model) writing "false" '
        "turns the protection OFF:\n  "
        + "\n  ".join(offenders)
        + '\n\nWrap the read: coerce_bool(<src>.get("<key>"), default=False)'
    )


def test_the_security_rule_sees_a_read_with_no_default() -> None:
    """The form that hid three of the five trust_new_host sites. If this stops
    matching, the rule silently narrows to the one the other rule already covers."""
    offenders = []
    tree = ast.parse('if server_config.get("trust_new_host"):\n    pass\n')
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in SECURITY_KEYS
        ):
            offenders.append(node)
    assert len(offenders) == 1
    # ...and the narrow documented-toggle detector must NOT see it — that asymmetry
    # is the whole reason this second rule exists.
    assert _raw_reads_in(tree) == []


def _keys_found(src: str) -> list[str]:
    return [key for _, key, _ in _raw_reads_in(ast.parse(src))]


def test_the_detector_matches_a_dotted_receiver() -> None:
    """`self.config.get(...)` must be seen as ONE receiver. A tail-anchored match lets a
    mechanical fix rewrite it into `self.coerce_bool(config.get(...))` — which is how the
    first pass at this corrupted four files."""
    src = 'if not self.config.get("enabled", True):\n    return\n'
    assert _keys_found(src) == ["enabled"]


def test_an_already_coerced_read_is_not_flagged() -> None:
    src = 'x = coerce_bool(browser_cfg.get("enabled", True), default=True)\n'
    assert _keys_found(src) == []


def test_a_non_boolean_default_is_out_of_scope() -> None:
    """Only True/False defaults are boolean reads. `get("timeout", 120)` is a number and
    coercing it would be nonsense."""
    assert _keys_found('cfg.get("timeout_seconds", 120)\n') == []


def test_prose_that_mentions_the_pattern_is_not_code() -> None:
    """gateway/server.py DOCUMENTS this footgun in a docstring. A line-based scan flagged
    that sentence as an offender, and a mechanical fix then rewrote the prose into
    nonsense — which is why the detector runs on the AST."""
    src = '"""so a raw ``section_cfg.get("enabled", True)`` would leave it ON."""\n'
    assert _keys_found(src) == []
