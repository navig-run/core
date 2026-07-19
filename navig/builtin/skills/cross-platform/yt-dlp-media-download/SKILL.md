```skill
---
name: yt-dlp-media-download
description: Download video/audio from YouTube and 1000+ sites using yt-dlp
user-invocable: true
navig-commands:
  - navig media yt download --url {url}
  - navig media yt download --url {url} --audio
  - navig media yt download --url {url} --format {fmt} --output {dir}
  - navig media yt info --url {url}
  - navig media yt formats --url {url}
requires:
  - yt-dlp in PATH (pip install yt-dlp or winget install yt-dlp)
  - ffmpeg recommended for format conversion and audio extraction
os: [windows, linux, mac]
examples:
  - "Download this YouTube video"
  - "Download just the audio from this URL as mp3"
  - "What formats are available for this video?"
  - "Download a playlist"
  - "Get video metadata without downloading"
---

# yt-dlp Media Downloader

Download video and audio from YouTube, Twitch, Twitter/X, Reddit, SoundCloud, and 1000+ sites.

## Prerequisites

- `yt-dlp` in PATH: `pip install yt-dlp` or `winget install yt-dlp`
- `ffmpeg` strongly recommended (required for audio extraction, merging formats)
- Tool falls back to `youtube-dl` if `yt-dlp` is not found

## Common Tasks

### Download video (best quality)

**User says:** "Download this YouTube video"

```bash
navig media yt download --url "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### Download audio only (MP3)

**User says:** "I just want the audio"

```bash
navig media yt download --url "https://www.youtube.com/watch?v=..." --audio
```

### Download to specific directory

```bash
navig media yt download --url {url} --output "C:\Music\%(title)s.%(ext)s"
```

### Browse available formats

```bash
navig media yt formats --url {url}
```

Returns format ID, resolution, codec, and file size for each option.

### Select specific format

```bash
navig media yt download --url {url} --format "137+140"
```

### Fetch metadata only

```bash
navig media yt info --url {url}
```

Returns title, uploader, duration, view count, thumbnails, etc.

### Download full playlist

```bash
navig media yt download --url "https://youtube.com/playlist?list=..." --playlist
```

### Download with subtitles

```bash
navig media yt download --url {url} --subtitles
```

### Dry-run

```bash
navig media yt download --url {url} --dry-run
```

## Safety Notes

- `--dry-run` prints the yt-dlp command without downloading
- Respects yt-dlp's rate limits and site-specific restrictions
- Geo-blocked content requires a VPN or cookies — not handled by this tool
```
