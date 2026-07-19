"""navig media — multimedia hub.

Two halves under one command:
  * **analyse** (this file): video / audio / doc → a `briefing.md` (frames + transcript + AI).
    Reuses the extract.py spine, navig.media.frames, core.ocr, and llm_generate.
  * **generate** (navig.media.generation_service): the context-aware image/media generation
    workflow — thin passthroughs (`ingest` / `keep` / `reject`) so both live under `navig media`.

Briefings land in the unified refs vault: ``<space>/.navig/refs/notes/media/<slug>/`` (alongside
the generated media-asset library at ``.navig/refs/{images,videos,audio}/``).
"""
from __future__ import annotations

import re
from pathlib import Path

import typer

from navig import console_helper as ch

media_app = typer.Typer(
    name="generate",
    help="AI media — analyse (video→briefing) + generate (image/video/audio).",
    no_args_is_help=True,
)


@media_app.callback()
def _generate_root(ctx: typer.Context) -> None:
    # `navig media` was renamed to `navig generate`; `media` stays as a deprecated
    # alias (both names mount this same app). Warn only when invoked as `media`.
    if (ctx.info_name or "") == "media":
        ch.warning("`navig media` is deprecated — use `navig generate` (same command).")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")
    return s or "media"


def _default_out(source: Path) -> Path:
    """`<active space>/.navig/refs/notes/media/<slug>/`, falling back to `./<slug>.briefing/`."""
    try:
        from navig.media.refs_library import resolve_refs_root  # noqa: PLC0415
        return resolve_refs_root() / "notes" / "media" / _slug(source.name)
    except Exception:  # noqa: BLE001 — no active space / import issue → local dir
        return Path.cwd() / f"{_slug(source.name)}.briefing"


# ── clickable output helpers ────────────────────────────────────────────────

def _uri(path: str | None) -> str | None:
    """A file:// URI for terminal hyperlinks (clickable in VS Code / modern terminals)."""
    try:
        return Path(path).resolve().as_uri() if path else None
    except Exception:  # noqa: BLE001
        return None


def _print_variants(variants: list[dict]) -> None:
    """Print each variant as a clickable id + path line."""
    from rich.text import Text  # noqa: PLC0415
    for v in variants:
        uri = _uri(v.get("path"))
        line = Text("  ")
        line.append(v["id"][:8], style=(f"link {uri}" if uri else "bold cyan"))
        line.append(f"  {v.get('provider', '')}  ", style="dim")
        line.append(v.get("path") or "", style=(f"link {uri}" if uri else "dim"))
        ch.console.print(line)


# ── analyse ───────────────────────────────────────────────────────────────────

@media_app.command("analyze")
@media_app.command("analyse")  # both spellings
def media_analyze(
    source: str = typer.Argument(..., help="Video / audio / document to distil into a briefing."),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output dir (default: the refs vault)."),
    profile: str = typer.Option("generic", "--as", help="Briefing profile: howto | creative | generic."),
    max_frames: int = typer.Option(40, "--max-frames", help="Cap on extracted video frames."),
) -> None:
    """Extract a **briefing.md** from a video/audio/doc: frames + transcript + links + AI summary."""
    src = Path(source).expanduser()
    if not src.is_file():
        ch.error(f"Not a file: {src}")
        raise typer.Exit(1)
    if profile not in ("howto", "creative", "generic"):
        ch.warning(f"Unknown profile '{profile}' — using 'generic'.")
        profile = "generic"

    out_dir = out or _default_out(src)
    from navig.media.briefing import build_briefing  # noqa: PLC0415

    with ch.create_spinner(f"Distilling {src.name} …"):
        res = build_briefing(src, out_dir, profile=profile, max_frames=max_frames)

    ch.success(f"Briefing written: {res['briefing']}")
    ch.info(f"  kind={res['kind']} · frames={res['frames']} · links={res['links']} · "
            f"transcript={'yes' if res['has_transcript'] else 'no'} · "
            f"ai={'yes' if res['llm'] else 'no (template)'}")
    for n in res.get("notes", []):
        ch.warning(f"  {n}")


# ── frames / probe (mechanical, testable) ───────────────────────────────────────

