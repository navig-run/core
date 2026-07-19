"""tool.py — CLI fallback for yt_dlp (spawn-per-call)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_download, cmd_formats, cmd_info  # noqa: E402

TOOL = "yt_dlp"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navig media yt", description="yt-dlp media downloader"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_dl = sub.add_parser("download", help="Download media from URL")
    p_dl.add_argument("--url", required=True)
    p_dl.add_argument(
        "--output", default=None, help="Output directory or filename template"
    )
    p_dl.add_argument(
        "--format", default="bestvideo+bestaudio/best", help="Format selector"
    )
    p_dl.add_argument(
        "--audio", action="store_true", default=False, help="Extract audio only (mp3)"
    )
    p_dl.add_argument(
        "--playlist", action="store_true", default=False, help="Download full playlist"
    )
    p_dl.add_argument(
        "--subtitles", action="store_true", default=False, help="Embed subtitles"
    )
    p_dl.add_argument("--dry-run", action="store_true", default=False)

    p_info = sub.add_parser("info", help="Fetch metadata for a URL")
    p_info.add_argument("--url", required=True)

    p_fmt = sub.add_parser("formats", help="List available formats for a URL")
    p_fmt.add_argument("--url", required=True)

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    dispatch = {
        "download": cmd_download,
        "info": cmd_info,
        "formats": cmd_formats,
    }

    if command in dispatch:
        result = dispatch[command](params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
