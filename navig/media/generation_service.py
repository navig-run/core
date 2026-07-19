"""
Generation service — the NAVIG Media workflow engine.

Turns the stateless generators (:mod:`navig.tools.image_generation` etc.) into a
stateful, project-aware, versioned loop, and adds the derivative + processing
operations a real asset pipeline needs:

    generate(brief)      enrich w/ project context → run provider → stage every
                         variant into the refs library + a store row ("generated")
    reroll(id)           "next": same brief + group, a fresh variant
    edit(id, instr)      instruction edit (OpenAI gpt-image) — "make it more normal"
    remove_background(id) transparent-PNG cutout (Recraft)
    redesign(id, prompt) reference-based img2img redesign (Recraft)
    process(id, ops)     LOCAL pixel pipeline — crop/palette/chroma-key/downscale
    ingest(paths)        pull EXTERNALLY-generated files (Midjourney, …) into refs
    contact_sheet(...)   montage for review;  extract_palette(...) palette DNA
    keep/reject(id)      promote into refs/<category>  |  retain in .rejected
    history()            the gallery

Every derivative is a NEW variant in the SAME group (lineage in `context`), so
edits/redesigns/processed sprites sit beside their source in the strip and are
never destructive. Provider ops are async; local ops (process/ingest/…) are sync.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from navig.media import context_builder, refs_library
from navig.store.generated_media import get_generated_media

logger = logging.getLogger(__name__)

_EXT = {"image": ".png", "video": ".mp4", "audio": ".mp3", "text": ".md"}
VALID_MODALITIES = ("image", "video", "audio", "text")


def _new_id() -> str:
    return uuid.uuid4().hex


# ── Persistence helper ───────────────────────────────────────────────────────


def _persist_variant(
    root: Path,
    *,
    group_id: str,
    modality: str,
    src: Path,
    ext: str,
    prompt: str,
    provider: str,
    model: str | None,
    seed: int | None,
    params: dict[str, Any],
    context: dict[str, Any],
    license: str | None = None,
) -> dict[str, Any]:
    """Stage a produced file into refs + write a store row. Returns the row."""
    store = get_generated_media()
    media_id = _new_id()
    meta = {"group": group_id, "prompt": prompt, "provider": provider or "",
            "model": model, "seed": seed, "context": context, "license": license}
    staged = refs_library.stage(root, media_id=media_id, modality=modality, ext=ext,
                                src=Path(src), meta=meta)
    store.create(
        id=media_id, group_id=group_id, modality=modality, prompt=prompt,
        provider=provider or "", model=model, seed=seed, params=params,
        context=context, path=str(staged), license=license,
        # Store the SPACE dir (not the refs root) so resolve_refs_root() can
        # reconstruct the root without double-nesting .navig/refs.
        space=str(Path(root).parent.parent),
    )
    return store.get(media_id)


# ── Provider runner (generate) ───────────────────────────────────────────────


async def _run_provider(
    modality: str,
    enriched_prompt: str,
    *,
    provider: str | None,
    size: str | None,
    kind: str,
    duration_s: float | None,
    n: int,
    seed: int | None,
    out_dir: Path,
) -> list[Any]:
    """Run the right generator into ``out_dir``; return a list of result objects.

    Dispatch order: a facet-registered backend (navig-image / -video / -audio /
    -text via ``navig.media.types.register_generator``) wins; otherwise fall back
    to the built-in per-modality backends below. This is the Phase-3 seam — the
    engine stays in core; facets plug their generators in.
    """
    from navig.media.types import get_generator  # noqa: PLC0415

    _backend = get_generator(modality)
    if _backend is not None:
        return await _backend(
            enriched_prompt, provider=provider, size=size, kind=kind,
            duration_s=duration_s, n=n, seed=seed, out_dir=out_dir,
        )

    if modality == "image":
        from navig.tools.image_generation import (
            ImageGenerationConfig,
            ImageGenerator,
            ImageProvider,
            ImageSize,
        )

        cfg = ImageGenerationConfig.from_env()
        cfg.output_dir = str(out_dir)
        gen = ImageGenerator(cfg)
        try:
            sz = ImageSize(size) if size in {s.value for s in ImageSize} else None
            return await gen.generate(
                enriched_prompt, size=sz, n=n,
                provider=ImageProvider(provider) if provider else None, seed=seed,
            )
        finally:
            await gen.close()

    if modality == "video":
        from navig.tools.video_generation import (
            VideoGenerationConfig,
            VideoGenerator,
            VideoProvider,
        )

        cfg = VideoGenerationConfig.from_env()
        cfg.output_dir = str(out_dir)
        gen = VideoGenerator(cfg)
        try:
            vid = await gen.generate(
                enriched_prompt,
                provider=VideoProvider(provider) if provider else None, seed=seed,
            )
            return [vid]
        finally:
            await gen.close()

    if modality == "audio":
        from navig.tools.audio_generation import AudioGenerationConfig, AudioGenerator

        cfg = AudioGenerationConfig.from_env()
        cfg.output_dir = str(out_dir)
        gen = AudioGenerator(cfg)
        try:
            aud = await gen.generate(enriched_prompt, kind=kind, duration_s=duration_s)
            return [aud]
        finally:
            await gen.close()

    raise ValueError(f"unknown modality: {modality}")


async def _generate_into_group(
    *, group_id, modality, enriched_prompt, context, provider, model, size, kind,
    duration_s, n, seed, root, license=None,
) -> list[dict[str, Any]]:
    ext = _EXT.get(modality, ".bin")
    variants: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="navig-generate-") as tmp:
        objs = await _run_provider(
            modality, enriched_prompt, provider=provider, size=size, kind=kind,
            duration_s=duration_s, n=n, seed=seed, out_dir=Path(tmp),
        )
        for obj in objs:
            local = getattr(obj, "local_path", None)
            if not local or not Path(local).exists():
                logger.warning("media variant produced no local file (%s); skipping", provider)
                continue
            variants.append(_persist_variant(
                root, group_id=group_id, modality=modality, src=Path(local), ext=ext,
                prompt=enriched_prompt, provider=provider or "",
                model=getattr(obj, "model", None) or model,
                seed=getattr(obj, "seed", None) if seed is None else seed,
                params={"size": size, "kind": kind, "duration_s": duration_s, "n": n},
                context=context, license=license,
            ))
    return variants


async def generate(
    *, modality, prompt, provider=None, model=None, placement=None, palette=None,
    reference_ref=None, size=None, kind="music", duration_s=None, n=1, seed=None,
    license=None, space_dir=None,
) -> dict[str, Any]:
    """Generate variant(s) for a brief; stage + persist each. Returns the group."""
    if modality not in VALID_MODALITIES:
        raise ValueError(f"modality must be one of {VALID_MODALITIES}")
    root = refs_library.resolve_refs_root(space_dir)
    refs_library.ensure_layout(root)
    built = context_builder.build_context(
        prompt, placement=placement, palette=palette,
        reference_ref=reference_ref, space_dir=Path(root).parent.parent,
    )
    group_id = _new_id()
    variants = await _generate_into_group(
        group_id=group_id, modality=modality, enriched_prompt=built["enriched_prompt"],
        context=built["context"], provider=provider, model=model, size=size, kind=kind,
        duration_s=duration_s, n=n, seed=seed, root=root, license=license,
    )
    return {"group_id": group_id, "enriched_prompt": built["enriched_prompt"],
            "context": built["context"], "variants": variants}


async def reroll(source_id: str, *, seed: int | None = None) -> dict[str, Any]:
    """"Next": generate a fresh variant using the source's brief + group."""
    store = get_generated_media()
    row = store.get(source_id)
    if row is None:
        raise ValueError(f"no such variant: {source_id}")
    params = row.get("params") or {}
    root = refs_library.resolve_refs_root(row.get("space"))
    variants = await _generate_into_group(
        group_id=row["group_id"], modality=row["modality"], enriched_prompt=row["prompt"],
        context=row.get("context") or {}, provider=row.get("provider") or None,
        model=row.get("model"), size=params.get("size"), kind=params.get("kind") or "music",
        duration_s=params.get("duration_s"), n=1, seed=seed, root=root,
        license=row.get("license"),
    )
    return {"group_id": row["group_id"], "variants": variants}


