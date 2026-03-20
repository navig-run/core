```skill
---
name: nssm-service-manager
description: Install, start, stop, remove, and query Windows services using NSSM
user-invocable: true
navig-commands:
  - navig sys nssm install --name {service} --exe {path}
  - navig sys nssm start --name {service}
  - navig sys nssm stop --name {service}
  - navig sys nssm status --name {service}
  - navig sys nssm remove --name {service} --yes
requires:
  - nssm.exe (from C:\USB\system\nssm\win64\nssm.exe)
  - Admin rights for install/remove
os: [windows]
examples:
  - "Install my Node.js app as a Windows service"
  - "Stop the myapp service"
  - "Is the backup service running?"
  - "Remove the old scheduler service"
---

# NSSM Windows Service Manager

Install any executable as a Windows service and manage its lifecycle.

## Prerequisites

- **Admin required** for `install` and `remove`
- NSSM binary from USB at `C:\USB\system\nssm\win64\nssm.exe`
- Windows only

## Common Tasks

### Install an app as a service

**User says:** "Run my Python script as a Windows service"

```bash
navig sys nssm install --name myapp --exe "C:\Server\bin\python\python-3.12\python.exe" --args "C:\myapp\main.py"
```

Then start it:

```bash
navig sys nssm start --name myapp
```

### Check service status

```bash
navig sys nssm status --name myapp
```

### Stop a service

```bash
navig sys nssm stop --name myapp
```

### Remove a service

```bash
navig sys nssm remove --name myapp --yes
```

> Requires `--yes` to confirm destructive removal.
> Use `--dry-run` to preview what would happen.

## Safety Notes

- `--dry-run` supported for `install` and `remove`
- Admin check is performed early; exits with code `3` if elevation needed
```