@media_app.command("frames")
def media_frames(
    video: str = typer.Argument(..., help="Video file."),
    out: Path = typer.Option(Path("frames"), "--out", "-o", help="Output dir for frames."),
    mode: str = typer.Option("scene", "--mode", help="scene (scene-change) | interval."),
    max_frames: int = typer.Option(60, "--max-frames"),
) -> None:
    """Extract keyframes from a video (ffmpeg scene-change or interval sampling)."""
    from navig.media.frames import extract_frames, ffmpeg_available  # noqa: PLC0415
    if not ffmpeg_available():
        ch.error("ffmpeg not installed.", details="Install ffmpeg to extract frames.")
        raise typer.Exit(1)
    frames = extract_frames(Path(video).expanduser(), out, mode=mode, max_frames=max_frames)
    ch.success(f"{len(frames)} frame(s) → {out}")


@media_app.command("probe")
def media_probe(video: str = typer.Argument(..., help="Video file.")) -> None:
    """Print video metadata (duration / resolution / fps) via ffprobe."""
    from navig.media.frames import probe  # noqa: PLC0415
    meta = probe(Path(video).expanduser())
    if not meta:
        ch.error("ffprobe unavailable or unreadable file.")
        raise typer.Exit(1)
    for k, v in meta.items():
        ch.info(f"  {k}: {v}")


# ── dedupe-audio (acoustic near-dup via chromaprint) ────────────────────────────

@media_app.command("dedupe-audio")
def media_dedupe_audio(
    directory: str = typer.Argument(..., help="Folder of audio files to scan."),
    threshold: float = typer.Option(0.90, "--threshold", "-t",
                                     help="Bit-similarity to treat as duplicate (0-1)."),
    apply: bool = typer.Option(False, "--apply", help="Quarantine extras (default: dry-run)."),
    quarantine_dir: Path | None = typer.Option(None, "--quarantine",
                                               help="Where to move dupes (default: <dir>/_dupes)."),
    workers: int = typer.Option(6, "--workers", help="Parallel fpcalc workers."),
) -> None:
    """Find & collapse re-shared/re-encoded duplicate tracks via acoustic fingerprints.

    Catches same-song copies that byte-hashing misses (re-encodes, m4a↔mp3).
    Dry-run unless ``--apply``; extras are MOVED to a quarantine folder, never deleted.
    """
    from navig.media import audio_dedupe as ad  # noqa: PLC0415

    root = Path(directory).expanduser()
    if not root.is_dir():
        ch.error(f"Not a directory: {root}")
        raise typer.Exit(1)
    if not ad.fpcalc_available():
        ch.error("fpcalc (Chromaprint) not found.",
                 details="Install it (e.g. `scoop install chromaprint`) or set NAVIG_FPCALC.")
        raise typer.Exit(1)

    with ch.create_spinner(f"Fingerprinting audio in {root.name} …"):
        fps = ad.fingerprint_dir(root, workers=workers)
    if not fps:
        ch.warning("No fingerprintable audio found.")
        raise typer.Exit(0)

    clusters = ad.cluster(fps, threshold=threshold)
    extras = sum(len(c) - 1 for c in clusters)
    reclaim = sum(
        (root / n).stat().st_size
        for c in clusters
        for n in sorted(c, key=lambda x: (root / x).stat().st_size, reverse=True)[1:]
        if (root / n).exists()
    )
    ch.info(f"  fingerprinted {len(fps)} · duplicate groups {len(clusters)} · "
            f"removable {extras} · reclaimable {reclaim / (1024**2):.0f} MB")

    if not apply:
        for c in sorted(clusters, key=len, reverse=True)[:8]:
            ch.info(f"    {len(c)}× — e.g. {c[0]}")
        ch.info("  (dry-run — re-run with --apply to quarantine extras)")
        return

    qdir = quarantine_dir or (root / "_dupes")
    res = ad.quarantine(root, clusters, qdir)
    ch.success(f"Quarantined {res['quarantined']} dupes "
               f"({res['reclaimed_bytes'] / (1024**2):.0f} MB) → {res['quarantine_dir']}")


# ── dedupe-files / images / video (share this apply helper) ─────────────────────

def _dedupe_apply(root: Path, to_move: list[str], apply: bool,
                  quarantine_dir: Path | None, label: str) -> None:
    reclaim = sum((root / n).stat().st_size for n in to_move if (root / n).exists())
    ch.info(f"  removable {len(to_move)} · reclaimable {reclaim / (1024**2):.0f} MB")
    if not apply:
        ch.info("  (dry-run — re-run with --apply to quarantine extras)")
        return
    from navig.media.image_dedupe import quarantine  # noqa: PLC0415
    res = quarantine(root, to_move, quarantine_dir or (root / "_dupes"))
    ch.success(f"Quarantined {res['quarantined']} {label} "
               f"({res['reclaimed_bytes'] / (1024**2):.0f} MB) → {res['quarantine_dir']}")


