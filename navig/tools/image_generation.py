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

from navig.platform.paths import media_dir

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
    OPENAI_GPT_IMAGE = "openai_gpt_image"  # gpt-image (generate + edit, b64)
    RECRAFT = "recraft"  # Recraft V3 — raster + vector
    GEMINI_FLASH = "gemini_flash"  # Google gemini-2.5-flash-image
    GEMINI_PRO = "gemini_pro"  # Google gemini-3-pro-image
    STABILITY = "stability"  # Stability AI
    LOCAL = "local"  # Local model (ComfyUI, A1111)


# Which ImageProvider a bare vault/media key label maps to (for the smart default).
_IMAGE_KEY_TO_PROVIDER: dict[str, ImageProvider] = {
    "openai": ImageProvider.OPENAI,
    "recraft": ImageProvider.RECRAFT,
    "google": ImageProvider.GEMINI_FLASH,
    "stability": ImageProvider.STABILITY,
}


def _config_get(*dotted_keys: str) -> str | None:
    """Return the first present global-config value among ``dotted_keys``, or
    ``None``. Walks the same ``global_config`` dict that ``navig config get``
    reads, so ``navig config set generate.<key> <value>`` sticks. Never raises."""
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager().global_config
    except Exception:  # noqa: BLE001 — config is optional; never break generation
        return None
    for dotted in dotted_keys:
        node: Any = cfg
        ok = True
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if ok and node:
            return str(node)
    return None


def _config_image_provider() -> str | None:
    """The persistent default image provider (``generate.image_provider`` →
    ``media.image_provider``), or ``None``."""
    return _config_get("generate.image_provider", "media.image_provider")