# ── Derivative image ops (edit / remove-bg / redesign) ───────────────────────


async def _image_op(source_id: str, op: str, runner) -> dict[str, Any]:
    """Shared plumbing: load source → run an ImageGenerator coroutine → persist.

    `runner(gen, src_path)` returns a GeneratedImage.
    """
    store = get_generated_media()
    row = store.get(source_id)
    if row is None:
        raise ValueError(f"no such variant: {source_id}")
    if row["modality"] != "image":
        raise ValueError(f"{op} only applies to images")
    if not row.get("path") or not Path(row["path"]).exists():
        raise ValueError("source image file is missing")

    root = refs_library.resolve_refs_root(row.get("space"))
    from navig.tools.image_generation import ImageGenerationConfig, ImageGenerator

    gen = ImageGenerator(ImageGenerationConfig.from_env())
    try:
        img = await runner(gen, row["path"])
        with tempfile.TemporaryDirectory(prefix="navig-op-") as tmp:
            out = Path(tmp) / "out.png"
            await img.save_to_file(str(out))
            ctx = dict(row.get("context") or {})
            ctx.update({"derived_from": source_id, "op": op})
            return _persist_variant(
                root, group_id=row["group_id"], modality="image", src=out, ext=".png",
                prompt=img.prompt, provider=img.provider.value, model=img.model,
                seed=None, params={"op": op}, context=ctx, license=row.get("license"),
            )
    finally:
        await gen.close()


