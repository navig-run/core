from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

# Configure logging to stderr so it doesn't break stdout JSON
logging.basicConfig(level=logging.INFO, stream=sys.stderr)


def _load_handler_module():
    handler_path = Path(__file__).resolve().parents[1] / "handler.py"
    spec = importlib.util.spec_from_file_location("navig_memory_handler", handler_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load handler.py from {handler_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("navig_memory_handler", module)
    spec.loader.exec_module(module)
    return module


_HANDLER = _load_handler_module()


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


plugin = NavigPlugin("navig-memory")


@plugin.command("recall")
def recall(query=""):
    logging.info(f"Recalling memory for: {query}")
    if not query:
        return {"results": []}

    store = _HANDLER._get_store()
    return {"results": store.search(query, limit=10)}


@plugin.command("remember")
def remember(content, type="fact"):
    logging.info(f"Remembering: {content} ({type})")
    store = _HANDLER._get_store()
    tags = [type] if type else []
    memory_id = store.store(content, tags=tags)
    return {
        "status": "success",
        "id": memory_id,
        "path": str(_HANDLER._store_path(None)),
    }


if __name__ == "__main__":
    plugin.run()
