"""
Image Tool Pack - image_generate.

Wraps navig.tools.image_generation for the ToolRouter.
Note: image generation is async; the handler bridges async->sync.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


def _sync_generate(**kwargs):
    """Sync wrapper for async ImageGenerator.generate()."""
    from navig.tools.image_generation import ImageGenerator, ImageProvider

    # Map a friendly provider string onto the enum (default: config/env).
    provider = kwargs.pop("provider", None)
    if provider:
        kwargs["provider"] = ImageProvider(provider)

    gen = ImageGenerator()

    async def _run():
        # generate + close on the SAME loop (the client is bound to it).
        try:
            return await gen.generate(**kwargs)
        finally:
            await gen.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — safe to call asyncio.run() directly
        result = asyncio.run(_run())
    else:
        # Running inside an active event loop — use a thread to avoid blocking it
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = pool.submit(asyncio.run, _run()).result()
    return [
        {"url": img.url, "local_path": str(img.local_path) if img.local_path else None}
        for img in result
    ]


def register_tools(registry: ToolRegistry) -> None:
    from navig.tools.router import SafetyLevel, ToolDomain, ToolMeta

    registry.register(
        ToolMeta(
            name="image_generate",
            domain=ToolDomain.IMAGE,
            description=(
                "Generate images from text prompts. Providers: recraft (raster+vector), "
                "openai_gpt_image, openai (dall-e-3), gemini_flash, gemini_pro, stability, local."
            ),
            safety=SafetyLevel.MODERATE,
            parameters_schema={
                "prompt": {
                    "type": "string",
                    "required": True,
                    "description": "Image description",
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "recraft | openai_gpt_image | openai | gemini_flash | gemini_pro | "
                        "stability | local (omit to use configured default)"
                    ),
                },
                "size": {
                    "type": "string",
                    "default": "1024x1024",
                    "description": "Image dimensions",
                },
                "n": {
                    "type": "integer",
                    "default": 1,
                    "description": "Number of images",
                },
            },
            # No single hard requirement — any one provider key enables the tool.
            required_config=[],
            tags=["image", "generate", "creative", "recraft", "gemini"],
        ),
        handler=_sync_generate,
    )
