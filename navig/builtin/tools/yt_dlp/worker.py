"""
yt_dlp worker — download videos/audio or fetch metadata via yt-dlp.
CLI path: media yt download | info | formats
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from common import Timer, emit, err, ok, require_on_path, run

TOOL = "yt_dlp"


def _ytdlp():
    # Try yt-dlp first, fall back to youtube-dl
    for name in ("yt-dlp", "yt_dlp", "youtube-dl"):
        try:
            return require_on_path(name)
        except FileNotFoundError:
            continue
    raise FileNotFoundError("yt-dlp not found. Install: pip install yt-dlp")


def cmd_download(args: dict) -> dict:
    t = Timer()
    try:
        exe = _ytdlp()
        url = args["url"]
        output = args.get("output", "%(title)s.%(ext)s")
        fmt = args.get("format", "bestvideo+bestaudio/best")
        audio = args.get("audio", False)
        dry = args.get("dry_run", False)

        cmd = [exe, url, "-o", output]
        if audio:
            cmd += ["-x", "--audio-format", "mp3"]
        else:
            cmd += ["-f", fmt]
        if dry:
            cmd.append("--simulate")

        rc, out, er = run(cmd, timeout=300)
        if rc != 0:
            return err(TOOL, "download", [er or out], ms=t.ms())
        return ok(
            TOOL,
            "download",
            {
                "url": url,
                "output": output,
                "audio_only": audio,
                "dry_run": dry,
                "log": out[-2000:] if out else "",
            },
            ms=t.ms(),
        )
    except Exception as e:
        return err(TOOL, "download", [str(e)], ms=t.ms())


def cmd_info(args: dict) -> dict:
    t = Timer()
    try:
        exe = _ytdlp()
        rc, out, er = run(
            [exe, "--dump-json", "--no-playlist", args["url"]], timeout=30
        )
        if rc != 0:
            return err(TOOL, "info", [er or out], ms=t.ms())
        try:
            meta = json.loads(out)
            data = {
                k: meta.get(k)
                for k in (
                    "id",
                    "title",
                    "uploader",
                    "duration",
                    "view_count",
                    "upload_date",
                    "description",
                    "thumbnail",
                    "webpage_url",
                    "ext",
                    "filesize_approx",
                )
            }
        except Exception:
            data = {"raw": out[:3000]}
        return ok(TOOL, "info", data, ms=t.ms())
    except Exception as e:
        return err(TOOL, "info", [str(e)], ms=t.ms())


def cmd_formats(args: dict) -> dict:
    t = Timer()
    try:
        exe = _ytdlp()
        rc, out, er = run([exe, "--list-formats", args["url"]], timeout=30)
        if rc != 0:
            return err(TOOL, "formats", [er or out], ms=t.ms())
        return ok(TOOL, "formats", {"url": args["url"], "output": out}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "formats", [str(e)], ms=t.ms())


HANDLERS = {"download": cmd_download, "info": cmd_info, "formats": cmd_formats}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handler = HANDLERS.get(req.get("command", ""))
            emit(
                handler(req.get("args", {}))
                if handler
                else err(TOOL, req.get("command", "?"), ["Unknown command"])
            )
        except Exception as e:
            emit(err(TOOL, "?", [str(e)]))


if __name__ == "__main__":
    main()