@media_app.command("dedupe-files")
def media_dedupe_files(
    directory: str = typer.Argument(..., help="Folder to scan."),
    apply: bool = typer.Option(False, "--apply", help="Quarantine extras (default: dry-run)."),
    quarantine_dir: Path | None = typer.Option(None, "--quarantine"),
    workers: int = typer.Option(8, "--workers"),
) -> None:
    """Exact byte-identical duplicates (content SHA-256). Keeps one, quarantines the rest."""
    from navig.media import file_dedupe as fd  # noqa: PLC0415
    from navig.media import image_dedupe as im
    root = Path(directory).expanduser()
    if not root.is_dir():
        ch.error(f"Not a directory: {root}")
        raise typer.Exit(1)
    with ch.create_spinner(f"Hashing {root.name} …"):
        hashes = fd.hash_dir(root, workers=workers)
    clusters = fd.cluster(hashes)
    ch.info(f"  files {len(hashes)} · duplicate groups {len(clusters)}")
    _dedupe_apply(root, im.extras_to_drop(root, clusters), apply, quarantine_dir, "files")


@media_app.command("dedupe-images")
def media_dedupe_images(
    directory: str = typer.Argument(..., help="Folder of images to scan."),
    threshold: int = typer.Option(0, "--threshold", "-t",
                                  help="Perceptual Hamming distance (0=identical look; higher=looser)."),
    drop_thumbs: bool = typer.Option(True, "--thumbs/--no-thumbs",
                                     help="Also drop `X_thumb.ext` when full X exists."),
    apply: bool = typer.Option(False, "--apply"),
    quarantine_dir: Path | None = typer.Option(None, "--quarantine"),
    workers: int = typer.Option(8, "--workers"),
) -> None:
    """Perceptual image near-dups (256-bit dHash) + redundant thumbnails. Keeps highest-res."""
    from navig.media import image_dedupe as im  # noqa: PLC0415
    root = Path(directory).expanduser()
    if not root.is_dir():
        ch.error(f"Not a directory: {root}")
        raise typer.Exit(1)
    thumbs = im.redundant_thumbs(root) if drop_thumbs else []
    with ch.create_spinner(f"Hashing images in {root.name} …"):
        hashes = im.hash_dir(root, workers=workers, skip=set(thumbs))
    clusters = im.cluster(hashes, threshold=threshold)
    extras = im.extras_to_drop(root, clusters)
    ch.info(f"  images {len(hashes)} · thumbnails {len(thumbs)} · "
            f"perceptual groups {len(clusters)}")
    _dedupe_apply(root, thumbs + extras, apply, quarantine_dir, "images")


@media_app.command("dedupe-video")
def media_dedupe_video(
    directory: str = typer.Argument(..., help="Folder of videos to scan."),
    threshold: int = typer.Option(24, "--threshold", "-t",
                                  help="Avg per-frame Hamming to treat as duplicate (lower=stricter)."),
    apply: bool = typer.Option(False, "--apply"),
    quarantine_dir: Path | None = typer.Option(None, "--quarantine"),
    workers: int = typer.Option(6, "--workers"),
) -> None:
    """Perceptual video near-dups via keyframe signatures (catches re-encodes/re-uploads)."""
    from navig.media import image_dedupe as im
    from navig.media import video_dedupe as vd  # noqa: PLC0415
    root = Path(directory).expanduser()
    if not root.is_dir():
        ch.error(f"Not a directory: {root}")
        raise typer.Exit(1)
    with ch.create_spinner(f"Fingerprinting videos in {root.name} … (frame extraction)"):
        sigs = vd.signature_dir(root, workers=workers)
    clusters = vd.cluster(sigs, threshold=threshold)
    ch.info(f"  videos {len(sigs)} · duplicate groups {len(clusters)}")
    _dedupe_apply(root, im.extras_to_drop(root, clusters), apply, quarantine_dir, "videos")


