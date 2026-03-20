---
name: media-factory
description: Media processing and playback automation using FFmpeg, MPV, and AutoHotkey.
metadata:
  navig:
    emoji: 🎬
    requires:
      bins: [ffmpeg, magick, youtube-dl, mpv, mediainfo]
---

# Media Factory Skill

Automate video, audio, and image processing workflows, and control media playback.

## Playback (mpv)

### Basic Playback
```bash
# Play a file
mpv "video.mp4"

# Play with IPC socket for control
mpv --input-ipc-server=\\.\pipe\mpvsocket "video.mp4"
```

### Auto-Continue / Playlist
```bash
# Play a playlist file
mpv --playlist=playlist.m3u

# Play all files in directory
mpv "C:\Music\*"
```

### Remote Control (via IPC)
Send JSON commands to the running MPV instance.
```bash
# Pause
echo '{ "command": ["set_property", "pause", true] }' | navig run --stdin --pipe-to-mpv
```

## Global Media Control (AHK)

Control system-wide media, regardless of the active application.

```bash
# Play/Pause
navig ahk send "{Media_Play_Pause}"

# Next Track
navig ahk send "{Media_Next}"

# Volume Up
navig ahk send "{Volume_Up}"
```

## Video Processing (FFmpeg)

### Download
```bash
# Download best quality video
youtube-dl -f bestvideo+bestaudio "URL"

# Download audio only (MP3)
youtube-dl -x --audio-format mp3 "URL"
```

### Convert & Compress
```bash
# Convert to MP4 (H.264/AAC) for web compatibility
ffmpeg -i input.mkv -c:v libx264 -crf 23 -c:a aac -b:a 128k output.mp4

# Extract audio
ffmpeg -i video.mp4 -vn -c:a copy audio.aac
```

### Editing
```bash
# Trim video (Start at 00:01:00, duration 30s)
ffmpeg -i input.mp4 -ss 00:01:00 -t 00:00:30 -c copy clip.mp4
```

## Metadata (MediaInfo)
```bash
# Get JSON metadata
mediainfo --Output=JSON input.mp4
```

## Image Operations (ImageMagick)

### Batch Processing
```bash
# Convert PNG to JPG (Quality 85%)
mogrify -format jpg -quality 85 *.png

# Resize all images to width 1080px (maintain aspect ratio)
mogrify -resize 1080x *.jpg
```

## Best Practices
1. **MPV IPC**: Use the IPC socket for programmed control of the player.
2. **AHK for Global**: Use AHK when you just want to "hit the pause button" on whatever is playing (Spotify, YouTube, etc.).
3. **CRF for Quality**: Use `-crf 18-28` for balance between size and quality.



