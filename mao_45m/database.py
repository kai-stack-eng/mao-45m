"""Write the telescope state and the subreflector control to PostgreSQL.

Used by subref.control when it is run with ``--database``. The
connection string is read from the environment variable
MAO_DATABASE_URL rather than taken as an argument, because control()
logs all of its arguments and the string contains the password.

Writing must never hold up or stop the control loop, so:

* every statement has a short timeout (STATEMENT_TIMEOUT),
* errors are logged, never raised,
* after the connection is lost, reconnection is attempted only every
  RETRY_WAIT seconds, so a missing database costs one connection
  attempt per RETRY_WAIT rather than one per control period.

psycopg is imported only when a Database is created, so the control
side does not need it unless --database is given.

"""

__all__ = ["Database", "get_database"]


# standard library
import math
import os    
from dataclasses import asdict, fields, replace
from datetime import datetime, timezone
from logging import getLogger
from time import monotonic
from typing import TYPE_CHECKING, Any


# dependencies
from .cosmos import State

if TYPE_CHECKING:
    from .subref.records import Control, Run


# constants
ENV_DSN = "MAO_DATABASE_URL"
LOGGER = getLogger(__name__)
CONNECT_TIMEOUT = 2  # s; libpq does not accept less
STATEMENT_TIMEOUT = 200  # ms; per statement, well inside one control period
RETRY_WAIT = 10.0  # s; wait after losing the connection before reconnecting

# Which State fields go into which table: (the field used as time,
# the other columns). The names must match both the State attributes
# and the database columns (see monitor/init.sql).
STATE_TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "weather": (
        "wind_time",
        ("wind_speed", "wind_direction", "temperature"),
    ),
    "antenna": (
        "time",
        ("elevation", "azimuth", "dX", "dZ1", "dZ2"),
    ),
}




class Database:
    """Database writer for the subreflector control loop.

    Args:
        dsn: PostgreSQL connection string.

    """

    def __init__(self, dsn: str, /) -> None:
        try:
            import psycopg
        except ImportError as error:
            raise ImportError(
                "psycopg is needed to write to the database. "
                "Install it with: uv sync --extra monitor"
            ) from error

        self.psycopg = psycopg
        self.dsn = dsn
        self.conn: Any = None
        self.retry_at = 0.0
        self.last_error: str | None = None
        self.failures = 0
        self.run: "Run | None" = None

        check_state_tables()
        self.connect()

    def write(self, *, state: State, control: "Control") -> bool:
        """Write one control cycle. Never raises; False if nothing was written.

        Args:
            state: Telescope state received in this cycle.
            control: Row of the control table for this cycle
                (see mao_45m.subref.records).

        Returns:
            True if the rows were written (or were duplicates).

        """
        try:
            rows = get_state_rows(state)
            rows["control"] = asdict(control)
        except Exception as error:
            self.report(f"could not prepare rows: {error!r}")
            return False

        if (conn := self.connect()) is None:
            return False

        try:
            with conn.transaction():
                for table, row in rows.items():
                    insert(conn, table, row)
        except self.psycopg.Error as error:
            if conn.broken or conn.closed:
                self.drop(f"lost the database connection: {error!r}")
            else:
                self.report(f"write skipped: {error!r}")

            return False

        self.recovered()
        return True
    
    def start_run(self, run: "Run", /) -> None:
        """Record the start of a control run in the run table. Never raises.

        Args:
            run: Settings of the run (see mao_45m.subref.records).

        """
        self.run = run
        self.write_run()

    def finish_run(self, error: BaseException | None = None, /) -> None:
        """Record the end of the current run. Never raises.

        Args:
            error: Exception that ended the run (None for a normal end).

        """
        if self.run is None:
            return

        self.run = replace(
            self.run,
            finished_at=datetime.now(timezone.utc),
            error=None if error is None else repr(error),
        )
        self.write_run()

    def write_run(self) -> None:
        """Insert or update the row of the current run (never raises)."""
        if self.run is None or (conn := self.connect()) is None:
            return

        try:
            upsert_run(conn, self.run)
        except self.psycopg.Error as error:
            if conn.broken or conn.closed:
                self.drop(f"lost the database connection: {error!r}")
            else:
                self.report(f"run record skipped: {error!r}")

    def connect(self) -> Any:
        """Return a live connection, or None if not available now."""
        if self.conn is not None and not self.conn.closed:
            return self.conn

        if monotonic() < self.retry_at:
            return None

        try:
            self.conn = self.psycopg.connect(
                self.dsn,
                autocommit=True,
                connect_timeout=CONNECT_TIMEOUT,
                options=f"-c statement_timeout={STATEMENT_TIMEOUT}",
            )
        except self.psycopg.Error as error:
            self.drop(f"could not connect to the database: {error!r}")
            return None

        LOGGER.info("Connected to the database.")
        return self.conn

    def drop(self, message: str, /) -> None:
        """Close the connection and wait RETRY_WAIT before reconnecting."""
        self.close()
        self.retry_at = monotonic() + RETRY_WAIT
        self.report(f"{message}; retrying every {RETRY_WAIT:g} s")

    def report(self, message: str, /) -> None:
        """Log a failure: WARNING the first time, DEBUG while it repeats."""
        self.failures += 1

        if message != self.last_error:
            LOGGER.warning(message)
            self.last_error = message
        else:
            LOGGER.debug(message)

    def recovered(self) -> None:
        """Log once that writing works again after failures."""
        if self.failures:
            LOGGER.info(f"Database writes resumed after {self.failures} failures.")

        self.failures = 0
        self.last_error = None

    def close(self) -> None:
        """Close the connection (safe to call more than once)."""
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass

            self.conn = None
            
    def __enter__(self) -> "Database":
        """Enter the context manager."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Record the end of the run and close the connection."""
        self.finish_run(exc_value)
        self.close()


