"""Translate resolved image params into what one provider actually accepts.

A preset says *what it wants* (aspect 16:9, high quality, avoid text); providers
differ in what they take. Anything a provider can't take natively is either
folded into the prompt (when that still has an effect) or dropped — and either
way it is listed in ``notes`` so the operator can see what reached the provider.
Nothing provider-specific is ever sent to a provider that would reject it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Sizes each provider path in navig_generate.tools.image_generation accepts.
_SIZES: dict[str, list[str]] = {
    "openai": ["1024x1024", "1792x1024", "1024x1792"],  # DALL·E 3
    "openai_dalle3": ["1024x1024", "1792x1024", "1024x1792"],
    "openai_gpt_image": ["1024x1024", "1536x1024", "1024x1536"],
    # These take a size and map it themselves (nearest supported / aspect+resolution).
    "recraft": ["1024x1024", "1536x1024", "1024x1536", "1792x1024", "1024x1792"],
    "elevenlabs": ["1024x1024", "1536x1024", "1024x1536", "1792x1024", "1024x1792"],
    "stability": ["1024x1024", "1536x1024", "1024x1536"],
    "local": ["1024x1024", "1536x1024", "1024x1536", "1792x1024", "1024x1792"],
}
# Providers with no size parameter: the aspect is stated in the prompt instead.
_ASPECT_IN_PROMPT = {"gemini_flash", "gemini_pro"}
_QUALITY: dict[str, dict[str, str]] = {
    "openai": {"low": "standard", "medium": "standard", "standard": "standard", "high": "hd", "hd": "hd"},
    "openai_dalle3": {"low": "standard", "medium": "standard", "standard": "standard", "high": "hd", "hd": "hd"},
    "openai_gpt_image": {"low": "low", "medium": "medium", "standard": "medium", "high": "high", "hd": "high"},
}
_STYLE = {"openai", "openai_dalle3"}  # vivid / natural


@dataclass
class ImageRequest:
    prompt: str
    size: str | None = None
    quality: str | None = None
    style: str | None = None
    seed: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [])}


def _ratio(aspect: str) -> float:
    w, h = (float(x) for x in aspect.split(":"))
    return w / h


def _nearest(sizes: list[str], aspect: str) -> str:
    want = _ratio(aspect)
    return min(sizes, key=lambda s: abs(int(s.split("x")[0]) / int(s.split("x")[1]) - want))


def translate_image(provider: str, prompt: str, params: dict[str, Any], negative: str = "") -> ImageRequest:
    """Map preset ``params`` onto ``provider``'s real parameters."""
    prov = (provider or "").strip().lower()
    req = ImageRequest(prompt=prompt)
    aspect = params.get("aspect")
    if aspect:
        if prov in _SIZES:
            req.size = _nearest(_SIZES[prov], aspect)
            if req.size and abs(_ratio(aspect) - _ratio(req.size.replace("x", ":"))) > 0.05:
                req.notes.append(f"aspect {aspect} → nearest supported size {req.size}")
        elif prov in _ASPECT_IN_PROMPT or not prov:
            req.prompt += f"\n\nAspect ratio {aspect}."
            req.notes.append(f"aspect {aspect} stated in the prompt ({prov or 'default provider'} takes no size)")
        else:
            req.notes.append(f"aspect {aspect} not sent: unknown provider {prov!r}")
    quality = params.get("quality")
    if quality:
        mapped = _QUALITY.get(prov, {}).get(quality)
        if mapped:
            req.quality = mapped
        else:
            req.notes.append(f"quality {quality!r} not supported by {prov or 'the default provider'}; dropped")
    style = params.get("style")
    if style:
        if prov in _STYLE:
            req.style = style
        else:
            req.notes.append(f"style {style!r} is an OpenAI DALL·E setting; not sent to {prov or 'the default provider'}")
    if params.get("seed") is not None:
        if prov in ("stability", "local", "elevenlabs"):
            req.seed = int(params["seed"])
        else:
            req.notes.append(f"seed not supported by {prov or 'the default provider'}; dropped")
    if negative:
        # No NAVIG image path passes a native negative prompt today, so fold it in.
        req.prompt += f"\n\nAvoid: {negative}."
        req.notes.append("negative constraints folded into the prompt (no native negative prompt on this path)")
    return req
