"""`navig voice` — speak text and transcribe audio, from the navig-audio plugin.

The commands and the TTS/STT engines live in navig-audio (``navig_audio.commands.voice``), so
``navig-audio voice …`` runs the same code without navig. When the plugin is not installed, each
command still exists and prints the one install line (exit 2) instead of "No such command".
"""

from __future__ import annotations

import typer

try:
    from navig_audio.commands.voice import voice_app
except ImportError:
    voice_app = typer.Typer(
        name="voice", help="Voice synthesis and recognition commands", no_args_is_help=True
    )

    def _needs_plugin(ctx: typer.Context) -> None:
        import importlib.util

        from navig import console_helper as ch
        from navig.plugins.require import install_hint

        if importlib.util.find_spec("navig_audio") is not None:
            # navig-audio < 0.2 has the engines but not these commands yet.
            ch.error(
                "Voice needs navig-audio 0.2 or newer.",
                "Update it:  pip install -U navig-audio",
            )
        else:
            ch.error(
                "Voice needs the navig-audio plugin (TTS/STT engines).",
                f"Install it:  {install_hint('navig-audio')}",
            )
        raise typer.Exit(2)

    for _name, _help in (
        ("speak", "Synthesize speech from text."),
        ("transcribe", "Transcribe audio file to text."),
        ("list-voices", "List available voices for a provider."),
    ):
        voice_app.command(
            _name,
            help=f"{_help} (needs navig-audio)",
            context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
        )(_needs_plugin)