def get_database() -> Database:
    """Create a Database from the environment variable MAO_DATABASE_URL."""
    if not (dsn := os.environ.get(ENV_DSN)):
        raise ValueError(
            f"Set {ENV_DSN} to write to the database, e.g. "
            f"postgresql://nobeyama:<password>@127.0.0.1:5432/nobeyama"
        )

    return Database(dsn)



def get_state_rows(state: State, /) -> dict[str, dict[str, Any]]:
    """Build one row per table in STATE_TABLES."""
    values = asdict(state)
    rows: dict[str, dict[str, Any]] = {}

    for table, (time, columns) in STATE_TABLES.items():
        if values[time] is None:
            continue

        rows[table] = {"time": values[time]}

        for column in columns:
            rows[table][column] = to_real(values[column])

    return rows



def insert(conn: Any, table: str, row: dict[str, Any], /) -> None:
    """Insert one row, ignoring one whose time is already there."""
    columns = ", ".join(row)
    holders = ", ".join(["%s"] * len(row))
    sql = (
        f"INSERT INTO {table} ({columns}) VALUES ({holders}) "
        f"ON CONFLICT (time) DO NOTHING"
    )

    with conn.cursor() as cursor:
        cursor.execute(sql, [to_null(value) for value in row.values()])

def upsert_run(conn: Any, run: "Run", /) -> None:
    """Insert the run row, or update it if it was already inserted."""
    row = asdict(run)
    columns = ", ".join(row)
    holders = ", ".join(["%s"] * len(row))
    updates = ", ".join(f"{key} = EXCLUDED.{key}" for key in row if key != "started_at")
    sql = (
        f"INSERT INTO run ({columns}) VALUES ({holders}) "
        f"ON CONFLICT (started_at) DO UPDATE SET {updates}"
    )

    with conn.cursor() as cursor:
        cursor.execute(sql, list(row.values()))


def to_null(value: Any, /) -> Any:
    """Turn a float NaN into None (NULL in the database); keep the rest."""
    if isinstance(value, float) and math.isnan(value):
        return None

    return value

def to_real(value: Any, /) -> float | None:
    """Convert to float, turning NaN into None (NULL in the database)."""
    value = float(value)
    return None if math.isnan(value) else value


def check_state_tables() -> None:
    """Warn about mismatches between State and STATE_TABLES."""
    stored = {
        name for time, columns in STATE_TABLES.values() for name in (time, *columns)
    }
    names = {field.name for field in fields(State)}

    if missing := stored - names:
        LOGGER.warning(f"STATE_TABLES names that are not State fields: {sorted(missing)}")

    if extra := names - stored:
        LOGGER.warning(f"State fields that are not stored: {sorted(extra)}")