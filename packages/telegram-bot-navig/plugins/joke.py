"""plugins/joke.py — Tell a random joke. /joke"""

from __future__ import annotations

import random

from telegram import Update
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

JOKES = [
    ("Why do programmers prefer dark mode?", "Because light attracts bugs! 🐛"),
    (
        "How many programmers does it take to change a light bulb?",
        "None — that's a hardware problem.",
    ),
    ("Why did the developer go broke?", "Because they used up all their cache! 💸"),
    (
        "A SQL query walks into a bar,",
        "walks up to two tables and asks: 'Can I join you?'",
    ),
    ("Why do Python developers wear glasses?", "Because they can't C# 🕶️"),
    ("What's a computer's favourite snack?", "Micro-chips 🍟"),
    ("What did the CPU say to the RAM?", "'You can always count on me.'"),
    (
        "Why was the JavaScript developer sad?",
        "Because they didn't know how to 'null' their feelings.",
    ),
    ("What's the object-oriented way to get rich?", "Inheritance."),
    ("Why don't scientists trust atoms?", "Because they make up everything!"),
    (
        "A programmer's partner says: 'Go get milk. If they have eggs, get a dozen.'",
        "The programmer returns with 12 gallons of milk.",
    ),
    (
        "There are 10 types of people:",
        "those who understand binary and those who don't.",
    ),
    (
        "Why did the function break up with the loop?",
        "It didn't want to keep going in circles.",
    ),
    ("What do you call a programmer from Finland?", "Nerdic."),
    ("How do you comfort a JavaScript bug?", "You console it."),
]


class JokePlugin(BotPlugin):
    @property
    def meta(self):
        return PluginMeta("joke", "Tell a random programmer joke.", "1.0.0")

    @property
    def command(self):
        return "joke"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        setup, punchline = random.choice(JOKES)
        await update.message.reply_text(
            f"😄 _{setup}_\n\n*{punchline}*", parse_mode="Markdown"
        )


def create():
    return JokePlugin()
