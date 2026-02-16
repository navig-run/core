import json
import sys

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
        for line in sys.stdin:
            try:
                message = json.loads(line)
                method = message.get("method")
                if method in self.commands:
                    result = self.commands[method](**message.get("params", {}))
                    print(json.dumps({"id": message.get("id"), "result": result}))
                    sys.stdout.flush()
            except Exception as e:
                print(json.dumps({"error": str(e)}))
                sys.stdout.flush()
