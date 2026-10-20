"""
Image Tool Pack - image_generate.

Wraps navig.tools.image_generation for the ToolRouter.
The handler is async; the ToolRouter awaits it (async_execute) or drives it (execute).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


async def _generate(**kwargs):
    """Run ImageGenerator.generate() — an async tool handler (no sync bridge)."""
    from navig.tools.image_generation import ImageGenerator, ImageProvider

    # Map a friendly provider string onto the enum (default: config/env).
    provider = kwargs.pop("provider", None)
    if provider:
        kwargs["provider"] = ImageProvider(provider)

    gen = ImageGenerator()

    # generate + close on the SAME loop (the client is bound to it). An async handler:
    # the router awaits it in async_execute and drives it in a loop-less thread for
    # execute(). The old sync bridge blocked the caller's event loop on .result() for
    # the whole render.
    try:
        result = await gen.generate(**kwargs)
    finally:
        await gen.close()
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
                        "stability | local | elevenlabs (omit to use configured default)"
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
        handler=_generate,
    )