async def edit(source_id: str, instruction: str, *, mask_path: str | None = None) -> dict[str, Any]:
    """Instruction-edit a variant (e.g. 'make this more normal, no cape')."""
    return await _image_op(source_id, "edit",
                           lambda g, p: g.edit(p, instruction, mask_path=mask_path))


async def remove_background(source_id: str) -> dict[str, Any]:
    """Cut out the background of a variant → transparent PNG (Recraft)."""
    return await _image_op(source_id, "remove_background", lambda g, p: g.remove_background(p))


async def redesign(source_id: str, prompt: str, *, strength: float = 0.4) -> dict[str, Any]:
    """Reference-based img2img redesign of a variant (Recraft)."""
    return await _image_op(source_id, "redesign", lambda g, p: g.img2img(p, prompt, strength=strength))


# ── Local pixel processing (crop/palette/chroma-key/downscale) ───────────────


def process(source_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the LOCAL pixel pipeline on a variant → a new processed variant.

    `ops` e.g. [{"op":"chroma_key","color":"#FF00FF"}, {"op":"quantize","colors":16},
                {"op":"downscale","size":64}]. Great for turning AI art into a
    game-ready sprite. Requires Pillow (navig.media.processing).
    """
    from navig.media import processing

    if not processing.pillow_available():
        raise ImportError("Pillow required for processing: pip install Pillow")
    store = get_generated_media()
    row = store.get(source_id)
    if row is None:
        raise ValueError(f"no such variant: {source_id}")
    if row["modality"] != "image":
        raise ValueError("process only applies to images")
    if not row.get("path") or not Path(row["path"]).exists():
        raise ValueError("source image file is missing")

    root = refs_library.resolve_refs_root(row.get("space"))
    with tempfile.TemporaryDirectory(prefix="navig-proc-") as tmp:
        out = Path(tmp) / "out.png"
        processing.pipeline(row["path"], out, ops)
        ctx = dict(row.get("context") or {})
        ctx.update({"derived_from": source_id, "op": "process", "ops": ops})
        return _persist_variant(
            root, group_id=row["group_id"], modality="image", src=out, ext=".png",
            prompt=row["prompt"], provider="local:process", model="pillow-pipeline",
            seed=row.get("seed"), params={"ops": ops}, context=ctx, license=row.get("license"),
        )


# ── External-generator ingest ────────────────────────────────────────────────


def ingest(
    paths: list[str] | str,
    *,
    provider: str = "external",
    model: str | None = None,
    prompt: str = "",
    seed: int | None = None,
    license: str | None = None,
    space_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Pull externally-generated files (Midjourney/etc.) into the refs library.

    Accepts a directory (ingest every image in it) or a list of file paths. For
    each file, an adjacent ``<name>.json`` sidecar (if present) overrides
    prompt/provider/model/seed/license. Returns ``{group_id, variants:[…]}``.
    Nothing is imported into the game here — this is the reviewable staging step.
    """
    import json as _json

    root = refs_library.resolve_refs_root(space_dir)
    refs_library.ensure_layout(root)
    files = _collect_files(paths)
    group_id = _new_id()
    variants: list[dict[str, Any]] = []
    for f in files:
        meta = {"provider": provider, "model": model, "prompt": prompt,
                "seed": seed, "license": license}
        sidecar = f.with_suffix(".json")
        if sidecar.exists():
            try:
                meta.update({k: v for k, v in _json.loads(sidecar.read_text(encoding="utf-8")).items()
                             if k in meta})
            except Exception:  # noqa: BLE001
                pass
        variants.append(_persist_variant(
            root, group_id=group_id, modality="image", src=f, ext=f.suffix or ".png",
            prompt=meta["prompt"] or "", provider=meta["provider"] or "external",
            model=meta["model"], seed=meta["seed"],
            params={"ingested_from": str(f)},
            context={"ingested": True, "source_file": str(f)}, license=meta["license"],
        ))
    return {"group_id": group_id, "variants": variants, "count": len(variants)}


def _collect_files(paths: list[str] | str) -> list[Path]:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    out: list[Path] = []
    items = [paths] if isinstance(paths, str) else list(paths)
    for item in items:
        p = Path(item)
        if p.is_dir():
            out.extend(sorted(f for f in p.iterdir() if f.suffix.lower() in exts))
        elif p.is_file():
            out.append(p)
    return out


# ── Review aids (contact sheet, palette DNA) ─────────────────────────────────


def contact_sheet(
    *, modality: str | None = "image", status: str | None = None,
    group_id: str | None = None, cols: int = 6, space_dir: str | Path | None = None,
) -> str:
    """Build a review montage of variants (a group, or a status filter). Returns path."""
    from navig.media import processing

    if not processing.pillow_available():
        raise ImportError("Pillow required for contact sheets: pip install Pillow")
    store = get_generated_media()
    rows = store.list_group(group_id) if group_id else store.list(
        modality=modality, status=status,
        space=str(space_dir) if space_dir else None, limit=200,
    )
    paths = [r["path"] for r in rows if r.get("path") and Path(r["path"]).exists()]
    if not paths:
        raise ValueError("no variant files to montage")
    root = refs_library.resolve_refs_root(space_dir if space_dir else rows[0].get("space"))
    refs_library.ensure_layout(root)
    dst = root / f"contact-{_new_id()[:8]}.png"
    return processing.contact_sheet(paths, dst, cols=cols)


def extract_palette(source: str, *, colors: int = 16) -> list[str]:
    """Extract a hex palette from a directory of reference frames (or kept refs)."""
    from navig.media import processing

    if not processing.pillow_available():
        raise ImportError("Pillow required for palette extraction: pip install Pillow")
    paths = [str(p) for p in _collect_files(source)]
    if not paths:
        raise ValueError("no images found to sample")
    return processing.extract_palette(paths, colors=colors)


# ── Keep / reject / history ──────────────────────────────────────────────────


def keep(media_id: str) -> dict[str, Any] | None:
    store = get_generated_media()
    row = store.get(media_id)
    if row is None:
        return None
    root = refs_library.resolve_refs_root(row.get("space"))
    dest = refs_library.promote(root, media_id, row["modality"])
    store.set_status(media_id, "kept", path=str(dest))
    return store.get(media_id)


def reject(media_id: str) -> dict[str, Any] | None:
    store = get_generated_media()
    row = store.get(media_id)
    if row is None:
        return None
    root = refs_library.resolve_refs_root(row.get("space"))
    dest = refs_library.reject(root, media_id)
    store.set_status(media_id, "rejected", path=str(dest))
    return store.get(media_id)


def set_license(media_id: str, license: str) -> dict[str, Any] | None:
    """Record/patch the license for a variant (provenance)."""
    store = get_generated_media()
    if not store.update(media_id, license=license):
        return None
    return store.get(media_id)


def active_space_key() -> str | None:
    """The active space's key as stored on rows (for space-scoped queries)."""
    try:
        from navig.spaces.active import get_active_working_dir

        return str(get_active_working_dir())
    except Exception:  # noqa: BLE001
        return None


def cwd_space_dir() -> Path:
    """The space a CLI invocation should act on: the project you're STANDING IN.

    Walks up from the caller's ORIGINAL launch directory (``NAVIG_INVOCATION_CWD``,
    stamped by the CLI *before* it chdirs to the pinned space) to the nearest
    ``.navig/`` — so ``navig media`` in a project folder scopes to that project,
    git-style. Falls back to the globally-active/pinned space when the launch dir
    isn't inside any space, or for non-CLI callers (deck/agent) where the env var
    is unset. The daemon/agent otherwise keep using ``get_active_working_dir()``.
    """
    import os  # noqa: PLC0415

    launch = os.environ.get("NAVIG_INVOCATION_CWD")
    if launch:
        cur = Path(launch)
        while True:
            try:
                if (cur / ".navig").is_dir():
                    return cur
            except OSError:
                break
            if cur.parent == cur:
                break
            cur = cur.parent
    from navig.spaces.active import get_active_working_dir

    return get_active_working_dir()


def history(
    *, modality=None, status=None, space=None, all_spaces=False, limit=200
) -> list[dict[str, Any]]:
    """Variants for the ACTIVE space by default; pass all_spaces=True for the global view.

    `space` overrides the active-space key explicitly (a space root path).
    """
    space_filter = None if all_spaces else (space or active_space_key())
    return get_generated_media().list(
        modality=modality, status=status, space=space_filter, limit=limit
    )
