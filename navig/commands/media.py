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
from navig.platform.paths import resolve_user_path

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
    src = resolve_user_path(source).expanduser()
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
    frames = extract_frames(resolve_user_path(video).expanduser(), out, mode=mode, max_frames=max_frames)
    ch.success(f"{len(frames)} frame(s) → {out}")


@media_app.command("probe")
def media_probe(video: str = typer.Argument(..., help="Video file.")) -> None:
    """Print video metadata (duration / resolution / fps) via ffprobe."""
    from navig.media.frames import probe  # noqa: PLC0415
    meta = probe(resolve_user_path(video).expanduser())
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

    root = resolve_user_path(directory).expanduser()
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
    root = resolve_user_path(directory).expanduser()
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
    root = resolve_user_path(directory).expanduser()
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
    root = resolve_user_path(directory).expanduser()
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
    port: int | None = typer.Option(
        None, "--port", "-p",
        help="Default: 8770, or a free port if the OS has that range reserved."),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open the browser."),
) -> None:
    """Launch a local web gallery for any media folder (grid + preview, streams video)."""
    from navig.http_bind import PortBindError  # noqa: PLC0415
    from navig.media.browse import serve  # noqa: PLC0415
    root = resolve_user_path(directory).expanduser()
    if not root.is_dir():
        ch.error(f"Not a directory: {root}")
        raise typer.Exit(1)
    ch.success("Media gallery starting…   (Ctrl+C to stop)")
    # serve() prints the URL and opens the browser: with no --port the port is only
    # known after binding (the preferred one may be reserved by the OS).
    try:
        serve(root, port, open_browser=not no_open)
    except PortBindError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        pass


# NOTE: the universal explorer moved to its own top-level command — `navig explore`
# (plugin: navig-explore). `navig media` is deprecated; don't add surface here.


# ── generate + iterate + process: navig-generate's commands, mounted here ─────
# They live in navig_generate.commands.generate so `navig-generate` runs them without
# navig; appended in this position so `navig generate --help` lists them as before.
# Sub-groups (registered_groups, e.g. `navig generate eleven …`) must ride along too —
# copying only the flat commands would mount every verb EXCEPT the group's.
# Guarded: navig-generate is a hard dependency, but a broken or half-upgraded install must
# not take core's own analyse / frames / probe / browse verbs down with it — the
# generation verbs answer with the repair instead.
try:
    from navig_generate.commands.generate import generate_app as _generate_app  # noqa: E402
except ImportError as _generate_exc:  # pragma: no cover - exercised by a broken install only
    _GENERATE_IMPORT_ERROR = str(_generate_exc)

    @media_app.command("gen")
    def _media_gen_unavailable() -> None:
        """Generate media — provided by navig-generate (currently not importable)."""
        from navig.plugins.require import install_hint  # noqa: PLC0415

        ch.error(f"navig-generate could not be imported: {_GENERATE_IMPORT_ERROR}")
        ch.dim(f"  Repair: {install_hint('navig-generate')}")
        raise typer.Exit(2)
else:
    media_app.registered_commands.extend(_generate_app.registered_commands)
    media_app.registered_groups.extend(_generate_app.registered_groups)


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
    _register_download_stub("installed, currently disabled", "navig store enable plugin:navig-download")
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
