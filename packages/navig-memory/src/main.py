import json
import logging
import sys

# Configure logging to stderr so it doesn't break stdout JSON
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


plugin = NavigPlugin("navig-memory")


@plugin.command("recall")
def recall(query=""):
    logging.info(f"Recalling memory for: {query}")
    # Mock search
    return {
        "results": [
            {"content": f"Found memory related to '{query}'", "confidence": 0.95},
            {"content": "Project uses Python 3.11", "confidence": 0.88},
        ]
    }


@plugin.command("remember")
def remember(content, type="fact"):
    logging.info(f"Remembering: {content} ({type})")
    return {
        "status": "success",
        "id": "mem_generated_id",
        "path": ".navig/memory/facts.md",
    }


if __name__ == "__main__":
    plugin.run()
