"""Interactive AI chat implementation for the NAVIG CLI."""

from __future__ import annotations

from navig import console_helper as ch
from navig.console_helper import get_console


def run_ai_chat(initial_query: str | None = None, single_query: bool = False) -> None:
    """Run interactive AI chat or process a single query."""

    console = get_console()

    try:
        from navig.ai import AIAssistant
        from navig.config import get_config_manager

        ai = AIAssistant(get_config_manager())

        if single_query and initial_query:
            # Single query mode — run and exit
            import asyncio

            response = asyncio.run(ai.chat(initial_query, []))
            console.print(response)
            return

        # Interactive mode
        console.print("\n🤖 [bold cyan]NAVIG AI Chat[/bold cyan]")
        console.print("   Type your question or command. Type 'exit' or 'quit' to leave.\n")

        conversation: list[dict[str, str]] = []

        if initial_query:
            import asyncio

            console.print(f"[dim]You:[/dim] {initial_query}")
            response = asyncio.run(ai.chat(initial_query, conversation))
            console.print(f"\n{response}\n")
            conversation.append({"role": "user", "content": initial_query})
            conversation.append({"role": "assistant", "content": response})

        import asyncio

        while True:
            try:
                user_input = input("You: ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ("exit", "quit", "q", "bye"):
                    console.print("\n👋 Goodbye!")
                    break

                response = asyncio.run(ai.chat(user_input, conversation))
                console.print(f"\n{response}\n")

                conversation.append({"role": "user", "content": user_input})
                conversation.append({"role": "assistant", "content": response})

                # Keep conversation manageable
                if len(conversation) > 20:
                    conversation = conversation[-20:]

            except KeyboardInterrupt:
                console.print("\n👋 Goodbye!")
                break
            except EOFError:
                break

    except ImportError as e:
        ch.error(f"AI module not available: {e}")
        ch.info("Ensure NAVIG is installed correctly: pip install -e .")
    except Exception as e:
        ch.error(f"AI chat error: {e}")
