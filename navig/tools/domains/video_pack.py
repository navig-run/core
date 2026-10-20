"""
Video Tool Pack - video_generate.

Wraps navig.tools.video_generation for the ToolRouter.
Video generation is async (submit→poll); the handler is async and the ToolRouter
awaits it (async_execute) or drives it (execute).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


def _safe_seed_image(image_url: str) -> str:
    """A model-supplied seed image must be an http(s) URL or a file under a NAVIG root.

    The ElevenLabs Flows driver inlines a LOCAL path's bytes into its request body,
    so a model-chosen absolute path (``/etc/passwd``, a .env, anything) would be read
    and uploaded to the provider — a prompt-injection exfil channel. Local reads are
    therefore confined to the media library and the active space's refs, exactly
    where generated/kept images live; anything else fails here, before the request.
    Fail-closed: if neither root can be resolved, local paths are refused.
    """
    if image_url.startswith(("http://", "https://")):
        return image_url

    from pathlib import Path

    from navig.platform.paths import resolve_user_path

    p = resolve_user_path(image_url)
    roots: list[Path] = []
    try:
        from navig_sdk.host import media_dir

        # Per-KIND media dirs, not the data/config home: media_dir(kind) resolves
        # both the new <data>/<kind> and the legacy <config>/<kind> layout, while
        # the homes themselves keep the vault and session stores — which must
        # never be an allowed seed-image read.
        roots.extend(Path(media_dir(kind)) for kind in ("images", "videos", "audio"))
    except Exception:  # noqa: BLE001 — media home unavailable → fewer allowed roots
        pass
    try:
        from navig.media.refs_library import resolve_refs_root

        roots.append(Path(resolve_refs_root()))
    except Exception:  # noqa: BLE001 — no active space → media home only
        pass
    if any(p.is_relative_to(root) for root in roots):
        return str(p)
    allowed = " · ".join(str(r) for r in roots) or "none available"
    raise ValueError(
        f"seed image must be an http(s) URL or a file inside the media library / "
        f"space refs (allowed roots: {allowed}) — got {image_url!r}"
    )


async def _generate(**kwargs):
    """Run VideoGenerator.generate() — an async tool handler (no sync bridge)."""
    from navig.tools.video_generation import VideoGenerator, VideoProvider

    provider = kwargs.pop("provider", None)
    if provider:
        kwargs["provider"] = VideoProvider(provider)
    if kwargs.get("image_url"):
        kwargs["image_url"] = _safe_seed_image(str(kwargs["image_url"]))

    gen = VideoGenerator()

    # generate + close on the SAME loop (the client is bound to it). An async handler:
    # the router awaits it in async_execute and drives it in a loop-less thread for
    # execute(). The old sync bridge blocked the caller's event loop on .result() for
    # the whole render.
    try:
        result = await gen.generate(**kwargs)
    finally:
        await gen.close()
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
                "Providers: gemini_veo (free tier), replicate, runway, luma, "
                "elevenlabs (ElevenLabs Flows — Pro plan or above)."
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
                    "description": (
                        "gemini_veo | replicate | runway | luma | elevenlabs "
                        "(omit for default)"
                    ),
                },
                "image_url": {
                    "type": "string",
                    "description": (
                        "Optional seed image for image-to-video: an http(s) URL, or a "
                        "local file path inside the space's refs or the media library"
                    ),
                },
            },
            required_config=[],
            tags=["video", "generate", "creative", "veo"],
        ),
        handler=_generate,
    )