@media_app.command("browse")
def media_browse(
    directory: str = typer.Argument(..., help="Folder of media to browse."),
    port: int = typer.Option(8770, "--port", "-p"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open the browser."),
) -> None:
    """Launch a local web gallery for any media folder (grid + preview, streams video)."""
    import webbrowser  # noqa: PLC0415

    from navig.media.browse import serve  # noqa: PLC0415
    root = Path(directory).expanduser()
    if not root.is_dir():
        ch.error(f"Not a directory: {root}")
        raise typer.Exit(1)
    url = f"http://localhost:{port}"
    ch.success(f"Media gallery → {url}   (Ctrl+C to stop)")
    if not no_open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        serve(root, port)
    except KeyboardInterrupt:
        pass


# NOTE: the universal explorer moved to its own top-level command — `navig explore`
# (plugin: navig-explore). `navig media` is deprecated; don't add surface here.


# ── generate + iterate + process (delegate to navig.media.generation_service) ────

@media_app.command("gen")
def media_gen(
    prompt: str = typer.Argument(..., help="What to generate"),
    modality: str = typer.Option("image", "--modality", help="image | video | audio"),
    provider: str | None = typer.Option(None, "--provider", help="Provider id (omit = default)"),
    placement: str | None = typer.Option(None, "--placement", help="Where it goes, e.g. 'left box of login card'"),
    ref: str | None = typer.Option(None, "--ref", help="Reference screenshot path (recorded as context)"),
    size: str | None = typer.Option(None, "--size", help="Image size, e.g. 1024x1024"),
    kind: str = typer.Option("music", "--kind", help="Audio kind: music | sfx | tts"),
    n: int = typer.Option(1, "--n", help="How many variants (image)"),
) -> None:
    """Generate variant(s) into the active space's refs library."""
    import asyncio  # noqa: PLC0415

    from navig.media import generation_service as gen  # noqa: PLC0415
    label = f"Generating {n} {modality}{'s' if n > 1 else ''} via {provider or 'default provider'}…"
    try:
        with ch.create_spinner(label):
            res = asyncio.run(gen.generate(
                modality=modality, prompt=prompt, provider=provider, placement=placement,
                reference_ref=ref, size=size, kind=kind, n=n,
                space_dir=gen.cwd_space_dir(),
            ))
    except Exception as exc:  # noqa: BLE001
        ch.error(f"Generation failed: {exc}")
        raise typer.Exit(1) from exc
    variants = res.get("variants") or []
    if not variants:
        ch.warning("No variants produced (check provider key + quota).")
        raise typer.Exit(1)
    ch.success(f"Generated {len(variants)} variant(s) · group {res['group_id'][:8]}")
    _print_variants(variants)
    ch.dim("Keep one:  navig media keep <id>   ·   sprite it:  navig media process <id> --ops \"...\"")


@media_app.command("list")
def media_list(
    modality: str | None = typer.Option(None, "--modality", help="image | video | audio"),
    status: str | None = typer.Option(None, "--status", help="generated | kept | rejected"),
    all_spaces: bool = typer.Option(False, "--all", "-a",
                                    help="Every space (default: only THIS space's generations)"),
) -> None:
    """List variants in THIS space, newest first (clickable). --all spans every space."""
    from rich.table import Table  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    from navig.media import generation_service as gen  # noqa: PLC0415

    space_dir = gen.cwd_space_dir()
    rows = gen.history(modality=modality, status=status,
                       space=str(space_dir), all_spaces=all_spaces, limit=100)
    scope = "all spaces" if all_spaces else (Path(space_dir).name or "current dir")

    if not rows:
        ch.info(f"No generations in {scope} yet.")
        if not all_spaces:
            ch.dim("See every space with:  navig media list --all")
        return

    t = Table(show_header=True, header_style="bold", box=None, pad_edge=False, expand=False)
    t.add_column("#", no_wrap=True)
    t.add_column("status", no_wrap=True)
    t.add_column("type", no_wrap=True)
    t.add_column("provider", no_wrap=True)
    if all_spaces:
        t.add_column("space", no_wrap=True, style="dim")
    t.add_column("prompt", overflow="ellipsis", max_width=48)

    st_style = {"kept": "green", "generated": "yellow", "rejected": "dim"}
    for v in rows:
        uri = _uri(v.get("path"))
        idc = Text(v["id"][:8], style=(f"link {uri}" if uri else "bold cyan"))
        cells = [idc, Text(v["status"], style=st_style.get(v["status"], "")),
                 v["modality"], v.get("provider", "")]
        if all_spaces:
            cells.append(Path(v["space"]).name if v.get("space") else "—")
        cells.append(Text(v.get("prompt") or ""))
        t.add_row(*cells)

    ch.dim(f"media · {scope} · {len(rows)} item(s)")
    ch.print_table(t)
    ch.dim("click an id to open · keep/reject <id>" + ("" if all_spaces else " · --all spans every space"))


@media_app.command("ingest")
def media_ingest(
    paths: list[str] = typer.Argument(..., help="Dir of externally-generated files, or file(s)."),
    provider: str = typer.Option("external", "--provider", help="Where they came from"),
    prompt: str = typer.Option("", "--prompt", help="Shared prompt (or use per-file .json sidecars)"),
    license: str = typer.Option("", "--license", help="License to record"),
) -> None:
    """Pull externally-generated files into the refs library for review (Midjourney, etc.)."""
    from navig.media import generation_service as gen  # noqa: PLC0415
    src: list[str] | str = paths[0] if len(paths) == 1 else list(paths)
    with ch.create_spinner("Ingesting…"):
        res = gen.ingest(src, provider=provider, prompt=prompt, license=license or None,
                         space_dir=gen.cwd_space_dir())
    ch.success(f"Ingested {res['count']} file(s) · group {res['group_id'][:8]}")
    _print_variants(res["variants"])


@media_app.command("keep")
def media_keep(media_id: str = typer.Argument(..., help="Variant id to keep (promote).")) -> None:
    """Promote a staged variant to the kept library."""
    from navig.media import generation_service as gen  # noqa: PLC0415
    row = gen.keep(media_id)
    if row is None:
        ch.error("No such variant.")
        raise typer.Exit(1)
    ch.success("Kept → refs library")
    _print_variants([row])


@media_app.command("reject")
def media_reject(media_id: str = typer.Argument(..., help="Variant id to reject (retained).")) -> None:
    """Reject a staged variant (retained in .rejected, never deleted)."""
    from navig.media import generation_service as gen  # noqa: PLC0415
    row = gen.reject(media_id)
    if row is None:
        ch.error("No such variant.")
        raise typer.Exit(1)
    ch.info("Rejected (retained)")
    _print_variants([row])


@media_app.command("edit")
def media_edit(
    media_id: str = typer.Argument(..., help="Variant id to edit"),
    instruction: str = typer.Argument(..., help="e.g. 'make this more normal, no cape'"),
) -> None:
    """Instruction-edit an image variant (OpenAI gpt-image)."""
    import asyncio  # noqa: PLC0415

    from navig.media import generation_service as gen  # noqa: PLC0415
    with ch.create_spinner("Editing (gpt-image)…"):
        row = asyncio.run(gen.edit(media_id, instruction))
    ch.success("Edited")
    _print_variants([row])


@media_app.command("rembg")
def media_rembg(media_id: str = typer.Argument(..., help="Variant id")) -> None:
    """Remove the background → transparent PNG (Recraft)."""
    import asyncio  # noqa: PLC0415

    from navig.media import generation_service as gen  # noqa: PLC0415
    with ch.create_spinner("Removing background (Recraft)…"):
        row = asyncio.run(gen.remove_background(media_id))
    ch.success("Background removed → transparent PNG")
    _print_variants([row])


@media_app.command("redesign")
def media_redesign(
    media_id: str = typer.Argument(..., help="Variant id"),
    prompt: str = typer.Argument(..., help="How to redesign it"),
    strength: float = typer.Option(0.4, "--strength", help="0=subtle .. 1=loose"),
) -> None:
    """Reference-based img2img redesign (Recraft)."""
    import asyncio  # noqa: PLC0415

    from navig.media import generation_service as gen  # noqa: PLC0415
    with ch.create_spinner("Redesigning (Recraft img2img)…"):
        row = asyncio.run(gen.redesign(media_id, prompt, strength=strength))
    ch.success("Redesigned")
    _print_variants([row])


@media_app.command("process")
def media_process(
    media_id: str = typer.Argument(..., help="Variant id"),
    ops: str = typer.Option(..., "--ops", help="e.g. 'chroma_key:#FF00FF,quantize:16,downscale:64'"),
) -> None:
    """LOCAL pixel pipeline → a game-ready sprite variant (Pillow)."""
    from navig.media import generation_service as gen  # noqa: PLC0415
    with ch.create_spinner("Processing (pixel pipeline)…"):
        row = gen.process(media_id, _parse_ops(ops))
    ch.success("Processed → sprite")
    _print_variants([row])


@media_app.command("palette")
def media_palette(
    source: str = typer.Argument(..., help="Dir of reference frames"),
    colors: int = typer.Option(16, "--colors", help="Palette size"),
) -> None:
    """Extract a hex palette from reference frames."""
    from navig.media import generation_service as gen  # noqa: PLC0415
    cols = gen.extract_palette(source, colors=colors)
    ch.success(f"Palette ({len(cols)}): " + " ".join(cols))


@media_app.command("contact-sheet")
def media_contact_sheet(
    group: str = typer.Option("", "--group", help="Group id (default: all kept images)"),
    status: str = typer.Option("", "--status", help="Filter: generated | kept | rejected"),
) -> None:
    """Montage variants into a review contact sheet (Pillow)."""
    from navig.media import generation_service as gen  # noqa: PLC0415
    path = gen.contact_sheet(group_id=group or None, status=status or None,
                             space_dir=str(gen.cwd_space_dir()))
    ch.success(f"Contact sheet → {path}")


@media_app.command("license")
def media_license(
    media_id: str = typer.Argument(..., help="Variant id"),
    value: str = typer.Argument(..., help="License string, e.g. CC0-1.0"),
) -> None:
    """Record the license for a variant (provenance)."""
    from navig.media import generation_service as gen  # noqa: PLC0415
    row = gen.set_license(media_id, value)
    if row is None:
        ch.error("No such variant.")
        raise typer.Exit(1)
    ch.success(f"License set: {value}")


def _parse_ops(spec: str) -> list[dict]:
    """Parse 'chroma_key:#FF00FF,quantize:16,downscale:64' → op dicts."""
    ops: list[dict] = []
    for token in (t.strip() for t in spec.split(",") if t.strip()):
        name, _, arg = token.partition(":")
        name, arg = name.strip(), arg.strip()
        if name == "chroma_key":
            ops.append({"op": "chroma_key", "color": arg or "#FF00FF"})
        elif name == "quantize":
            ops.append({"op": "quantize", "colors": int(arg or 16)})
        elif name == "downscale":
            ops.append({"op": "downscale", "size": int(arg or 64)})
        elif name in ("crop", "normalize", "remove_solid_bg"):
            ops.append({"op": name})
        else:
            raise typer.BadParameter(f"unknown op: {name}")
    return ops


# ── video downloads (merged from `navig download` — navig-download plugin) ────
# Back-compat convenience: when the navig-download plugin is installed AND enabled,
# its download app also mounts here as `navig media tiktok …` / `navig media
# download <url>` (the canonical home is now the top-level `navig download`).
# Disabled or missing → actionable stubs, so the disable toggle unwires this
# surface too, not only the top-level `download`/`tiktok` entry-point verbs.


def _navig_download_disabled() -> bool:
    try:
        from navig.plugins.package import disabled_plugin_ids  # noqa: PLC0415

        return "navig-download" in disabled_plugin_ids()
    except Exception:  # noqa: BLE001 — state read must never break `navig media`
        return False


def _register_download_stub(reason: str, activate: str) -> None:
    @media_app.command("download")
    @media_app.command("tiktok", hidden=True)
    def _media_download_stub() -> None:
        """Download a video — provided by the navig-download plugin (or `navig download`)."""
        ch.info(f"'media download' is provided by plugin [bold]navig-download[/bold] ({reason})")
        ch.dim(f"  Activate: {activate}")
        raise typer.Exit(2)


if _navig_download_disabled():
    _register_download_stub("installed, currently disabled", "navig plugin enable navig-download")
else:
    try:
        from navig_download.commands.download import tiktok_app as _tiktok_app
        from navig_download.commands.download import tiktok_download as _tiktok_download

        media_app.add_typer(_tiktok_app, name="tiktok")
        media_app.command(
            "download",
            help="Download a video (TikTok & friends) — same engine as `navig download`.",
        )(_tiktok_download)
    except ImportError:  # plugin not installed — actionable stubs instead of silence
        from navig.plugins.require import install_hint  # noqa: PLC0415

        _register_download_stub("not installed", install_hint("navig-download"))
