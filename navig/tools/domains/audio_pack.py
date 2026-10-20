"""
Audio Tool Pack - audio_generate.

Wraps navig.tools.audio_generation for the ToolRouter.
The handler is async; the ToolRouter awaits it (async_execute) or drives it (execute).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


async def _generate(**kwargs):
    """Run AudioGenerator.generate() — an async tool handler (no sync bridge)."""
    from navig.tools.audio_generation import AudioGenerator

    gen = AudioGenerator()

    # generate + close on the SAME loop (the client is bound to it). An async handler:
    # the router awaits it in async_execute and drives it in a loop-less thread for
    # execute(). The old sync bridge blocked the caller's event loop on .result() for
    # the whole render.
    try:
        result = await gen.generate(**kwargs)
    finally:
        await gen.close()
    return {
        "local_path": str(result.local_path) if result.local_path else None,
        "kind": result.kind.value,
        "provider": result.provider.value,
        "model": result.model,
    }


def register_tools(registry: ToolRegistry) -> None:
    from navig.tools.router import SafetyLevel, ToolDomain, ToolMeta

    registry.register(
        ToolMeta(
            name="audio_generate",
            domain=ToolDomain.AUDIO,
            description=(
                "Generate audio from a text prompt via ElevenLabs. "
                "kind: music | sfx | tts."
            ),
            safety=SafetyLevel.MODERATE,
            parameters_schema={
                "prompt": {
                    "type": "string",
                    "required": True,
                    "description": "What to generate (song brief, sound description, or text to speak)",
                },
                "kind": {
                    "type": "string",
                    "default": "music",
                    "description": "music | sfx | tts",
                },
                "duration_s": {
                    "type": "number",
                    "description": "Optional target duration in seconds (music/sfx)",
                },
            },
            required_config=[],
            tags=["audio", "music", "tts", "sfx", "elevenlabs"],
        ),
        handler=_generate,
    )
