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
    from navig.tools.image_generation import ImageGenerator
    gen = ImageGenerator()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, gen.generate(**kwargs)).result()
        else:
            result = asyncio.run(gen.generate(**kwargs))
    finally:
        asyncio.run(gen.close())
    return [{"url": img.url, "local_path": str(img.local_path) if img.local_path else None} for img in result]


def register_tools(registry: "ToolRegistry") -> None:
    from navig.tools.router import SafetyLevel, ToolDomain, ToolMeta

    registry.register(
        ToolMeta(
            name="image_generate",
            domain=ToolDomain.IMAGE,
            description="Generate images from text prompts (DALL-E, Stability, Local).",
            safety=SafetyLevel.MODERATE,
            parameters_schema={
                "prompt": {"type": "string", "required": True, "description": "Image description"},
                "size": {"type": "string", "default": "1024x1024", "description": "Image dimensions"},
                "n": {"type": "integer", "default": 1, "description": "Number of images"},
            },
            required_config=["OPENAI_API_KEY"],
            tags=["image", "generate", "creative"],
        ),
        handler=_sync_generate,
    )
