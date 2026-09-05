"""Interactive AI chat implementation for the NAVIG CLI."""

from __future__ import annotations

from typing import Any

import typer

from navig import console_helper as ch
from navig.console_helper import get_console


def _chat_context() -> dict[str, Any]:
    """Minimal environment context for ``AIAssistant.ask``.

    ``ask()`` expects a context *dict* (client OS, active server, …) — not a list of
    messages. Client platform matters: without it the model answers a Windows
    operator with bash commands.
    """
    from pathlib import Path

    context: dict[str, Any] = {"directory": Path.cwd().as_posix()}

    try:
        from navig.commands.ai import _client_platform_context

        context["client_os"], context["client_arch"] = _client_platform_context()
    except Exception:  # pragma: no cover — ask() falls back to platform.system()
        pass

    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        if server_name := cm.get_active_server():
            context["server"] = cm.load_server_config(server_name)
    except Exception:  # no active server is normal — chat works without one
        pass

    return context


def run_ai_chat(initial_query: str | None = None, single_query: bool = False) -> None:
    """Run interactive AI chat or process a single query."""

    console = get_console()

    try:
        from navig.ai import AIAssistant
        from navig.config import get_config_manager

        ai = AIAssistant(get_config_manager())
        context = _chat_context()

        # AIAssistant exposes ask(), not chat(), and it is SYNCHRONOUS. Every call
        # here used to be `asyncio.run(ai.chat(text, conversation))`, which raised
        # AttributeError into the handler below — so `navig chat` only ever printed
        # "AI chat error: 'AIAssistant' object has no attribute 'chat'". ask() keeps
        # its own conversation history (ConversationStore), so no list is threaded.
        if single_query and initial_query:
            console.print(ai.ask(initial_query, context))
            return

        # Interactive mode
        console.print("\n🤖 [bold cyan]NAVIG AI Chat[/bold cyan]")
        console.print("   Type your question or command. Type 'exit' or 'quit' to leave.\n")

        if initial_query:
            console.print(f"[dim]You:[/dim] {initial_query}")
            console.print(f"\n{ai.ask(initial_query, context)}\n")

        while True:
            try:
                user_input = input("You: ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ("exit", "quit", "q", "bye"):
                    console.print("\n👋 Goodbye!")
                    break

                console.print(f"\n{ai.ask(user_input, context)}\n")

            except KeyboardInterrupt:
                console.print("\n👋 Goodbye!")
                break
            except EOFError:
                break

    except ImportError as e:
        ch.error(f"AI module not available: {e}")
        ch.info("Ensure NAVIG is installed correctly: pip install -e .")
        raise typer.Exit(1) from e
    except Exception as e:
        ch.error(f"AI chat error: {e}")
        raise typer.Exit(1) from e
