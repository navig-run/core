"""tool.py — CLI fallback for iperf3 (spawn-per-call)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err, emit  # noqa: E402

# Import handlers from worker
sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_client, cmd_server  # noqa: E402

TOOL = "iperf3"


def main() -> None:
    parser = argparse.ArgumentParser(prog="navig net iperf3", description="iperf3 network speed test")
    sub = parser.add_subparsers(dest="command", required=True)

    p_client = sub.add_parser("client", help="Run iperf3 client test")
    p_client.add_argument("--host", required=True, help="Server host/IP")
    p_client.add_argument("--port", type=int, default=5201)
    p_client.add_argument("--duration", type=int, default=10)
    p_client.add_argument("--udp", action="store_true", default=False)
    p_client.add_argument("--parallel", type=int, default=1)
    p_client.add_argument("--reverse", action="store_true", default=False)
    p_client.add_argument("--dry-run", action="store_true", default=False)

    p_server = sub.add_parser("server", help="Run iperf3 server")
    p_server.add_argument("--port", type=int, default=5201)
    p_server.add_argument("--bind", default=None)
    p_server.add_argument("--dry-run", action="store_true", default=False)

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    if command == "client":
        result = cmd_client(params)
    elif command == "server":
        result = cmd_server(params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
