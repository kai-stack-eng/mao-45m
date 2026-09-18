"""Poll COSMOS and write the telescope state into PostgreSQL.

Run it directly for testing:

    python monitor/collector.py --cosmos-host 127.0.0.1

or let docker compose run it (see monitor/docker-compose.yml).

Design notes:

* The poll interval is ours to choose; COSMOS answers only when asked.
  We poll faster than the weather station updates and let the primary
  key on `time` drop the repeats, so the interval does not have to match
  the (still unconfirmed) update period of any single field.
* Each response is written to more than one table. Which column goes
  where is declared once in TABLES below, so adding a field later means
  editing one line here and running one ALTER TABLE.
* Both the COSMOS socket and the database connection are reopened
  automatically after an error, so a network blip does not end the run.
* cosmos.py is loaded straight from its file rather than imported as
  mao_45m.cosmos, because the package __init__ pulls in fire, numpy and
  xarray for the control side. Monitoring needs none of that.
"""

__all__ = ["collect", "main"]


# standard library
import argparse
import importlib.util
import logging
import os
import signal
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any


# dependencies
import psycopg


# constants
COSMOS_PATH = Path(__file__).resolve().parent.parent / "mao_45m" / "cosmos.py"
LOGGER = logging.getLogger("collector")
SOCKET_TIMEOUT = 5.0  # sec; guards against a COSMOS that stops answering
RETRY_WAIT = 5.0  # sec; wait before reconnecting after an error


def load_cosmos():
    """Load mao_45m/cosmos.py without running the package __init__."""
    spec = importlib.util.spec_from_file_location("cosmos", COSMOS_PATH)

    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {COSMOS_PATH}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cosmos_module = load_cosmos()
Cosmos = cosmos_module.Cosmos
State = cosmos_module.State

# Which State field goes into which table. The names must match both the
# State attributes and the database columns. To add wind direction:
#   1. ALTER TABLE weather ADD COLUMN wind_direction REAL;
#   2. add "wind_direction" to the "weather" tuple below
#   3. add the field to State in mao_45m/cosmos.py
TABLES: dict[str, tuple[str, ...]] = {
    "weather": ("wind_speed", "wind_direction", "temperature"),
    "pointing": ("elevation", "azimuth"),
}


