"""tool.py — CLI fallback for speedtest (spawn-per-call)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_run  # noqa: E402

TOOL = "speedtest"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navig net speedtest",
        description="Dual-method internet speed test: speedtest-cli (Ookla) + iperf3.",
    )
    parser.add_argument(
        "--iperf3-server",
        dest="iperf3_server",
        default=None,
        help="iperf3 server hostname or IP (required unless --skip-iperf3)",
    )
    parser.add_argument("--iperf3-port", dest="iperf3_port", type=int, default=5201)
    parser.add_argument(
        "--skip-speedtest", dest="skip_speedtest", action="store_true", default=False
    )
    parser.add_argument(
        "--skip-iperf3", dest="skip_iperf3", action="store_true", default=False
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        default=False,
        help="Suppress raw output banners (JSON summary only)",
    )

    args = parser.parse_args()

    if not args.skip_iperf3 and not args.iperf3_server:
        result = err(
            TOOL,
            "run",
            ["--iperf3-server required unless --skip-iperf3 is set"],
            code=1,
        )
        print(json.dumps(result, indent=2))
        sys.exit(1)

    result = cmd_run(
        {
            "iperf3_server": args.iperf3_server,
            "iperf3_port": args.iperf3_port,
            "skip_speedtest": args.skip_speedtest,
            "skip_iperf3": args.skip_iperf3,
            "silent": args.silent,
        }
    )

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
