"""
sdk_examples.py — Developer guide and live runnable examples.

This file is both documentation and runnable code.
Run it directly to test all example plugins locally without connecting to Telegram:

    python sdk_examples.py

────────────────────────────────────────────────────────────────────────────────

STEP 1 — Understand the plugin contract
═══════════════════════════════════════

Every plugin:
  • Inherits BotPlugin from plugin_base.py
  • Implements three abstract members:
      meta     – PluginMeta(name, description, version)
      command  – Telegram command keyword, without /  (e.g. "rolldice")
      handle() – async coroutine that does the actual work

  • Exposes a module-level factory:
      def create() -> YourPlugin: ...

  • Lives in a single file inside the plugins/ directory.
    The filename doesn't need to match the command name — but it helps.

────────────────────────────────────────────────────────────────────────────────

STEP 2 — Minimal plugin skeleton
═════════════════════════════════
"""

from __future__ import annotations

# ── imports used for local testing ──────────────────────────────────────────
import asyncio
import html
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Make sibling imports work when run as a script
sys.path.insert(0, str(Path(__file__).parent))

from plugin_base import BotPlugin, PluginMeta

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

# ── Fake Telegram primitives for local testing ───────────────────────────────
# In production, PTB provides real Update / Context objects.
# Here we mock just enough to run handlers locally.


def _fake_update(text: str = "/cmd") -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.edit_text = AsyncMock()
    return update


def _fake_context(*args: str) -> MagicMock:
    ctx = MagicMock()
    ctx.args = list(args)
    return ctx


# ────────────────────────────────────────────────────────────────────────────
# EXAMPLE A — /echo plugin
# Echoes whatever the user writes after the command.
# Demonstrates: accessing command arguments via context.args
# ────────────────────────────────────────────────────────────────────────────

from telegram import Update
from telegram.ext import ContextTypes


class EchoPlugin(BotPlugin):
    """Echo the user's message back to them."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="echo",
            description="Echo back the text you send.",
            version="1.0.0",
        )

    @property
    def command(self) -> str:
        return "echo"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /echo <your message>")
            return
        await update.message.reply_text(" ".join(args))


def create_echo() -> EchoPlugin:
    return EchoPlugin()


# ────────────────────────────────────────────────────────────────────────────
# EXAMPLE B — /pick plugin
# Picks a random item from a comma-separated list.
# Demonstrates: parsing raw message text beyond context.args
# ────────────────────────────────────────────────────────────────────────────

import random


class PickPlugin(BotPlugin):
    """Pick a random item from a comma-separated list."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="pick",
            description="Pick randomly from a list: /pick pizza, sushi, tacos",
            version="1.0.0",
        )

    @property
    def command(self) -> str:
        return "pick"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        raw = " ".join(context.args or [])
        items = [i.strip() for i in raw.split(",") if i.strip()]
        if not items:
            await update.message.reply_text("Usage: /pick option1, option2, option3")
            return
        choice = random.choice(items)
        await update.message.reply_text(f"🎯 I pick: <b>{html.escape(choice)}</b>", parse_mode="HTML")


def create_pick() -> PickPlugin:
    return PickPlugin()


# ────────────────────────────────────────────────────────────────────────────
# EXAMPLE C — /calc plugin
# Evaluates a basic arithmetic expression.
# Demonstrates: error handling inside handle(), returning error messages.
# ────────────────────────────────────────────────────────────────────────────

import ast
import operator as _op

_SAFE_OPS = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.Pow: _op.pow,
    ast.USub: _op.neg,
}


def _safe_eval(expr: str) -> float:
    """Evaluate a numeric expression using AST — no eval(), no exec()."""

    def _eval(node: ast.expr) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            op_fn = _SAFE_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op_fn(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return _op.neg(_eval(node.operand))
        raise ValueError(f"Unsupported expression node: {type(node).__name__}")

    tree = ast.parse(expr.strip(), mode="eval")
    return _eval(tree.body)


class CalcPlugin(BotPlugin):
    """Evaluate a math expression safely."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="calc",
            description="Evaluate a math expression, e.g. /calc 3 * (4 + 2)",
            version="1.0.0",
        )

    @property
    def command(self) -> str:
        return "calc"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        expr = " ".join(context.args or [])
        if not expr:
            await update.message.reply_text(
                "Usage: /calc <expression>   e.g. /calc 2 ** 10"
            )
            return
        try:
            result = _safe_eval(expr)
            # Format: drop trailing .0 for whole numbers
            formatted = f"{result:.10g}"
            await update.message.reply_text(
                f"<code>{html.escape(expr)}</code> = <b>{html.escape(formatted)}</b>", parse_mode="HTML"
            )
        except (ValueError, ZeroDivisionError, SyntaxError) as exc:
            await update.message.reply_text(f"⚠️ Could not evaluate: {exc}")


def create_calc() -> CalcPlugin:
    return CalcPlugin()


# ────────────────────────────────────────────────────────────────────────────
# STEP 3 — Register plugins with a live PluginLoader
# (This shows wiring, not running — run bot.py to actually connect to Telegram)
# ────────────────────────────────────────────────────────────────────────────

"""
from telegram.ext import Application
from plugin_loader import PluginLoader

app = Application.builder().token("YOUR_BOT_TOKEN").build()

loader = PluginLoader(app, plugins_dir="plugins")
loader.load_all()  # discovers all *.py files in plugins/

# You can also add SDK example plugins manually:
# (They need to be saved as files in plugins/ to be auto-discovered)

app.run_polling()
"""

# ────────────────────────────────────────────────────────────────────────────
# STEP 4 — Local test runner (no Telegram required)
# Run this file directly: python sdk_examples.py
# ────────────────────────────────────────────────────────────────────────────


async def _run_local_tests() -> None:
    print("\n" + "═" * 60)
    print("  telegram-bot-navig SDK — local plugin test runner")
    print("═" * 60 + "\n")

    plugins = [
        (
            "echo",
            create_echo(),
            _fake_update("/echo hello world"),
            _fake_context("hello", "world"),
        ),
        ("echo", create_echo(), _fake_update("/echo"), _fake_context()),
        (
            "pick",
            create_pick(),
            _fake_update("/pick"),
            _fake_context("pizza", " sushi", " tacos"),
        ),
        ("calc", create_calc(), _fake_update("/calc"), _fake_context("2", "**", "10")),
        ("calc", create_calc(), _fake_update("/calc"), _fake_context("10", "/", "0")),
    ]

    # Test disabled state using the wrap-around __call__
    disabled_plugin = create_echo()
    disabled_plugin.disable()
    plugins.append(
        (
            "echo (disabled)",
            disabled_plugin,
            _fake_update("/echo hi"),
            _fake_context("hi"),
        )
    )

    for label, plugin, update, ctx in plugins:
        print(f"▶ /{label}")
        await plugin(update, ctx)
        calls = update.message.reply_text.call_args_list
        if calls:
            for call in calls:
                print(f"  → {call.args[0]!r}")
        else:
            print("  → (no reply)")
        update.message.reply_text.reset_mock()
        print()

    print("All local tests complete ✓")


if __name__ == "__main__":
    asyncio.run(_run_local_tests())
