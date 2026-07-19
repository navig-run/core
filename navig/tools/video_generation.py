"""
NAVIG Video Generation Tool

Text-to-video (and image-to-video) across providers:
- Google Veo (via the Gemini API)   — free tier through Google AI Studio
- Replicate                         — hosted catalog (Kling, Luma, Wan, …)
- Runway (Gen-4)                    — cinematic, paid
- Luma Dream Machine (Ray)          — dreamy motion, paid

All four are asynchronous jobs (submit → poll → download). Each driver submits
the job, polls until the video URL is ready (or times out), and returns a
`GeneratedVideo`. Keys resolve through the shared media resolver (env → vault),
so a key stored in the Deck vault works without exporting an env var.

Nothing here modifies or deletes any source; it only produces new files.
"""

from __future__ import annotations

import asyncio
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
from navig.tools.media_providers import resolve_media_key

logger = get_debug_logger()


class VideoProvider(Enum):
    """Supported video generation providers."""

    GEMINI_VEO = "gemini_veo"
    REPLICATE = "replicate"
    RUNWAY = "runway"
    LUMA = "luma"


@dataclass
class VideoGenerationConfig:
    """Configuration for video generation."""

    provider: VideoProvider = VideoProvider.GEMINI_VEO

    # Model overrides per provider (release ids drift; keep swappable).
    veo_model: str = "veo-3.0-generate-preview"
    replicate_model: str = "kwaivgi/kling-v2.1"
    runway_model: str = "gen4_turbo"
    luma_model: str = "ray-2"

    # Job polling
    poll_interval: float = 5.0
    poll_timeout: float = 300.0  # 5 min — video jobs are slow

    # Output
    output_dir: str = field(
        default_factory=lambda: str(media_dir("videos"))
    )
    save_locally: bool = True

    @classmethod
    def from_env(cls) -> VideoGenerationConfig:
        return cls(provider=VideoProvider(os.environ.get("VIDEO_PROVIDER", "gemini_veo")))


@dataclass
class GeneratedVideo:
    """A generated video result."""

    prompt: str
    provider: VideoProvider
    url: str | None = None
    local_path: str | None = None
    model: str | None = None
    duration_s: float | None = None
    generation_time: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "provider": self.provider.value,
            "url": self.url,
            "local_path": self.local_path,
            "model": self.model,
            "duration_s": self.duration_s,
            "generation_time": self.generation_time,
            "created_at": self.created_at.isoformat(),
        }

    async def save_to_file(self, filepath: str, client: httpx.AsyncClient) -> str:
        """Download the video URL to a local file."""
        if not self.url:
            raise ValueError("No video URL available to save")
        path = Path(filepath).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        resp = await client.get(self.url)
        resp.raise_for_status()
        path.write_bytes(resp.content)
        self.local_path = str(path)
        return str(path)


