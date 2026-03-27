import json
import logging
import os
import sys
from pathlib import Path

from ahk_engine import AHKAdapter

# Configure logging to stderr
logging.basicConfig(level=logging.INFO, stream=sys.stderr)


class NavigPlugin:
    def __init__(self, name):
        self.name = name
        self.commands = {}

    def command(self, name):
        def decorator(func):
            self.commands[name] = func
            return func

        return decorator

    def run(self):
        logging.info(f"Plugin {self.name} starting loop...")
        for line in sys.stdin:
            try:
                line = line.strip()
                if not line:
                    continue

                message = json.loads(line)
                method = message.get("method")
                msg_id = message.get("id")
                params = message.get("params", {})

                if method in self.commands:
                    try:
                        result = self.commands[method](**params)
                        response = {"jsonrpc": "2.0", "result": result, "id": msg_id}
                    except Exception as e:
                        response = {
                            "jsonrpc": "2.0",
                            "error": {"code": -32000, "message": str(e)},
                            "id": msg_id,
                        }
                else:
                    response = {
                        "jsonrpc": "2.0",
                        "error": {"code": -32601, "message": "Method not found"},
                        "id": msg_id,
                    }

                print(json.dumps(response))
                sys.stdout.flush()

            except json.JSONDecodeError:
                logging.error(f"Invalid JSON received: {line}")
            except Exception as e:
                logging.error(f"Unexpected error: {e}")


# Initialize Logic
adapter = AHKAdapter()
# Ensure templates are pointed to plugin dir
plugin_root = Path(__file__).parent.parent
adapter._templates_dir = plugin_root / "templates"
adapter._primitives_path = plugin_root / "templates" / "primitives"
adapter._workflows_path = plugin_root / "templates" / "workflows"

# Initialize Plugin
plugin = NavigPlugin("navig-windows-automation")


@plugin.command("click")
def click(x, y, button="left", clicks=1):
    logging.info(f"Clicking at {x},{y}")
    res = adapter.click(int(x), int(y), button, int(clicks))
    return {"success": res.success, "output": res.stdout, "error": res.stderr}


@plugin.command("type")
def type_text(text):
    logging.info(f"Typing text (len={len(text)})")
    res = adapter.type_text(text)
    return {"success": res.success, "output": res.stdout}


@plugin.command("open-app")
def open_app(target):
    logging.info(f"Opening app: {target}")
    res = adapter.open_app(target)
    return {"success": res.success, "output": res.stdout}


@plugin.command("window-list")
def window_list():
    windows = adapter.get_all_windows()
    return {"windows": [w.to_dict() for w in windows]}


@plugin.command("window-close")
def window_close(title):
    logging.info(f"Closing window: {title}")
    res = adapter.close_window(title)
    return {"success": res.success}


if __name__ == "__main__":
    plugin.run()
