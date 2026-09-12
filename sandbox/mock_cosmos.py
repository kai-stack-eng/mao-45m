"""Mock COSMOS server for testing mao_45m.cosmos without the real telescope.

Usage:
    python sandbox/mock_cosmos.py
    python sandbox/mock_cosmos.py --scenario extra
    python sandbox/mock_cosmos.py --scenario shuffled
    python sandbox/mock_cosmos.py --scenario missing
    python sandbox/mock_cosmos.py --scenario silent
"""

import argparse
import math
import socketserver
import threading
import time
from datetime import datetime

HOST = "127.0.0.1"
PORT = 11111
TIME_FORMAT = "%y%m%d%H%M%S.%f"
WEATHER_PERIOD = 60.0  # sec


class Telescope:
    def __init__(self) -> None:
        self.start = time.time()
        self.lock = threading.Lock()

    def weather(self):
        now = time.time()
        slot = int(now // WEATHER_PERIOD)
        phase = slot * WEATHER_PERIOD / 600.0

        wind = 3.0 + 2.0 * math.sin(phase)
        direction = (180.0 + 40.0 * math.sin(phase * 0.7)) % 360.0
        temp = 5.0 + 8.0 * math.sin(phase * 0.3)
        stamp = datetime.fromtimestamp(slot * WEATHER_PERIOD)

        return wind, direction, temp, stamp

    def elevation(self) -> float:
        t = time.time() - self.start
        return 45.0 + 20.0 * math.sin(t / 30.0)


TELESCOPE = Telescope()


def build_response(scenario: str) -> str:
    wind, direction, temp, stamp = TELESCOPE.weather()
    el = TELESCOPE.elevation()
    ts = stamp.strftime(TIME_FORMAT)[:-4]

    if scenario == "extra":
        return (
            f"wind: {wind:.2f}  dir: {direction:.2f}  hum: 65.20  "
            f"tmp: {temp:.2f}  press: 1013.20  el: {el:.2f}  time: {ts}\n"
        )

    if scenario == "shuffled":
        return f"time: {ts}  el: {el:.2f}  tmp: {temp:.2f}  wind: {wind:.2f}\n"

    if scenario == "missing":
        return f"wind: {wind:.2f}  tmp: {temp:.2f}  time: {ts}\n"

    if scenario == "garbage":
        return "error: unknown command\n"

    return f"wind: {wind:.2f}  tmp: {temp:.2f}  el: {el:.2f}  time: {ts}\n"


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        peer = self.client_address
        print(f"[mock] connected from {peer[0]}:{peer[1]}")

        while True:
            line = self.rfile.readline()

            if not line:
                break

            parts = line.decode().strip().split()

            if not parts:
                continue

            cmd = parts[0]

            if cmd == "pullwte":
                if self.server.scenario == "silent":
                    print("[mock] pullwte -> (no response, testing timeout)")
                    time.sleep(60)
                    continue

                resp = build_response(self.server.scenario)
                print(f"[mock] pullwte -> {resp.strip()}")
                self.wfile.write(resp.encode())
                self.wfile.flush()

            elif cmd == "pushsub":
                axis, value = parts[1], parts[2]
                resp = f"set {axis} {value}\n"
                print(f"[mock] pushsub -> {resp.strip()}")
                self.wfile.write(resp.encode())
                self.wfile.flush()

            else:
                print(f"[mock] unknown command: {cmd!r}")

        print(f"[mock] disconnected from {peer[0]}:{peer[1]}")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    scenario = "default"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--scenario",
        default="default",
        choices=["default", "extra", "shuffled", "missing", "garbage", "silent"],
    )
    args = parser.parse_args()

    Server.scenario = args.scenario

    with Server((args.host, args.port), Handler) as server:
        print(f"[mock] listening on {args.host}:{args.port} (scenario={args.scenario})")
        print("[mock] press Ctrl+C to stop")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[mock] stopped")


if __name__ == "__main__":
    main()
