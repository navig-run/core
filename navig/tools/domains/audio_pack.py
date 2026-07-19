"""
Audio Tool Pack - audio_generate.

Wraps navig.tools.audio_generation for the ToolRouter.
Note: audio generation is async; the handler bridges async->sync.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


def _sync_generate(**kwargs):
    """Sync wrapper for async AudioGenerator.generate()."""
    from navig.tools.audio_generation import AudioGenerator

    gen = AudioGenerator()

    async def _run():
        # generate + close on the SAME loop (the client is bound to it).
        try:
            return await gen.generate(**kwargs)
        finally:
            await gen.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        result = asyncio.run(_run())
    else:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = pool.submit(asyncio.run, _run()).result()
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
        handler=_sync_generate,
    )
