"""AI Assistant Commands"""

import logging
import os
import platform
import subprocess
from typing import Any

from navig import console_helper as ch

logger = logging.getLogger(__name__)


def ask_ai(question: str, model: str | None, options: dict[str, Any]):
    """Ask AI about server, get context-aware answers."""
    from navig.ai import AIAssistant
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    ai = AIAssistant(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        from navig.cli.recovery import require_active_server
        server_name = require_active_server(options, config_manager)

    # Gather context
    ch.dim("The Schema's engines are analyzing...\n")

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    # Always inject client platform so the AI gives OS-correct commands.
    context: dict = {
        "server": server_config,
        "directory": "C:\\" if os.name == "nt" else "/",
        "client_os": f"{platform.system()} {platform.release()}",
        "client_arch": platform.machine(),
    }

    # Gather running processes (optional, can fail gracefully)
    is_local_host = (
        bool(server_config.get("is_local"))
        or str(server_config.get("type", "")).lower() == "local"
        or server_config.get("host", "") in ("localhost", "127.0.0.1", "::1")
    )
    try:
        if os.name == "nt" and is_local_host:
            # Windows local: tasklist filtered for common services — no SSH needed.
            # Do NOT pass text=True here — we decode manually so that non-UTF-8
            # bytes in tasklist output (e.g. OEM code page cp850/cp1252 on
            # French/European Windows) don't crash the subprocess readerthread
            # with UnicodeDecodeError.
            _r = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=10,
            )
            if _r.returncode == 0:
                try:
                    _stdout = _r.stdout.decode("utf-8")
                except UnicodeDecodeError:
                    import locale as _locale
                    _enc = _locale.getpreferredencoding(False) or "cp1252"
                    _stdout = _r.stdout.decode(_enc, errors="replace")
                _relevant = [
                    ln for ln in _stdout.splitlines()
                    if any(k in ln.lower() for k in ("python", "nginx", "mysql", "node", "php", "apache"))
                ]
                context["processes"] = _relevant[:20] or ["(no web services detected)"]
        elif os.name != "nt":
            result = remote_ops.execute_command(
                "ps aux | grep -E 'nginx|php|mysql' | grep -v grep", server_config
            )
            if result.returncode == 0:
                context["processes"] = result.stdout.strip().split("\n")
        # Windows + remote host: skip probe (requires SSH client)
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"Failed to gather process context: {e}")
        # Continue without process info — not critical
    except Exception as e:
        logger.debug(f"Unexpected error gathering process context: {e}")

    # Get AI response
    try:
        response = ai.ask(question, context, model_override=model)

        # Render as markdown using console_helper
        ch.print_markdown(response)

    except ValueError as e:
        ch.error(str(e))
    except Exception as e:
        ch.error(f"AI communication failed: {e}")
