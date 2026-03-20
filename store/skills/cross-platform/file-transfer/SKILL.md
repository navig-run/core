---
name: file-transfer
description: Upload, download, and edit files on remote servers via NAVIG
user-invocable: true
navig-commands:
  - navig file upload {local} {remote}
  - navig file download {remote} {local}
  - navig file edit {remote_path}
  - navig sync pull {remote_path} {local_path}
  - navig sync push {local_path} {remote_path}
examples:
  - "Download the nginx config"
  - "Upload this file to the server"
  - "Edit the .env file on production"
  - "Sync the website files"
  - "Pull the logs from the server"
---

# File Transfer & Editing

Upload, download, edit remote files, and sync directories between local and remote servers.

## Common Tasks

### Download a File

**User says:** "Download the nginx config" / "Get the .env file"

```bash
navig host use {host}
navig file download /etc/nginx/nginx.conf ./nginx.conf
```

**Response:**
```
📥 Downloaded from {host}:

/etc/nginx/nginx.conf → ./nginx.conf (2.3KB)

✅ File saved locally
```

### Upload a File

**User says:** "Upload index.html to the server"

```bash
navig file upload ./index.html /var/www/html/index.html
```

**Response:**
```
📤 Uploaded to {host}:

./index.html → /var/www/html/index.html (4.5KB)

✅ File uploaded successfully
```

### Edit a Remote File

**User says:** "Edit the .env on production"

```bash
navig file edit /var/www/app/.env
```

Opens the file in your local editor with remote sync.

### Sync a Directory (Pull)

**User says:** "Sync the website files to my computer"

```bash
navig sync pull /var/www/html ./local-copy
```

**Response:**
```
📥 Syncing from {host}:/var/www/html...

Transferred: 45 files (12.3MB)
New: 3 files
Updated: 8 files
Unchanged: 34 files

✅ Sync complete → ./local-copy/
```

### Sync a Directory (Push)

**User says:** "Deploy my local changes to the server"

```bash
navig sync push ./dist /var/www/html
```

⚠️ **Confirm before push**: "This will overwrite remote files. Continue? (yes/no)"

## Common Files to Download

| File | Path | Purpose |
|------|------|---------|
| Nginx config | `/etc/nginx/nginx.conf` | Web server config |
| App .env | `/var/www/app/.env` | Environment variables |
| SSH config | `~/.ssh/config` | SSH settings |
| Nginx error log | `/var/log/nginx/error.log` | Debug web errors |
| System log | `/var/log/syslog` | System events |

## Safety Rules

- **Downloads**: Always safe, no confirmation needed
- **Uploads**: Confirm if overwriting existing file
- **Sync push**: Always confirm (overwrites remote)
- **Editing**: Create automatic backup before edit

## Error Handling

- **File not found**: "File '{path}' doesn't exist on {host}. Check the path."
- **Permission denied**: "Can't access '{path}'. Try with sudo or check permissions."
- **No space**: "Upload failed — server disk is full. Free some space first."
- **Large file**: "This file is 500MB. Download may take a while. Continue?"