class VideoGenerator:
    """Multi-provider video generation client."""

    def __init__(self, config: VideoGenerationConfig | None = None):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for video generation. Install: pip install httpx")
        self.config = config or VideoGenerationConfig.from_env()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def generate(
        self,
        prompt: str,
        provider: VideoProvider | None = None,
        image_url: str | None = None,
        save: bool = True,
        seed: int | None = None,
    ) -> GeneratedVideo:
        """Generate a video from a text prompt (optionally seeded with an image).

        `seed` is honored by Replicate models that accept it; Veo/Runway/Luma
        have no public seed param and ignore it (best-effort reproducibility).
        """
        provider = provider or self.config.provider

        if provider == VideoProvider.GEMINI_VEO:
            video = await self._generate_veo(prompt, image_url)
        elif provider == VideoProvider.REPLICATE:
            video = await self._generate_replicate(prompt, image_url, seed)
        elif provider == VideoProvider.RUNWAY:
            video = await self._generate_runway(prompt, image_url)
        elif provider == VideoProvider.LUMA:
            video = await self._generate_luma(prompt, image_url)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

        if save and self.config.save_locally and video.url:
            out = Path(self.config.output_dir).expanduser()
            out.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                await video.save_to_file(str(out / f"video_{ts}.mp4"), await self._get_client())
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to save video: %s", e)
        return video

    # ── Google Veo (Gemini API, long-running operation) ──────────────────────
    async def _generate_veo(self, prompt: str, image_url: str | None) -> GeneratedVideo:
        api_key = resolve_media_key("google", "GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("Google (Gemini) API key not configured")
        client = await self._get_client()
        model = self.config.veo_model
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        start = datetime.now()

        # 1) Submit the long-running predict operation.
        resp = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning",
            headers=headers,
            json={"instances": [{"prompt": prompt}]},
        )
        resp.raise_for_status()
        op_name = resp.json().get("name")
        if not op_name:
            raise RuntimeError("Veo: no operation name returned")

        # 2) Poll the operation until done.
        deadline = start.timestamp() + self.config.poll_timeout
        while True:
            await asyncio.sleep(self.config.poll_interval)
            op = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/{op_name}",
                headers=headers,
            )
            op.raise_for_status()
            body = op.json()
            if body.get("done"):
                resp_body = body.get("response", {})
                samples = (
                    resp_body.get("generateVideoResponse", {}).get("generatedSamples")
                    or resp_body.get("generatedSamples")
                    or []
                )
                url = None
                if samples:
                    url = (samples[0].get("video") or {}).get("uri") or samples[0].get("uri")
                # Veo download URIs need the key appended.
                if url and "key=" not in url:
                    url = f"{url}{'&' if '?' in url else '?'}key={api_key}"
                return GeneratedVideo(
                    prompt=prompt,
                    provider=VideoProvider.GEMINI_VEO,
                    url=url,
                    model=model,
                    generation_time=(datetime.now() - start).total_seconds(),
                )
            if datetime.now().timestamp() > deadline:
                raise TimeoutError("Veo generation timed out")

    # ── Replicate (predictions API) ──────────────────────────────────────────
    async def _generate_replicate(
        self, prompt: str, image_url: str | None, seed: int | None = None
    ) -> GeneratedVideo:
        token = resolve_media_key("replicate", "REPLICATE_API_TOKEN")
        if not token:
            raise ValueError("Replicate API token not configured")
        client = await self._get_client()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        start = datetime.now()
        model_input: dict[str, Any] = {"prompt": prompt}
        if seed is not None:
            model_input["seed"] = seed
        if image_url:
            model_input["start_image"] = image_url

        # Uses the model-scoped predictions endpoint (official model slug).
        resp = await client.post(
            f"https://api.replicate.com/v1/models/{self.config.replicate_model}/predictions",
            headers=headers,
            json={"input": model_input},
        )
        resp.raise_for_status()
        pred = resp.json()
        get_url = pred.get("urls", {}).get("get")

        deadline = start.timestamp() + self.config.poll_timeout
        while pred.get("status") not in ("succeeded", "failed", "canceled"):
            if datetime.now().timestamp() > deadline:
                raise TimeoutError("Replicate generation timed out")
            await asyncio.sleep(self.config.poll_interval)
            poll = await client.get(get_url, headers=headers)
            poll.raise_for_status()
            pred = poll.json()

        if pred.get("status") != "succeeded":
            raise RuntimeError(f"Replicate job {pred.get('status')}: {pred.get('error')}")
        output = pred.get("output")
        url = output[0] if isinstance(output, list) and output else output
        return GeneratedVideo(
            prompt=prompt,
            provider=VideoProvider.REPLICATE,
            url=url,
            model=self.config.replicate_model,
            generation_time=(datetime.now() - start).total_seconds(),
        )

    # ── Runway (Gen-4, tasks API) ────────────────────────────────────────────
    async def _generate_runway(self, prompt: str, image_url: str | None) -> GeneratedVideo:
        key = resolve_media_key("runway", "RUNWAYML_API_SECRET", "RUNWAY_API_KEY")
        if not key:
            raise ValueError("Runway API key not configured")
        client = await self._get_client()
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Runway-Version": "2024-11-06",
        }
        start = datetime.now()
        # Runway's text→video path requires a seed image for image_to_video;
        # a promptText-only job uses text_to_video where available.
        endpoint = "image_to_video" if image_url else "text_to_video"
        payload: dict[str, Any] = {"model": self.config.runway_model, "promptText": prompt}
        if image_url:
            payload["promptImage"] = image_url
        resp = await client.post(
            f"https://api.dev.runwayml.com/v1/{endpoint}", headers=headers, json=payload
        )
        resp.raise_for_status()
        task_id = resp.json().get("id")

        deadline = start.timestamp() + self.config.poll_timeout
        while True:
            if datetime.now().timestamp() > deadline:
                raise TimeoutError("Runway generation timed out")
            await asyncio.sleep(self.config.poll_interval)
            poll = await client.get(
                f"https://api.dev.runwayml.com/v1/tasks/{task_id}", headers=headers
            )
            poll.raise_for_status()
            task = poll.json()
            status = task.get("status")
            if status == "SUCCEEDED":
                output = task.get("output") or []
                url = output[0] if output else None
                return GeneratedVideo(
                    prompt=prompt,
                    provider=VideoProvider.RUNWAY,
                    url=url,
                    model=self.config.runway_model,
                    generation_time=(datetime.now() - start).total_seconds(),
                )
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"Runway task {status}: {task.get('failure')}")

    # ── Luma Dream Machine (generations API) ─────────────────────────────────
    async def _generate_luma(self, prompt: str, image_url: str | None) -> GeneratedVideo:
        key = resolve_media_key("luma", "LUMAAI_API_KEY", "LUMA_API_KEY")
        if not key:
            raise ValueError("Luma API key not configured")
        client = await self._get_client()
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        start = datetime.now()
        payload: dict[str, Any] = {"prompt": prompt, "model": self.config.luma_model}
        if image_url:
            payload["keyframes"] = {"frame0": {"type": "image", "url": image_url}}
        resp = await client.post(
            "https://api.lumalabs.ai/dream-machine/v1/generations", headers=headers, json=payload
        )
        resp.raise_for_status()
        gen_id = resp.json().get("id")

        deadline = start.timestamp() + self.config.poll_timeout
        while True:
            if datetime.now().timestamp() > deadline:
                raise TimeoutError("Luma generation timed out")
            await asyncio.sleep(self.config.poll_interval)
            poll = await client.get(
                f"https://api.lumalabs.ai/dream-machine/v1/generations/{gen_id}", headers=headers
            )
            poll.raise_for_status()
            gen = poll.json()
            state = gen.get("state")
            if state == "completed":
                url = (gen.get("assets") or {}).get("video")
                return GeneratedVideo(
                    prompt=prompt,
                    provider=VideoProvider.LUMA,
                    url=url,
                    model=self.config.luma_model,
                    generation_time=(datetime.now() - start).total_seconds(),
                )
            if state == "failed":
                raise RuntimeError(f"Luma generation failed: {gen.get('failure_reason')}")


async def generate_video(
    prompt: str,
    provider: str = "gemini_veo",
    image_url: str | None = None,
    **kwargs,
) -> GeneratedVideo:
    """Generate a single video from a prompt. Returns a GeneratedVideo."""
    config = VideoGenerationConfig.from_env()
    config.provider = VideoProvider(provider)
    gen = VideoGenerator(config)
    try:
        return await gen.generate(prompt, image_url=image_url, **kwargs)
    finally:
        await gen.close()


def is_video_generation_available() -> bool:
    """True when at least one video provider has a resolvable key."""
    if not HTTPX_AVAILABLE:
        return False
    from navig.tools.media_providers import MEDIA_CATALOG, key_status

    return any(key_status(entry) for entry in MEDIA_CATALOG["video"])
