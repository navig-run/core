"""
NAVIG Image Generation Tool

AI-powered image generation using:
- OpenAI DALL-E 3
- Stability AI (Stable Diffusion)
- Local models (via ComfyUI/Automatic1111)

Features:
- Multiple provider support
- Image editing and variations
- Prompt enhancement
- Output management
"""

from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class ImageProvider(Enum):
    """Supported image generation providers."""

    OPENAI = "openai"  # DALL-E 3
    STABILITY = "stability"  # Stability AI
    LOCAL = "local"  # Local model (ComfyUI, A1111)


class ImageSize(Enum):
    """Standard image sizes."""

    SQUARE_SMALL = "256x256"
    SQUARE_MEDIUM = "512x512"
    SQUARE_LARGE = "1024x1024"
    LANDSCAPE = "1792x1024"
    PORTRAIT = "1024x1792"


class ImageQuality(Enum):
    """Image quality levels."""

    STANDARD = "standard"
    HD = "hd"


class ImageStyle(Enum):
    """Image style presets."""

    VIVID = "vivid"
    NATURAL = "natural"


@dataclass
class ImageGenerationConfig:
    """Configuration for image generation."""

    # Provider settings
    provider: ImageProvider = ImageProvider.OPENAI
    openai_api_key: str | None = None
    stability_api_key: str | None = None
    local_api_url: str = "http://localhost:7860"  # A1111/ComfyUI

    # Default generation parameters
    default_size: ImageSize = ImageSize.SQUARE_LARGE
    default_quality: ImageQuality = ImageQuality.STANDARD
    default_style: ImageStyle = ImageStyle.VIVID

    # Output settings
    output_dir: str = "~/.navig/images"
    save_locally: bool = True

    # Rate limiting
    max_concurrent: int = 2
    rate_limit_delay: float = 1.0

    @classmethod
    def from_env(cls) -> ImageGenerationConfig:
        """Create config from environment variables."""
        return cls(
            provider=ImageProvider(os.environ.get("IMAGE_PROVIDER", "openai")),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            stability_api_key=os.environ.get("STABILITY_API_KEY"),
            local_api_url=os.environ.get(
                "LOCAL_IMAGE_API_URL", "http://localhost:7860"
            ),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageGenerationConfig:
        """Create config from dictionary."""
        return cls(
            provider=ImageProvider(data.get("provider", "openai")),
            openai_api_key=data.get("openai_api_key"),
            stability_api_key=data.get("stability_api_key"),
            local_api_url=data.get("local_api_url", "http://localhost:7860"),
            default_size=ImageSize(data.get("default_size", "1024x1024")),
            default_quality=ImageQuality(data.get("default_quality", "standard")),
            default_style=ImageStyle(data.get("default_style", "vivid")),
            output_dir=data.get("output_dir", "~/.navig/images"),
            save_locally=data.get("save_locally", True),
        )


@dataclass
class GeneratedImage:
    """A generated image result."""

    prompt: str
    revised_prompt: str | None  # Provider's enhanced prompt
    provider: ImageProvider
    size: str

    # Image data (one of these will be set)
    url: str | None = None  # Remote URL
    b64_data: str | None = None  # Base64 encoded data
    local_path: str | None = None  # Local file path

    # Metadata
    generation_time: float = 0.0
    model: str | None = None
    seed: int | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "revised_prompt": self.revised_prompt,
            "provider": self.provider.value,
            "size": self.size,
            "url": self.url,
            "local_path": self.local_path,
            "generation_time": self.generation_time,
            "model": self.model,
            "seed": self.seed,
            "created_at": self.created_at.isoformat(),
        }

    async def save_to_file(self, filepath: str) -> str:
        """
        Save image to file.

        Args:
            filepath: Destination file path

        Returns:
            Actual saved path
        """
        path = Path(filepath).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        if self.b64_data:
            # Decode and save
            data = base64.b64decode(self.b64_data)
            path.write_bytes(data)
            self.local_path = str(path)
            return str(path)

        elif self.url and HTTPX_AVAILABLE:
            # Download from URL
            async with httpx.AsyncClient() as client:
                response = await client.get(self.url)
                response.raise_for_status()
                path.write_bytes(response.content)
                self.local_path = str(path)
                return str(path)

        raise ValueError("No image data available to save")