class Collector:
    """Owns the COSMOS and database connections and the poll loop."""

    def __init__(self, *, dsn: str, host: str, port: int) -> None:
        self.dsn = dsn
        self.host = host
        self.port = port
        self.cosmos: Cosmos | None = None
        self.conn: psycopg.Connection | None = None
        self.running = True

    # -- connections ---------------------------------------------------

    def connect_cosmos(self) -> Cosmos:
        """Return a live COSMOS client, connecting if necessary."""
        if self.cosmos is None:
            LOGGER.info(f"connecting to COSMOS at {self.host}:{self.port}")
            cosmos = Cosmos(host=self.host, port=self.port)
            # cosmos.py has no timeout of its own; set one here so a
            # silent COSMOS raises instead of blocking forever.
            cosmos.sock.settimeout(SOCKET_TIMEOUT)
            self.cosmos = cosmos
            LOGGER.info("connected to COSMOS")

        return self.cosmos

    def connect_db(self) -> psycopg.Connection:
        """Return a live database connection, connecting if necessary."""
        if self.conn is None or self.conn.closed:
            LOGGER.info("connecting to the database")
            self.conn = psycopg.connect(self.dsn, autocommit=True)
            LOGGER.info("connected to the database")

        return self.conn

    def drop_cosmos(self) -> None:
        """Close the COSMOS socket so the next poll reconnects.

        A timed-out response may still arrive later and would then be
        read as the answer to the following request, shifting every
        reading by one. Reconnecting avoids that.

        """
        if self.cosmos is not None:
            try:
                self.cosmos.sock.close()
            except OSError:
                pass

            self.cosmos = None

    def drop_db(self) -> None:
        """Close the database connection so the next write reconnects."""
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass

            self.conn = None

    def close(self) -> None:
        self.drop_cosmos()
        self.drop_db()

    # -- writing -------------------------------------------------------

    def write(self, state: State) -> dict[str, bool]:
        """Write one state into every table. True means the row was new."""
        conn = self.connect_db()
        values = asdict(state)
        written: dict[str, bool] = {}

        for table, columns in TABLES.items():
            row: dict[str, Any] = {"time": values["time"]}

            for column in columns:
                if column in values:
                    row[column] = values[column]
                else:
                    LOGGER.warning(f"{column!r} is not a field of State")

            written[table] = insert(conn, table, row)

        return written

    # -- loop ----------------------------------------------------------

    def stop(self, *_: Any) -> None:
        LOGGER.info("stopping")
        self.running = False

    def run(self, *, interval: float) -> None:
        """Poll until stopped, reconnecting on error."""
        report_extra_fields()
        next_poll = time.monotonic()

        while self.running:
            try:
                cosmos = self.connect_cosmos()
                state = cosmos.receive_state()
                written = self.write(state)

                if any(written.values()):
                    new = ", ".join(t for t, ok in written.items() if ok)
                    LOGGER.info(f"{state.time.isoformat()} -> {new}")
                else:
                    LOGGER.debug(f"{state.time.isoformat()} -> duplicate")

            except (OSError, TimeoutError) as error:
                LOGGER.warning(f"COSMOS error: {error!r}; reconnecting")
                self.drop_cosmos()
                time.sleep(RETRY_WAIT)
                next_poll = time.monotonic()
                continue

            except psycopg.Error as error:
                LOGGER.warning(f"database error: {error!r}; reconnecting")
                self.drop_db()
                time.sleep(RETRY_WAIT)
                next_poll = time.monotonic()
                continue

            except ValueError as error:
                # unparseable response: log and carry on, the next
                # response is usually fine
                LOGGER.warning(f"{error}")

            next_poll += interval
            time.sleep(max(0.0, next_poll - time.monotonic()))

        self.close()


def insert(conn: psycopg.Connection, table: str, row: dict[str, Any]) -> bool:
    """Insert one row, ignoring duplicates. True if the row was new."""
    columns = ", ".join(row)
    holders = ", ".join(["%s"] * len(row))
    sql = (
        f"INSERT INTO {table} ({columns}) VALUES ({holders}) "
        f"ON CONFLICT (time) DO NOTHING"
    )

    with conn.cursor() as cursor:
        cursor.execute(sql, list(row.values()))
        return cursor.rowcount > 0


def report_extra_fields() -> None:
    """Warn about State fields that no table would store."""
    known = {"time"}

    for columns in TABLES.values():
        known.update(columns)

    fields = set(State.__dataclass_fields__)

    if extra := fields - known:
        LOGGER.warning(
            f"these State fields are not stored anywhere: {sorted(extra)}. "
            f"Add them to TABLES and to the schema if you want them."
        )


def collect(
    *,
    cosmos_host: str = "127.0.0.1",
    cosmos_port: int = 11111,
    dsn: str = "",
    interval: float = 1.0,
) -> None:
    """Run the collector until interrupted."""
    collector = Collector(dsn=dsn, host=cosmos_host, port=cosmos_port)

    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)

    LOGGER.info(f"polling every {interval} s")
    collector.run(interval=interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cosmos-host",
        default=os.environ.get("COSMOS_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--cosmos-port",
        type=int,
        default=int(os.environ.get("COSMOS_PORT", "11111")),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("POLL_INTERVAL", "1.0")),
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "DATABASE_URL",
            "postgresql://nobeyama:nobeyama@127.0.0.1:5432/nobeyama",
        ),
    )
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)-7s %(message)s",
        level=args.log_level.upper(),
    )

    collect(
        cosmos_host=args.cosmos_host,
        cosmos_port=args.cosmos_port,
        dsn=args.dsn,
        interval=args.interval,
    )


if __name__ == "__main__":
    main()