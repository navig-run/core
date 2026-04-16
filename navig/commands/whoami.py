"""
navig.commands.whoami — Display the user's NAVIG entity sigil card.

Usage:
    navig whoami

Loads the persisted identity, re-derives the full NaviEntity from the seed,
and renders the Rich sigil card in the terminal. If no identity exists yet,
prompts the user to run `navig onboard` first.
"""

from __future__ import annotations

from navig.console_helper import get_console


def run_whoami() -> None:
    from navig.identity.entity import derive_entity
    from navig.identity.renderer import render_sigil_card
    from navig.identity.sigil_store import load_entity

    data = load_entity()
    if not data:
        try:


            get_console().print(
                "\n[bold yellow]No entity found.[/bold yellow]  "
                "Run [bold cyan]navig onboard[/bold cyan] to generate your identity sigil.\n"
            )
        except ImportError:
            print("No entity found.  Run `navig onboard` to generate your identity sigil.")
        return

    entity = derive_entity(data["seed"])
    render_sigil_card(entity)
