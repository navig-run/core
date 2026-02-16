# NAVIG Tray — Windows System Tray Launcher

Run NAVIG services (Gateway, Agent) from the Windows system tray.
No terminal window needed — right-click the tray icon for full control.

## Commands

| Command | Description |
|---------|-------------|
| `navig tray start` | Launch tray app (background) |
| `navig tray start -f` | Launch in foreground (with console) |
| `navig tray stop` | Stop the tray app |
| `navig tray status` | Check if tray is running |
| `navig tray status --json` | Status as JSON |
| `navig tray install` | Desktop shortcut |
| `navig tray install --auto-start` | Shortcut + Windows auto-start |
| `navig tray uninstall` | Remove shortcut, auto-start, settings |

## Tray Menu

Right-click the icon near the clock:
- Start/Stop/Restart **Gateway** and **Agent**
- Quick Actions: Dashboard, Status, Vault, Skills
- Toggle auto-start with Windows
- Open log folder

## Status Icon

- **Green** = services running
- **Yellow** = starting
- **Red** = error
- **Gray** = stopped

## Requirements

Windows + `pip install pystray Pillow` (auto-checked on install)

## Settings

`~/.navig/tray_settings.json` — auto-start, services to launch, gateway port.
