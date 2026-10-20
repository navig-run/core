"""Windows desktop toast — the ``toast`` notification channel.

No dependency: a short PowerShell one-liner drives the WinRT toast API. Title
and body travel in environment variables, never in the command line, so nothing
the notification contains is ever parsed as PowerShell. Off by default for every
type (the matrix opts it in); non-Windows hosts report "not supported" instead
of pretending to deliver.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

#: Windows PowerShell's own AppUserModelID — a toast needs a registered app id,
#: and this one is present on every Windows install.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$esc = { param($s) [System.Security.SecurityElement]::Escape($s) }
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastText02'><text id='1'>$(& $esc $env:NAVIG_TOAST_TITLE)</text><text id='2'>$(& $esc $env:NAVIG_TOAST_BODY)</text></binding></visual></toast>")
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:NAVIG_TOAST_APPID).Show($toast)
"""

_TIMEOUT_S = 15.0

_OFF_HINT = "Windows notifications are turned off (Settings → System → Notifications)"
_REG_BASE = "Software\\Microsoft\\Windows\\CurrentVersion"


def _windows_blocks_toasts() -> str:
    """Why Windows would silently drop our toast, or "" when nothing is known to block it.

    ``Show()`` succeeds even when the user switched app notifications off, so without this
    check the channel would report "shown" for a banner that never appeared. Registry reads
    only; anything unreadable counts as "not blocked" (we cannot claim what we cannot see).
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_BASE + "\\PushNotifications") as key:
            if winreg.QueryValueEx(key, "ToastEnabled")[0] == 0:
                return _OFF_HINT
    except (OSError, ImportError):
        pass
    try:
        import winreg

        path = _REG_BASE + "\\Notifications\\Settings\\" + _APP_ID
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            if winreg.QueryValueEx(key, "Enabled")[0] == 0:
                return "notifications for Windows PowerShell are turned off in Windows settings"
    except (OSError, ImportError):
        pass
    return ""


def send_toast(title: str, body: str) -> tuple[bool, str]:
    """Show one desktop toast. Returns ``(ok, detail)``; never raises."""
    if sys.platform != "win32":
        return False, "desktop toast is Windows-only"
    from navig.core.proc_text import console_encoding

    blocked = _windows_blocks_toasts()
    if blocked:
        return False, blocked
    env = dict(os.environ)
    env["NAVIG_TOAST_TITLE"] = (title or "NAVIG")[:120]
    env["NAVIG_TOAST_BODY"] = (body or "")[:300]
    env["NAVIG_TOAST_APPID"] = _APP_ID
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, user text only via env
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-Command", _SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            encoding=console_encoding(),
            errors="replace",
            timeout=_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "toast timed out"
    except OSError as exc:
        return False, f"powershell unavailable: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "toast failed").strip().splitlines()
        return False, (detail[0] if detail else "toast failed")[:200]
    return True, "shown"
