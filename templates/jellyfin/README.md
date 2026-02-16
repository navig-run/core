# Jellyfin Addon for NAVIG

Free Software Media System. The free alternative to Emby and Plex for organizing and streaming your personal media collection.

## Features

- **Free & Open Source**: No premium features, no tracking, completely free
- **Media Streaming**: Movies, TV Shows, Music, Books, Photos
- **Hardware Transcoding**: VAAPI, NVENC, QSV, V4L2M2M support
- **Live TV & DVR**: Built-in TV guide and recording
- **Multi-User**: Separate profiles with parental controls
- **Client Apps**: Web, mobile, TV, and desktop apps
- **Subtitle Support**: Automatic download and customization
- **Plugin System**: Extensible with community plugins

## Prerequisites

- 2GB+ RAM (4GB+ recommended for transcoding)
- CPU with hardware transcoding (optional but recommended)
- Sufficient storage for media library
- Network access for streaming

## Usage

```bash
# Enable the Jellyfin addon
navig addon enable jellyfin

# Check service status
navig addon run jellyfin status

# Restart Jellyfin
navig addon run jellyfin restart

# View live logs
navig addon run jellyfin logs

# Trigger library scan
navig addon run jellyfin scan_library

# Clear image cache
navig addon run jellyfin clear_cache

# Update Jellyfin
navig addon run jellyfin update

# Backup configuration
navig addon run jellyfin backup_config

# Check hardware transcoding support
navig addon run jellyfin hardware_info
```

## Configuration

### Template Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `config_dir` | Configuration directory | `/etc/jellyfin` |
| `data_dir` | Data directory | `/var/lib/jellyfin` |
| `media_dir` | Media library location | `/srv/media` |
| `default_port` | HTTP port | `8096` |

### Environment Variables

```bash
JELLYFIN_CONFIG_DIR=/etc/jellyfin
JELLYFIN_DATA_DIR=/var/lib/jellyfin
JELLYFIN_CACHE_DIR=/var/cache/jellyfin
JELLYFIN_LOG_DIR=/var/log/jellyfin
JELLYFIN_FFMPEG=/usr/lib/jellyfin-ffmpeg/ffmpeg
```

## Installation

### Debian/Ubuntu

```bash
# Add repository
curl -fsSL https://repo.jellyfin.org/ubuntu/jellyfin_team.gpg.key | gpg --dearmor -o /usr/share/keyrings/jellyfin.gpg
echo "deb [signed-by=/usr/share/keyrings/jellyfin.gpg] https://repo.jellyfin.org/ubuntu $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/jellyfin.list

# Install
apt update
apt install -y jellyfin

# Start service
systemctl enable --now jellyfin
```

### Using Docker

```bash
docker run -d \
  --name jellyfin \
  --user 1000:1000 \
  -p 8096:8096 \
  -p 8920:8920 \
  -v /opt/jellyfin/config:/config \
  -v /opt/jellyfin/cache:/cache \
  -v /srv/media:/media:ro \
  --device=/dev/dri:/dev/dri \
  --restart unless-stopped \
  jellyfin/jellyfin
```

## Hardware Transcoding

### Intel Quick Sync (QSV)

```bash
# Install drivers
apt install -y intel-media-va-driver-non-free vainfo

# Verify
vainfo

# Add jellyfin to render group
usermod -aG render jellyfin
systemctl restart jellyfin
```

### NVIDIA NVENC

```bash
# Install drivers (use your distribution's method)
apt install -y nvidia-driver-535

# Verify
nvidia-smi

# Docker: add --gpus all flag
docker run -d --gpus all ...
```

### AMD VAAPI

```bash
# Install drivers
apt install -y mesa-va-drivers vainfo

# Verify
vainfo

# Add jellyfin to video and render groups
usermod -aG video,render jellyfin
systemctl restart jellyfin
```

Enable in Dashboard → Playback → Transcoding → Hardware acceleration

## Media Organization

Recommended folder structure:
```
/srv/media/
├── movies/
│   ├── Movie Name (2024)/
│   │   └── Movie Name (2024).mkv
│   └── Another Movie (2023)/
├── tvshows/
│   ├── Show Name/
│   │   ├── Season 01/
│   │   │   ├── Show Name - S01E01 - Episode Title.mkv
│   │   │   └── Show Name - S01E02 - Episode Title.mkv
│   │   └── Season 02/
│   └── Another Show/
├── music/
│   ├── Artist Name/
│   │   └── Album Name/
│   │       ├── 01 - Track.flac
│   │       └── 02 - Track.flac
└── photos/
    └── 2024/
        └── January/
```

## Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name media.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name media.example.com;

    ssl_certificate /etc/letsencrypt/live/media.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/media.example.com/privkey.pem;

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8096;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $http_host;
        
        proxy_buffering off;
    }

    location /socket {
        proxy_pass http://127.0.0.1:8096;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

## API Examples

```bash
# Authenticate and get API key (use Dashboard → API Keys instead)
# Get all users
curl "http://localhost:8096/Users" \
  -H "X-Emby-Token: YOUR_API_KEY"

# Get all movies
curl "http://localhost:8096/Items?IncludeItemTypes=Movie&Recursive=true" \
  -H "X-Emby-Token: YOUR_API_KEY"

# Trigger library scan
curl -X POST "http://localhost:8096/Library/Refresh" \
  -H "X-Emby-Token: YOUR_API_KEY"

# Get server info
curl "http://localhost:8096/System/Info/Public"
```

## Popular Plugins

Install via Dashboard → Plugins → Catalog:

- **Intro Skipper**: Automatic intro detection and skip
- **Fanart**: Enhanced artwork from fanart.tv
- **OpenSubtitles**: Automatic subtitle downloads
- **Trakt**: Sync watch history with Trakt
- **Playback Reporting**: Detailed playback statistics
- **Merge Versions**: Combine multiple versions of same media

## Resources

- [Official Documentation](https://jellyfin.org/docs/)
- [GitHub Repository](https://github.com/jellyfin/jellyfin)
- [API Documentation](https://api.jellyfin.org/)
- [Reddit Community](https://reddit.com/r/jellyfin)
- [Matrix Chat](https://matrix.to/#/#jellyfinorg:matrix.org)


