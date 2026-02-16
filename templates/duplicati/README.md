# Duplicati Addon for NAVIG

Free backup software with strong encryption and cloud storage support. Features AES-256 encryption, incremental backups, deduplication, and support for 20+ storage backends.

## Features

- **Strong Encryption**: AES-256 encryption before data leaves your machine
- **Cloud Storage**: S3, Azure, B2, Dropbox, Google Drive, OneDrive, and more
- **Incremental Backups**: Only changed data is uploaded
- **Deduplication**: Intelligent block-level deduplication
- **Scheduling**: Built-in scheduler for automated backups
- **Web Interface**: Easy-to-use browser-based management
- **Compression**: Built-in compression to reduce storage usage
- **Verification**: Automatic backup verification

## Prerequisites

- Mono runtime (Linux) or .NET Framework (Windows)
- Supported storage destination (local, cloud, or remote server)
- Network access to storage backend

## Usage

```bash
# Enable the Duplicati addon
navig addon enable duplicati

# Check service status
navig addon run duplicati status

# Restart Duplicati service
navig addon run duplicati restart

# Run backup immediately
navig addon run duplicati backup_now

# List all configured backups
navig addon run duplicati list_backups

# Verify backup integrity
navig addon run duplicati verify

# Restore files from backup
navig addon run duplicati restore

# Repair backup database
navig addon run duplicati repair

# Compact backup storage
navig addon run duplicati compact

# View live logs
navig addon run duplicati logs
```

## Configuration

### Template Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `install_dir` | Installation directory | `/opt/duplicati` |
| `config_dir` | Configuration directory | `/root/.config/Duplicati` |
| `default_port` | Web UI port | `8200` |

### Environment Variables

```bash
DUPLICATI_HOME=/opt/duplicati
MONO_EXTERNAL_ENCODINGS=utf8
```

## Installation

### Debian/Ubuntu

```bash
# Install dependencies
apt update
apt install -y mono-complete ca-certificates-mono

# Download and install
wget https://updates.duplicati.com/stable/duplicati_2.0.8.1-1_all.deb
apt install -y ./duplicati_2.0.8.1-1_all.deb
```

### Using Docker

```bash
docker run -d \
  --name=duplicati \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=Etc/UTC \
  -p 8200:8200 \
  -v /opt/duplicati/config:/config \
  -v /backups:/backups \
  -v /source:/source:ro \
  --restart unless-stopped \
  lscr.io/linuxserver/duplicati:latest
```

## Systemd Service

```ini
# /etc/systemd/system/duplicati.service
[Unit]
Description=Duplicati Backup Service
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/duplicati-server --webservice-port=8200 --webservice-interface=any
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
systemctl daemon-reload
systemctl enable --now duplicati
```

## Access Control

Set password for web interface:
```bash
# Via command line
duplicati-cli set-password --password=your_password

# Or access http://localhost:8200 and set during first run
```

## Command Line Examples

### Create Backup Job

```bash
# Backup to local folder
duplicati-cli backup \
  "file:///backups/mybackup" \
  "/home/user/documents" \
  --passphrase="encryption_password"

# Backup to S3
duplicati-cli backup \
  "s3://bucket-name/path" \
  "/var/www" \
  --aws-access-key-id="YOUR_KEY" \
  --aws-secret-access-key="YOUR_SECRET" \
  --passphrase="encryption_password"

# Backup to Backblaze B2
duplicati-cli backup \
  "b2://bucket-name/path" \
  "/data" \
  --b2-accountid="YOUR_ACCOUNT_ID" \
  --b2-applicationkey="YOUR_APP_KEY" \
  --passphrase="encryption_password"
```

### Restore Files

```bash
# Restore entire backup
duplicati-cli restore \
  "file:///backups/mybackup" \
  --restore-path="/restore/target" \
  --passphrase="encryption_password"

# Restore specific files
duplicati-cli restore \
  "file:///backups/mybackup" \
  --restore-path="/restore/target" \
  --include="*.pdf" \
  --passphrase="encryption_password"

# Restore from specific date
duplicati-cli restore \
  "file:///backups/mybackup" \
  --restore-path="/restore/target" \
  --time="2024-01-15" \
  --passphrase="encryption_password"
```

### List and Verify

```bash
# List backup versions
duplicati-cli list \
  "file:///backups/mybackup" \
  --passphrase="encryption_password"

# Verify backup integrity
duplicati-cli test \
  "file:///backups/mybackup" \
  --passphrase="encryption_password"

# Show backup statistics
duplicati-cli compare \
  "file:///backups/mybackup" \
  --passphrase="encryption_password"
```

## Backup Strategies

### 3-2-1 Backup Rule

```bash
# Local backup
duplicati-cli backup "file:///backups/local" "/important" --passphrase="pass"

# Remote backup (different location)
duplicati-cli backup "sftp://user@remote/backups" "/important" --passphrase="pass"

# Cloud backup (offsite)
duplicati-cli backup "s3://bucket/backups" "/important" --passphrase="pass"
```

### Retention Policy

```bash
duplicati-cli backup \
  "file:///backups/mybackup" \
  "/data" \
  --retention-policy="1W:1D,4W:1W,12M:1M" \
  --passphrase="encryption_password"
# Keep: daily for 1 week, weekly for 4 weeks, monthly for 12 months
```

## Web UI Features

Access at `http://localhost:8200`:

1. **Add Backup**: Configure new backup jobs with wizard
2. **Schedule**: Set automatic backup schedules
3. **Restore**: Browse and restore from any backup version
4. **Logs**: View detailed operation logs
5. **Settings**: Global encryption, retention, and bandwidth settings

## Resources

- [Official Documentation](https://docs.duplicati.com/)
- [GitHub Repository](https://github.com/duplicati/duplicati)
- [Forum](https://forum.duplicati.com/)
- [Storage Backend Reference](https://docs.duplicati.com/manual/storage-backends)
- [Command Line Reference](https://docs.duplicati.com/manual/command-line)


