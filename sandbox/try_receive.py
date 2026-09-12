"""Poll the mock (or real) COSMOS server and print what comes back.

Usage:
    python sandbox/try_receive.py
    python sandbox/try_receive.py --count 60 --interval 0.5
    python sandbox/try_receive.py --host 192.168.1.10 --port 11111
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mao_45m.cosmos import Cosmos  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    print(f"connecting to {args.host}:{args.port}")

    last_time = None
    last_values = None
    n_new = 0
    n_same = 0

    with Cosmos(host=args.host, port=args.port) as cosmos:
        print(
            f"{'#':>3}  {'time (COSMOS)':<26} {'wind':>7} {'temp':>7} {'el':>8}  note"
        )
        print("-" * 70)

        for i in range(args.count):
            state = cosmos.receive_state()
            values = (state.wind_speed, state.temperature, state.elevation)

            if state.time == last_time:
                note = "same time"
                n_same += 1
            elif values == last_values:
                note = "same values"
                n_same += 1
            else:
                note = "new"
                n_new += 1

            print(
                f"{i + 1:>3}  {str(state.time):<26} "
                f"{state.wind_speed:>7.2f} {state.temperature:>7.2f} "
                f"{state.elevation:>8.3f}  {note}"
            )

            last_time = state.time
            last_values = values

            if i + 1 < args.count:
                time.sleep(args.interval)

    print("-" * 70)
    print(f"new records: {n_new},  duplicates: {n_same}")


if __name__ == "__main__":
    main()
