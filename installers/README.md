# installers/ — the scripts *you* run

Everything here is for people **installing, setting up, or repairing NAVIG**. Maintainer
tooling (release, version bump, instruction sync) lives in [`../tools/`](../tools/README.md)
and is excluded from every published artifact.

Most people never need this directory — the one-liners at the repo root are the front door:

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/navig-run/core/main/install.sh | bash
```
```powershell
# Windows
irm https://raw.githubusercontent.com/navig-run/core/main/install.ps1 | iex
```

Reach for the scripts below when you want a specific platform variant, an unattended server
install, or a repair.

## Install

| Script | Platform | Notes |
|---|---|---|
| `install_navig_linux.sh` | Linux | Standard installer |
| `install_navig_linux_enhanced.sh` | Linux | Adds shell integration + extras |
| `install_navig_macos.sh` | macOS | Standard installer |
| `install_navig_windows.ps1` | Windows | Standard installer |
| `install_navig_windows_enhanced.ps1` | Windows | Adds shell integration + extras |
| `bootstrap_navig_linux.sh` | Linux | First-time bootstrap for a fresh host |
| `github-bootstrap.sh` | any | Repository / community bootstrap helper |
| `navig_quick_setup.sh` · `.ps1` | Unix · Windows | Fast post-install configuration |

See [`../docs/ENHANCED_INSTALLERS_GUIDE.md`](../docs/ENHANCED_INSTALLERS_GUIDE.md) for what
"enhanced" adds, and [`../docs/user/INSTALL_GUIDE.md`](../docs/user/INSTALL_GUIDE.md) for the
full walkthrough.

## Servers and services

| Script | Purpose |
|---|---|
| `install_navig_factory_server.sh` | Install the operational factory server as a service |
| `install_vps_synapse_bridges.sh` | Install Matrix / Synapse bridges on a VPS |
| `install_matrix_synapse_windows.ps1` · `.cmd` | Matrix / Synapse install on Windows |
| `navig_windows_remote_deploy.ps1` | Deploy NAVIG to a remote Windows host |
| `bridge-tunnel.service` | systemd unit for the bridge tunnel |

## Repair, reinstall, remove

| Script | Purpose |
|---|---|
| `navig_ubuntu_reinstall.sh` | Reinstall cleanly on Ubuntu |
| `navig_ubuntu_backup_uninstall.sh` | Back up, then uninstall on Ubuntu |
| `fix-navig-permissions.ps1` | Repair file permissions on Windows |
| `fix_windows_network_sharing.ps1` | Repair Windows network sharing |
| `mount_remote_drives.ps1` | Mount configured remote drives on Windows |

## Terminal appearance (PowerShell modules)

`navig-colors.psm1`, `navig-icons.psm1`, `navig-statusbar.psm1` — imported by the Windows
installers to give the NAVIG terminal its palette, Nerd Font glyphs and status bar. The
codepoints mirror `navig/ui/icons.py`; keep the two in sync.

## Shared internals

`_lib/installer_common.sh`, `_lib/installer_common.ps1`, `_lib/upgrade_helpers.py` are sourced
by the installers above. They are not entry points — do not run them directly.