def _resolve_default_provider(keys: dict[str, str | None]) -> ImageProvider:
    """Pick the default image provider for ``navig generate``.

    Priority: explicit ``IMAGE_PROVIDER`` env → persistent config
    (``generate.image_provider`` / ``media.image_provider``) → smart default.

    The smart default is the fix for "I configured a Recraft key but it still
    says *OpenAI API key not configured*": if OpenAI (the historical hardcoded
    default) has no key yet exactly one other provider's key IS configured, use
    that provider instead of blindly erroring. Key storage and provider
    selection used to be fully decoupled, so a configured key had no effect.
    """
    explicit = os.environ.get("IMAGE_PROVIDER") or _config_image_provider()
    if explicit:
        try:
            return ImageProvider(str(explicit).strip().lower())
        except ValueError:
            logger.warning(
                "Ignoring invalid image provider %r — valid options: %s",
                explicit, ", ".join(p.value for p in ImageProvider),
            )

    if keys.get("openai"):
        return ImageProvider.OPENAI
    configured = [p for p in ("recraft", "google", "stability") if keys.get(p)]
    if len(configured) == 1:
        picked = _IMAGE_KEY_TO_PROVIDER[configured[0]]
        logger.info(
            "No OpenAI image key configured — defaulting image generation to %s "
            "(the provider you have a key for). Pin it with "
            "`navig config set generate.image_provider %s`.",
            picked.value, picked.value,
        )
        return picked
    # Nothing (or ambiguously several) configured → keep the historical default;
    # the provider-specific "key not configured" error will guide the user.
    return ImageProvider.OPENAI


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
    recraft_api_key: str | None = None
    gemini_api_key: str | None = None
    stability_api_key: str | None = None
    local_api_url: str = "http://localhost:7860"  # A1111/ComfyUI

    # Model overrides (release ids can change; keep them swappable via config)
    openai_image_model: str = "gpt-image-1"  # set "gpt-image-2" once available on your account
    recraft_model: str = "recraftv3"
    gemini_flash_model: str = "gemini-2.5-flash-image"
    gemini_pro_model: str = "gemini-3-pro-image"

    # Default generation parameters
    default_size: ImageSize = ImageSize.SQUARE_LARGE
    default_quality: ImageQuality = ImageQuality.STANDARD
    default_style: ImageStyle = ImageStyle.VIVID

    # Output settings — respects NAVIG_DATA_DIR and expands ~ correctly.
    output_dir: str = field(
        default_factory=lambda: str(media_dir("images"))
    )
    save_locally: bool = True

    # Rate limiting
    max_concurrent: int = 2
    rate_limit_delay: float = 1.0

    @classmethod
    def from_env(cls) -> ImageGenerationConfig:
        """Create config from environment variables + the NAVIG vault.

        Keys resolve through the shared media resolver (env → vault), so a key
        stored in the Deck vault UI works without exporting an env var.
        """
        from navig.tools.media_providers import resolve_media_key

        keys: dict[str, str | None] = {
            "openai": resolve_media_key("openai", "OPENAI_API_KEY"),
            "recraft": resolve_media_key("recraft", "RECRAFT_API_KEY"),
            "google": resolve_media_key("google", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "stability": resolve_media_key("stability", "STABILITY_API_KEY"),
        }
        # Model ids are swappable — release ids change (e.g. Recraft V4.1). Honor
        # an env var / `navig config set generate.<key>` override, else the default.
        defaults = cls()
        return cls(
            provider=_resolve_default_provider(keys),
            openai_api_key=keys["openai"],
            recraft_api_key=keys["recraft"],
            gemini_api_key=keys["google"],
            stability_api_key=keys["stability"],
            local_api_url=os.environ.get("LOCAL_IMAGE_API_URL", "http://localhost:7860"),
            openai_image_model=os.environ.get("OPENAI_IMAGE_MODEL")
            or _config_get("generate.openai_image_model")
            or defaults.openai_image_model,
            recraft_model=os.environ.get("RECRAFT_MODEL")
            or _config_get("generate.recraft_model")
            or defaults.recraft_model,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageGenerationConfig:
        """Create config from dictionary."""
        return cls(
            provider=ImageProvider(data.get("provider", "openai")),
            openai_api_key=data.get("openai_api_key"),
            recraft_api_key=data.get("recraft_api_key"),
            gemini_api_key=data.get("gemini_api_key"),
            stability_api_key=data.get("stability_api_key"),
            local_api_url=data.get("local_api_url", "http://localhost:7860"),
            default_size=ImageSize(data.get("default_size", "1024x1024")),
            default_quality=ImageQuality(data.get("default_quality", "standard")),
            default_style=ImageStyle(data.get("default_style", "vivid")),
            output_dir=data.get("output_dir") or str(media_dir("images")),
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
            raise ImportError("httpx is required for image generation. Install: pip install httpx")

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
        seed: int | None = None,
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
            seed: Optional generation seed. Honored by providers that support it
                (Stability, local A1111); ignored by providers without a seed
                param (OpenAI, Recraft, Gemini) — reproducibility is best-effort.

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
            elif provider == ImageProvider.OPENAI_GPT_IMAGE:
                images = await self._generate_openai_gpt_image(prompt, size, quality, n)
            elif provider == ImageProvider.RECRAFT:
                images = await self._generate_recraft(prompt, size, n)
            elif provider in (ImageProvider.GEMINI_FLASH, ImageProvider.GEMINI_PRO):
                images = await self._generate_gemini(prompt, provider, n)
            elif provider == ImageProvider.STABILITY:
                images = await self._generate_stability(prompt, size, n, seed)
            elif provider == ImageProvider.LOCAL:
                images = await self._generate_local(prompt, size, n, seed)
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
                    logger.warning("Failed to save image: %s", e)

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

    async def _generate_openai_gpt_image(
        self,
        prompt: str,
        size: ImageSize,
        quality: ImageQuality,
        n: int,
    ) -> list[GeneratedImage]:
        """Generate images using OpenAI gpt-image (returns base64, supports edits)."""
        api_key = self.config.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not configured")

        client = await self._get_client()
        # gpt-image accepts 1024x1024, 1536x1024, 1024x1536, or "auto".
        gpt_size = size.value if size.value in {"1024x1024", "1536x1024", "1024x1536"} else "1024x1024"
        # gpt-image quality vocabulary: low | medium | high (map hd→high).
        gpt_quality = "high" if quality == ImageQuality.HD else "medium"

        start_time = datetime.now()
        response = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.openai_image_model,
                "prompt": prompt,
                "n": max(1, min(n, 4)),
                "size": gpt_size,
                "quality": gpt_quality,
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
                    provider=ImageProvider.OPENAI_GPT_IMAGE,
                    size=gpt_size,
                    b64_data=item.get("b64_json"),
                    url=item.get("url"),
                    generation_time=generation_time,
                    model=self.config.openai_image_model,
                )
            )
        return images

    async def _generate_recraft(
        self,
        prompt: str,
        size: ImageSize,
        n: int,
    ) -> list[GeneratedImage]:
        """Generate images using Recraft V3 (raster or vector).

        Uses the `recraft_model` config field — set it to "recraftv3_vector" for
        SVG/vector output. Recraft's OpenAI-compatible endpoint returns URLs.
        """
        api_key = self.config.recraft_api_key or os.environ.get("RECRAFT_API_KEY")
        if not api_key:
            raise ValueError("Recraft API key not configured")

        client = await self._get_client()
        # Recraft accepts 1024x1024, 1365x1024, 1024x1365, 1536x1024, etc.
        recraft_size = size.value if size.value in {
            "1024x1024", "1024x1792", "1792x1024",
        } else "1024x1024"

        start_time = datetime.now()
        response = await client.post(
            "https://external.api.recraft.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "prompt": prompt,
                "model": self.config.recraft_model,
                "size": recraft_size,
                "n": max(1, min(n, 4)),
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
                    revised_prompt=None,
                    provider=ImageProvider.RECRAFT,
                    size=recraft_size,
                    url=item.get("url"),
                    b64_data=item.get("b64_json"),
                    generation_time=generation_time,
                    model=self.config.recraft_model,
                )
            )
        return images

    async def _generate_gemini(
        self,
        prompt: str,
        provider: ImageProvider,
        n: int,
    ) -> list[GeneratedImage]:
        """Generate images using Google Gemini (Flash Image / Pro Image).

        Uses the Generative Language API `generateContent`; inline image bytes
        come back base64-encoded on the response parts.
        """
        api_key = self.config.gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get(
            "GOOGLE_API_KEY"
        )
        if not api_key:
            raise ValueError("Google (Gemini) API key not configured")

        model = (
            self.config.gemini_pro_model
            if provider == ImageProvider.GEMINI_PRO
            else self.config.gemini_flash_model
        )
        client = await self._get_client()
        start_time = datetime.now()

        images: list[GeneratedImage] = []
        # Gemini generateContent returns one image per call — loop for n.
        for _ in range(max(1, min(n, 4))):
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseModalities": ["IMAGE"]},
                },
            )
            response.raise_for_status()
            data = response.json()
            generation_time = (datetime.now() - start_time).total_seconds()

            for cand in data.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        images.append(
                            GeneratedImage(
                                prompt=prompt,
                                revised_prompt=None,
                                provider=provider,
                                size="1024x1024",
                                b64_data=inline["data"],
                                generation_time=generation_time,
                                model=model,
                            )
                        )
            if n <= 1:
                break
            await asyncio.sleep(self.config.rate_limit_delay)
        return images

    async def _generate_stability(
        self,
        prompt: str,
        size: ImageSize,
        n: int,
        seed: int | None = None,
    ) -> list[GeneratedImage]:
        """Generate images using Stability AI."""
        api_key = self.config.stability_api_key or os.environ.get("STABILITY_API_KEY")
        if not api_key:
            raise ValueError("Stability API key not configured")

        client = await self._get_client()

        # Parse size
        width, height = map(int, size.value.split("x"))

        start_time = datetime.now()

        body: dict[str, Any] = {
            "text_prompts": [{"text": prompt, "weight": 1.0}],
            "cfg_scale": 7,
            "height": height,
            "width": width,
            "samples": n,
            "steps": 30,
        }
        if seed is not None:
            body["seed"] = seed

        response = await client.post(
            "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=body,
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
        seed: int | None = None,
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
                "seed": seed if seed is not None else -1,
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
        Instruction-edit an existing image (OpenAI gpt-image edits endpoint).

        With no mask, the whole image is edited to follow the prompt ("make this
        more normal, no cape"). With a mask, only the masked region is inpainted.

        Args:
            image_path: Path to image to edit
            prompt: The edit instruction
            mask_path: Optional PNG mask (transparent = area to change)

        Returns:
            Edited GeneratedImage (base64)
        """
        api_key = self.config.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key required for image editing")

        client = await self._get_client()
        image_data = Path(image_path).read_bytes()

        files = {
            "image": ("image.png", image_data, "image/png"),
            "prompt": (None, prompt),
            "model": (None, self.config.openai_image_model),
        }
        if mask_path:
            files["mask"] = ("mask.png", Path(mask_path).read_bytes(), "image/png")

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
            revised_prompt=item.get("revised_prompt"),
            provider=ImageProvider.OPENAI_GPT_IMAGE,
            size=self.config.default_size.value,
            url=item.get("url"),
            b64_data=item.get("b64_json"),
            generation_time=generation_time,
            model=self.config.openai_image_model,
        )

    async def remove_background(self, image_path: str) -> GeneratedImage:
        """Remove the background of an image → transparent PNG (Recraft).

        Recraft's removeBackground is the reliable, production path for
        transparent-background objects (gpt-image can't do transparency).
        """
        api_key = self.config.recraft_api_key or os.environ.get("RECRAFT_API_KEY")
        if not api_key:
            raise ValueError("Recraft API key required for background removal")

        client = await self._get_client()
        image_data = Path(image_path).read_bytes()
        start_time = datetime.now()
        response = await client.post(
            "https://external.api.recraft.com/v1/images/removeBackground",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("image.png", image_data, "image/png"),
                   "response_format": (None, "url")},
        )
        response.raise_for_status()
        data = response.json()
        generation_time = (datetime.now() - start_time).total_seconds()

        img = data.get("image") or (data.get("data") or [{}])[0]
        return GeneratedImage(
            prompt="[remove background]",
            revised_prompt=None,
            provider=ImageProvider.RECRAFT,
            size=self.config.default_size.value,
            url=img.get("url"),
            b64_data=img.get("b64_json"),
            generation_time=generation_time,
            model="recraft-removeBackground",
        )

    async def img2img(
        self,
        image_path: str,
        prompt: str,
        strength: float = 0.4,
    ) -> GeneratedImage:
        """Reference-based redesign: transform an image toward a prompt (Recraft).

        `strength` in [0,1] — how far to move from the source (low = subtle
        redesign, high = loose reinterpretation). Great for "redesign this civic
        character in our world's style".
        """
        api_key = self.config.recraft_api_key or os.environ.get("RECRAFT_API_KEY")
        if not api_key:
            raise ValueError("Recraft API key required for image-to-image")

        client = await self._get_client()
        image_data = Path(image_path).read_bytes()
        start_time = datetime.now()
        response = await client.post(
            "https://external.api.recraft.com/v1/images/imageToImage",
            headers={"Authorization": f"Bearer {api_key}"},
            files={
                "file": ("image.png", image_data, "image/png"),
                "prompt": (None, prompt),
                "strength": (None, str(strength)),
                "model": (None, self.config.recraft_model),
                "response_format": (None, "url"),
            },
        )
        response.raise_for_status()
        data = response.json()
        generation_time = (datetime.now() - start_time).total_seconds()

        img = data.get("image") or (data.get("data") or [{}])[0]
        return GeneratedImage(
            prompt=prompt,
            revised_prompt=None,
            provider=ImageProvider.RECRAFT,
            size=self.config.default_size.value,
            url=img.get("url"),
            b64_data=img.get("b64_json"),
            generation_time=generation_time,
            model=self.config.recraft_model,
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
                ImageSize(size) if size in [s.value for s in ImageSize] else ImageSize.SQUARE_LARGE
            ),
            n=1,
            **kwargs,
        )
        return images[0] if images else None
    finally:
        await generator.close()


def is_image_generation_available() -> bool:
    """Check if image generation is available (any provider key resolvable)."""
    if not HTTPX_AVAILABLE:
        return False

    from navig.tools.media_providers import MEDIA_CATALOG, key_status

    return any(key_status(entry) for entry in MEDIA_CATALOG["image"])