class ImageGenerator:
    """
    Multi-provider image generation client.

    Usage:
        generator = ImageGenerator()
        image = await generator.generate("A sunset over mountains")
    """

    def __init__(self, config: ImageGenerationConfig | None = None):
        """
        Initialize image generator.

        Args:
            config: Generation configuration
        """
        if not HTTPX_AVAILABLE:
            raise ImportError(
                "httpx is required for image generation. Install: pip install httpx"
            )

        self.config = config or ImageGenerationConfig.from_env()
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def generate(
        self,
        prompt: str,
        size: ImageSize | None = None,
        quality: ImageQuality | None = None,
        style: ImageStyle | None = None,
        n: int = 1,
        provider: ImageProvider | None = None,
        save: bool = True,
    ) -> list[GeneratedImage]:
        """
        Generate images from a text prompt.

        Args:
            prompt: Text description of the image
            size: Image size
            quality: Quality level
            style: Style preset
            n: Number of images to generate
            provider: Provider to use (overrides config)
            save: Whether to save locally

        Returns:
            List of GeneratedImage objects
        """
        provider = provider or self.config.provider
        size = size or self.config.default_size
        quality = quality or self.config.default_quality
        style = style or self.config.default_style

        async with self._semaphore:
            if provider == ImageProvider.OPENAI:
                images = await self._generate_openai(prompt, size, quality, style, n)
            elif provider == ImageProvider.STABILITY:
                images = await self._generate_stability(prompt, size, n)
            elif provider == ImageProvider.LOCAL:
                images = await self._generate_local(prompt, size, n)
            else:
                raise ValueError(f"Unsupported provider: {provider}")

        # Save locally if requested
        if save and self.config.save_locally:
            output_dir = Path(self.config.output_dir).expanduser()
            output_dir.mkdir(parents=True, exist_ok=True)

            for i, img in enumerate(images):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"image_{timestamp}_{i}.png"
                filepath = output_dir / filename

                try:
                    await img.save_to_file(str(filepath))
                except Exception as e:
                    logger.warning(f"Failed to save image: {e}")

        return images

    async def _generate_openai(
        self,
        prompt: str,
        size: ImageSize,
        quality: ImageQuality,
        style: ImageStyle,
        n: int,
    ) -> list[GeneratedImage]:
        """Generate images using OpenAI DALL-E."""
        api_key = self.config.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not configured")

        client = await self._get_client()

        start_time = datetime.now()

        response = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "dall-e-3",
                "prompt": prompt,
                "n": min(n, 1),  # DALL-E 3 only supports n=1
                "size": size.value,
                "quality": quality.value,
                "style": style.value,
                "response_format": "url",
            },
        )

        response.raise_for_status()
        data = response.json()

        generation_time = (datetime.now() - start_time).total_seconds()

        images = []
        for item in data.get("data", []):
            images.append(
                GeneratedImage(
                    prompt=prompt,
                    revised_prompt=item.get("revised_prompt"),
                    provider=ImageProvider.OPENAI,
                    size=size.value,
                    url=item.get("url"),
                    generation_time=generation_time,
                    model="dall-e-3",
                )
            )

        # DALL-E 3 only generates 1 image, so loop for multiple
        if n > 1:
            for _ in range(n - 1):
                await asyncio.sleep(self.config.rate_limit_delay)
                more = await self._generate_openai(prompt, size, quality, style, 1)
                images.extend(more)

        return images

    async def _generate_stability(
        self,
        prompt: str,
        size: ImageSize,
        n: int,
    ) -> list[GeneratedImage]:
        """Generate images using Stability AI."""
        api_key = self.config.stability_api_key or os.environ.get("STABILITY_API_KEY")
        if not api_key:
            raise ValueError("Stability API key not configured")

        client = await self._get_client()

        # Parse size
        width, height = map(int, size.value.split("x"))

        start_time = datetime.now()

        response = await client.post(
            "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "text_prompts": [{"text": prompt, "weight": 1.0}],
                "cfg_scale": 7,
                "height": height,
                "width": width,
                "samples": n,
                "steps": 30,
            },
        )

        response.raise_for_status()
        data = response.json()

        generation_time = (datetime.now() - start_time).total_seconds()

        images = []
        for item in data.get("artifacts", []):
            images.append(
                GeneratedImage(
                    prompt=prompt,
                    revised_prompt=None,
                    provider=ImageProvider.STABILITY,
                    size=size.value,
                    b64_data=item.get("base64"),
                    generation_time=generation_time,
                    model="stable-diffusion-xl-1024-v1-0",
                    seed=item.get("seed"),
                )
            )

        return images

    async def _generate_local(
        self,
        prompt: str,
        size: ImageSize,
        n: int,
    ) -> list[GeneratedImage]:
        """Generate images using local model (Automatic1111 API)."""
        client = await self._get_client()

        # Parse size
        width, height = map(int, size.value.split("x"))

        start_time = datetime.now()

        response = await client.post(
            f"{self.config.local_api_url}/sdapi/v1/txt2img",
            json={
                "prompt": prompt,
                "negative_prompt": "",
                "width": width,
                "height": height,
                "batch_size": n,
                "steps": 20,
                "cfg_scale": 7,
            },
        )

        response.raise_for_status()
        data = response.json()

        generation_time = (datetime.now() - start_time).total_seconds()

        images = []
        for b64_img in data.get("images", []):
            images.append(
                GeneratedImage(
                    prompt=prompt,
                    revised_prompt=None,
                    provider=ImageProvider.LOCAL,
                    size=size.value,
                    b64_data=b64_img,
                    generation_time=generation_time,
                    model="local",
                )
            )

        return images

    async def edit(
        self,
        image_path: str,
        prompt: str,
        mask_path: str | None = None,
    ) -> GeneratedImage:
        """
        Edit an existing image.

        Args:
            image_path: Path to image to edit
            prompt: Description of edit
            mask_path: Optional mask image for inpainting

        Returns:
            Edited GeneratedImage
        """
        # Only OpenAI supports image editing via API
        api_key = self.config.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key required for image editing")

        client = await self._get_client()

        # Read image file
        image_data = Path(image_path).read_bytes()

        files = {
            "image": ("image.png", image_data, "image/png"),
            "prompt": (None, prompt),
            "size": (None, self.config.default_size.value),
        }

        if mask_path:
            mask_data = Path(mask_path).read_bytes()
            files["mask"] = ("mask.png", mask_data, "image/png")

        start_time = datetime.now()

        response = await client.post(
            "https://api.openai.com/v1/images/edits",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
        )

        response.raise_for_status()
        data = response.json()

        generation_time = (datetime.now() - start_time).total_seconds()

        item = data.get("data", [{}])[0]
        return GeneratedImage(
            prompt=prompt,
            revised_prompt=None,
            provider=ImageProvider.OPENAI,
            size=self.config.default_size.value,
            url=item.get("url"),
            generation_time=generation_time,
            model="dall-e-2",  # Edit uses DALL-E 2
        )


# Convenience functions


async def generate_image(
    prompt: str,
    provider: str = "openai",
    size: str = "1024x1024",
    **kwargs,
) -> GeneratedImage:
    """
    Generate a single image from a prompt.

    Args:
        prompt: Text description
        provider: Provider name ("openai", "stability", "local")
        size: Image size
        **kwargs: Additional arguments

    Returns:
        GeneratedImage
    """
    config = ImageGenerationConfig.from_env()
    config.provider = ImageProvider(provider)

    generator = ImageGenerator(config)
    try:
        images = await generator.generate(
            prompt,
            size=(
                ImageSize(size)
                if size in [s.value for s in ImageSize]
                else ImageSize.SQUARE_LARGE
            ),
            n=1,
            **kwargs,
        )
        return images[0] if images else None
    finally:
        await generator.close()


def is_image_generation_available() -> bool:
    """Check if image generation is available."""
    if not HTTPX_AVAILABLE:
        return False

    # Check for API keys
    if os.environ.get("OPENAI_API_KEY"):
        return True
    if os.environ.get("STABILITY_API_KEY"):
        return True

    return False
