"""The channel-neutral half of the Extensions gate must stay neutral.

`extension_gate` exists so a second channel can REUSE the precedence rule instead
of copying it. That value is destroyed the moment something Telegram-specific
lands in it: the next channel reads the file, sees `telegram` in it, decides the
module is not for them, and writes their own. Two implementations of "is this
feature switched on?" is how "off" starts meaning different things depending on
where the operator taps.

The failure is silent by construction — a Telegram reference in there breaks
nothing, passes every other test, and is only ever discovered by the person who
gives up on reusing the module. So it is asserted here instead.

⚠ This is a rule about the SOURCE, not about behaviour, and it is checked on the
AST rather than with a substring scan: this file's own prose says "telegram"
several times, and so does the neutral module's docstring, which explains why the
split exists. A textual rule would have to forbid the explanation of itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CHANNELS = REPO / "core" / "navig" / "gateway" / "channels"
GATE = CHANNELS / "extension_gate.py"

# Every channel the gateway ships. A neutral module may not name any of them.
_CHANNEL_WORDS = ("telegram", "discord", "whatsapp", "matrix", "slack", "signal")


def _tree() -> ast.Module:
    return ast.parse(GATE.read_text(encoding="utf-8"))


def _identifiers_and_strings(tree: ast.Module) -> list[tuple[int, str]]:
    """Every name, attribute, import and string literal — but NOT a docstring.

    Docstrings are prose: the module's own explanation of why it is separate from
    the Telegram catalog necessarily names it. Code and data are what must stay
    neutral.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.arg):
            found.append((node.lineno, node.arg))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            found.append((node.lineno, node.module or ""))
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.append((node.lineno, node.value))
    return found


def test_the_gate_names_no_channel() -> None:
    offenders = [
        f"line {lineno}: {text!r}"
        for lineno, text in _identifiers_and_strings(_tree())
        if any(word in text.lower() for word in _CHANNEL_WORDS)
    ]
    assert not offenders, (
        "extension_gate.py must not name a specific channel — the whole point is that a "
        "second channel can call it unchanged:\n  " + "\n  ".join(offenders) + "\n"
        "Push the channel-specific part back into that channel's catalog module and pass "
        "the facts in as arguments."
    )


def test_the_gate_imports_nothing_from_a_channel_module() -> None:
    """A neutral module may not depend on a channel module — dependencies run one way.

    Named separately from the rule above because it is the one that would make the
    module genuinely unusable elsewhere rather than merely misleading.
    """
    tree = _tree()
    bad: list[str] = []
    for node in ast.walk(tree):
        module = ""
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
        elif isinstance(node, ast.Import):
            module = ",".join(a.name for a in node.names)
        if "navig.gateway.channels" in module:
            bad.append(f"line {node.lineno}: {module}")
    assert not bad, (
        "extension_gate.py imports from a channel module, so it is not neutral:\n  "
        + "\n  ".join(bad)
    )


def test_the_gate_is_stdlib_only_at_module_scope() -> None:
    """`navig help` must answer in under 50 ms.

    The Telegram catalog documents this rule for itself and now imports this module
    at module scope, so the constraint transfers here: a navig import at the top of
    this file becomes part of every `navig help`.
    """
    tree = _tree()
    module_level = {id(n) for n in tree.body}
    heavy: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if id(node) not in module_level:
            continue  # function-scoped, which is the documented way to do it
        name = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
        if (name or "").startswith("navig"):
            heavy.append(f"line {node.lineno}: {name}")
    assert not heavy, (
        "these navig imports sit at module scope and are paid on every `navig help`:\n  "
        + "\n  ".join(heavy)
        + "\nMove them inside the function that needs them (# noqa: PLC0415)."
    )


def test_telegram_no_longer_carries_its_own_copy_of_the_resolver() -> None:
    """Anti-vacuity, and the point of the whole change.

    The three rules above are assertions about EMPTY lists — exactly the shape that
    keeps passing if the extraction is reverted and `extension_gate` is left behind
    as a file nothing calls. This one fails in that case.
    """
    catalog = (CHANNELS / "telegram_extensions.py").read_text(encoding="utf-8")
    assert "from navig.gateway.channels.extension_gate import" in catalog, (
        "telegram_extensions.py no longer imports the neutral resolver — either the "
        "extraction was reverted, or a second copy of the precedence rule now exists."
    )
    assert "modules.overrides." not in catalog, (
        "telegram_extensions.py reads modules.overrides directly again; the precedence "
        "rule has to live in exactly one place or the channels will drift apart."
    )
