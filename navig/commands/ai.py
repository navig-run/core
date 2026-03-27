"""AI Assistant Commands"""

import logging
import subprocess
from typing import Any, Dict, Optional

from navig import console_helper as ch

logger = logging.getLogger(__name__)


def ask_ai(question: str, model: Optional[str], options: Dict[str, Any]):
    """Ask AI about server, get context-aware answers."""
    from navig.ai import AIAssistant
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    ai = AIAssistant(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server. AI needs context.")
        return

    # Gather context
    ch.dim("The Schema's engines are analyzing...\n")

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    context = {
        "server": server_config,
        "directory": "/",
    }

    # Gather running processes (optional, can fail gracefully)
    try:
        result = remote_ops.execute_command(
            "ps aux | grep -E 'nginx|php|mysql' | grep -v grep", server_config
        )
        if result.returncode == 0:
            context["processes"] = result.stdout.strip().split("\n")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Failed to gather process context: {e}")
        # Continue without process info - not critical
    except Exception as e:
        logger.warning(f"Unexpected error gathering process context: {e}")

    # Get AI response
    try:
        response = ai.ask(question, context, model_override=model)

        # Render as markdown using console_helper
        ch.print_markdown(response)

    except ValueError as e:
        ch.error(str(e))
    except Exception as e:
        ch.error(f"AI communication failed: {e}")
