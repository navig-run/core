"""
Video Tool Pack - video_generate.

Wraps navig.tools.video_generation for the ToolRouter.
Note: video generation is async (submit→poll); the handler bridges async->sync.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


def _sync_generate(**kwargs):
    """Sync wrapper for async VideoGenerator.generate()."""
    from navig.tools.video_generation import VideoGenerator, VideoProvider

    provider = kwargs.pop("provider", None)
    if provider:
        kwargs["provider"] = VideoProvider(provider)

    gen = VideoGenerator()

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
        "url": result.url,
        "local_path": str(result.local_path) if result.local_path else None,
        "provider": result.provider.value,
        "model": result.model,
    }


def register_tools(registry: ToolRegistry) -> None:
    from navig.tools.router import SafetyLevel, ToolDomain, ToolMeta

    registry.register(
        ToolMeta(
            name="video_generate",
            domain=ToolDomain.VIDEO,
            description=(
                "Generate a video from a text prompt (optionally seeded with an image). "
                "Providers: gemini_veo (free tier), replicate, runway, luma."
            ),
            safety=SafetyLevel.MODERATE,
            parameters_schema={
                "prompt": {
                    "type": "string",
                    "required": True,
                    "description": "Video description",
                },
                "provider": {
                    "type": "string",
                    "description": "gemini_veo | replicate | runway | luma (omit for default)",
                },
                "image_url": {
                    "type": "string",
                    "description": "Optional seed image URL for image-to-video",
                },
            },
            required_config=[],
            tags=["video", "generate", "creative", "veo"],
        ),
        handler=_sync_generate,
    )
