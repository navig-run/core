from pathlib import Path
from typing import Optional

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

voice_app = typer.Typer(
    name="voice",
    help="Voice synthesis and recognition commands",
    no_args_is_help=True,
)


@voice_app.command("speak")
def speak_command(
    text: str = typer.Argument(..., help="Text to speak"),
    provider: str = typer.Option(
        None, "--provider", "-p", help="TTS Provider (openai, elevenlabs, edge)"
    ),
    voice: str = typer.Option(None, "--voice", "-v", help="Voice ID/Name"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file path (.mp3)"
    ),
    play: bool = typer.Option(True, "--play/--no-play", help="Play audio immediately"),
):
    """Synthesize speech from text."""
    import asyncio

    from navig.voice.tts import TTSProvider, get_tts

    async def _run():
        tts = get_tts()

        # Resolve provider
        prov_enum = None
        if provider:
            try:
                prov_enum = TTSProvider(provider.lower())
            except ValueError:
                ch.warning(f"Unknown provider '{provider}', using default")

        ch.info(f"Synthesizing: '{text}'...")

        result = await tts.synthesize(
            text, provider=prov_enum, voice=voice, output_path=output
        )

        if result.success:
            ch.success(f"Audio saved to: {result.audio_path}")
            if result.provider:
                ch.info(f"Provider: {result.provider.value}, Voice: {result.voice}")

            if play and result.audio_path:
                try:
                    from navig.voice.playback import play_sound

                    success = await play_sound(result.audio_path)
                    if not success:
                        ch.warning(
                            "Playback failed (check audio device or ffmpeg/mpv installation)"
                        )
                except Exception as e:
                    ch.warning(f"Playback error: {e}")
        else:
            ch.error(f"TTS Failed: {result.error}")

    asyncio.run(_run())


@voice_app.command("transcribe")
def transcribe_command(
    file: Path = typer.Argument(..., help="Audio file path"),
    provider: str = typer.Option(None, "--provider", "-p", help="STT Provider"),
):
    """Transcribe audio file to text."""
    import asyncio

    from navig.voice.stt import STTProvider, get_stt

    async def _run():
        stt = get_stt()

        prov_enum = None
        if provider:
            try:
                prov_enum = STTProvider(provider.lower())
            except ValueError:
                ch.error(f"Unknown provider: {provider}")
                return

        ch.info(f"Transcribing {file}...")
        result = await stt.transcribe(file, provider=prov_enum)

        if result.success:
            ch.success("Transcription complete:")
            ch.console.print(f"[bold]{result.text}[/bold]")
            if result.confidence:
                ch.dim(f"Confidence: {result.confidence:.2f}")
        else:
            ch.error(f"Transcription failed: {result.error}")

    asyncio.run(_run())


@voice_app.command("list-voices")
def list_voices(
    provider: str = typer.Argument(
        "openai", help="TTS Provider (openai, elevenlabs, edge)"
    ),
):
    """List available voices for a provider."""
    import asyncio

    from navig.voice.tts import TTSProvider, get_tts

    async def _run():
        tts = get_tts()
        try:
            prov_enum = TTSProvider(provider.lower())
        except ValueError:
            ch.error(f"Unknown provider: {provider}")
            return

        voices = await tts.list_voices(prov_enum)

        if not voices:
            ch.warning("No voices found or provider not configured.")
            return

        from rich.table import Table

        table = Table(title=f"Voices for {provider}")
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Language", style="yellow")

        for v in voices:
            table.add_row(v["id"], v["name"], v["language"])

        ch.console.print(table)

    asyncio.run(_run())
