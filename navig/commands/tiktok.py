"""NAVIG TikTok CLI — download + AI briefings, powered by rapidok + yt-dlp.

Wraps the `rapidok` TikTok downloader (https://github.com/miztizm/rapidok) the
same way `navig farmore` wraps farmore: a thin, lazily-loaded subcommand that
degrades gracefully if the (default-installed) package is missing.

  navig tiktok download <url>     organized archival download (rapidok)
  navig tiktok profile  <user>    download a whole profile (rapidok)
  navig tiktok info     <url>     metadata: creator · country · description · stats
  navig tiktok comments <url>     top comments ranked by likes
  navig tiktok analyse  <url>     AI markdown briefing (description + top comments)
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Annotated

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

tiktok_app = typer.Typer(
    name="tiktok",
    help="🎵 TikTok downloader + AI briefings (via rapidok)",
    no_args_is_help=True,
)


def _require_rapidok() -> bool:
    from navig.tiktok import rapidok_available

    if rapidok_available():
        return True
    ch.error(
        "rapidok is not installed (it ships with navig by default). Install it with:\n"
        "  pip install rapidok\n"
        "or from source:\n"
        "  pip install -e /path/to/rapidok"
    )
    return False


def _require_ytdlp() -> bool:
    from navig.tiktok import ytdlp_available

    if ytdlp_available():
        return True
    ch.error("yt-dlp is not installed (it ships with `rapidok`).\n  pip install yt-dlp")
    return False


def _fmt_stats(meta: dict) -> str:
    bits = []
    for label, key in (("views", "view_count"), ("likes", "like_count"),
                       ("comments", "comment_count"), ("shares", "repost_count")):
        if meta.get(key) is not None:
            bits.append(f"{label} {meta[key]:,}")
    return " · ".join(bits)


# ── download / profile (rapidok) ──────────────────────────────────────────────

@tiktok_app.command("download")
def tiktok_download(
    url: Annotated[str, typer.Argument(help="TikTok video/photo URL")],
    out: Annotated[str, typer.Option("--out", "-o", help="Output directory")] = "downloads",
    watermark: Annotated[bool, typer.Option("--watermark", help="Keep the watermark")] = False,
) -> None:
    """Download a TikTok post (organized by creator) via rapidok."""
    if not _require_rapidok():
        raise typer.Exit(1)
    from navig.tiktok import engine

    ch.info(f"Downloading {url} → {out}/ …")
    res = engine.download_rapidok(url, output_dir=out, watermark=watermark)
    if res["ok"]:
        ch.success(f"Done → {res['output_dir']}")
    else:
        ch.error(res["stdout"].strip()[-800:] or "rapidok download failed")
        raise typer.Exit(res["returncode"] or 1)


@tiktok_app.command("profile")
def tiktok_profile(
    username: Annotated[str, typer.Argument(help="TikTok @username (no @ needed)")],
    out: Annotated[str, typer.Option("--out", "-o", help="Output directory")] = "downloads",
    max_downloads: Annotated[int, typer.Option("--max", help="Max videos to fetch")] = 0,
    watermark: Annotated[bool, typer.Option("--watermark", help="Keep the watermark")] = False,
) -> None:
    """Download all (or --max) videos from a creator's profile via rapidok."""
    if not _require_rapidok():
        raise typer.Exit(1)
    user = username.lstrip("@")
    cmd = [sys.executable, "-m", "rapidok", "--profile", user, "--output-dir", out]
    cmd.append("--watermark" if watermark else "--no-watermark")
    if max_downloads > 0:
        cmd += ["--max-downloads", str(max_downloads)]
    ch.info(f"Downloading @{user}'s profile → {out}/ …")
    raise typer.Exit(subprocess.run(cmd).returncode)  # noqa: S603


# ── metadata / analysis (yt-dlp) ──────────────────────────────────────────────

@tiktok_app.command("info")
def tiktok_info(
    url: Annotated[str, typer.Argument(help="TikTok URL")],
) -> None:
    """Show metadata: creator, country, description, and stats."""
    if not _require_ytdlp():
        raise typer.Exit(1)
    from navig.tiktok import engine

    meta = engine.info(url)
    ch.console.print(f"[bold]🎵 {meta.get('uploader') or 'TikTok'}[/bold]"
                     + (f"  ·  🌍 {meta['country']}" if meta.get("country") else "  ·  🌍 country n/a"))
    if meta.get("description"):
        ch.console.print(f"\n{meta['description'][:1000]}")
    stats = _fmt_stats(meta)
    if stats:
        ch.console.print(f"\n[dim]{stats}[/dim]")
    if meta.get("url"):
        ch.console.print(f"[dim]{meta['url']}[/dim]")


@tiktok_app.command("comments")
def tiktok_comments(
    url: Annotated[str, typer.Argument(help="TikTok URL")],
    top: Annotated[int, typer.Option("--top", "-n", help="How many top comments")] = 10,
) -> None:
    """List the top comments, ranked by likes."""
    if not _require_ytdlp():
        raise typer.Exit(1)
    from navig.tiktok import engine

    ch.info("Fetching comments (this can take a few seconds) …")
    meta = engine.info_with_comments(url, max_comments=top)
    comments = meta.get("comments") or []
    if not comments:
        ch.warning("No comments available for this post.")
        return
    for c in comments:
        author = f"@{c['author']}" if c.get("author") else "—"
        ch.console.print(f"[green]❤ {c['likes']:>5,}[/green]  [dim]{author}[/dim]  {c['text'][:300]}")


@tiktok_app.command("analyse")
def tiktok_analyse(
    url: Annotated[str, typer.Argument(help="TikTok URL")],
    comments: Annotated[int, typer.Option("--comments", "-c", help="Comments to weigh")] = 20,
) -> None:
    """AI markdown briefing of a video (description + best comments combined)."""
    if not _require_ytdlp():
        raise typer.Exit(1)
    from navig.tiktok import engine

    ch.info("Analysing (fetching metadata + comments, then briefing) …")
    result = asyncio.run(engine.analyse(url, max_comments=comments))
    meta = result["meta"]
    head = f"[bold]🎵 {meta.get('uploader') or 'TikTok'}[/bold]"
    if meta.get("country"):
        head += f"  ·  🌍 {meta['country']}"
    ch.console.print(head)
    stats = _fmt_stats(meta)
    if stats:
        ch.console.print(f"[dim]{stats}[/dim]\n")
    if result["brief"]:
        ch.console.print(result["brief"])
    else:
        ch.warning("AI briefing unavailable (no model configured?) — showing raw data.")
        if meta.get("description"):
            ch.console.print(f"\n{meta['description'][:1000]}")
        for c in (meta.get("comments") or [])[:10]:
            ch.console.print(f"  ❤ {c['likes']:,}  {c['text'][:200]}")


# alias-friendly callback (so `navig tt …` can map here via registration)
tt_app = tiktok_app
