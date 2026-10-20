# monitor

Records the state of the Nobeyama 45m telescope and the subreflector
control into PostgreSQL (TimescaleDB) and shows it in Grafana.

The records are written by the control loop itself
(`mao-45m subref control --database`). COSMOS accepts only one
connection, so there is no separate collector: records exist only while
the control loop runs with `--database`, one row per control period
(0.5 s by default).

```
correlator ── VDIF (UDP multicast) ──▶ control loop ◀── pullwte / pushsub ──▶ COSMOS
                                            │
                                            ▼  mao_45m/database.py
                          PostgreSQL + TimescaleDB (container, 127.0.0.1:5432)
                                            │
                                            ▼
                              Grafana (container, :3000) ◀── browser on any PC
```

## Setup

Run these from the repository root. On Windows or macOS, read
`podman-compose` as `docker compose`.

1. Install the database driver (an optional dependency):

   ```
   uv sync --extra monitor
   ```

2. Create `monitor/.env` and set the passwords:

   ```
   cp monitor/.env.example monitor/.env
   ```

   Use letters and digits only, and the same password in
   `POSTGRES_PASSWORD` and `MAO_DATABASE_URL`. Both passwords take effect
   only when the database or Grafana volume is first created. If port
   3000 is already taken on this PC, set `GRAFANA_PORT`.

3. Start the database and Grafana:

   ```
   cd monitor
   podman-compose up -d
   podman-compose exec db psql -U nobeyama -c "\dt"
   ```

   The tables `control`, `cosmos` and `run` should be listed.

4. In Grafana (`http://<this PC>:3000`, user `admin`, password
   `GRAFANA_PASSWORD`), add a PostgreSQL data source:

   | setting       | value                          |
   | ------------- | ------------------------------ |
   | Host URL      | `db:5432`                      |
   | Database name | `nobeyama`                     |
   | Username      | `nobeyama`                     |
   | Password      | `POSTGRES_PASSWORD` in `.env`  |
   | TLS/SSL Mode  | `disable`                      |
   | TimescaleDB   | on                             |

   Then import `monitor/grafana/mao-45m-dashboard.json`
   (Dashboards → New → Import) and choose that data source.

## Recording

Add `--database` to the control command and pass `.env` so that the
connection string is found (it is read from the environment rather than
given as an argument, because the control logs all of its arguments):

```
uv run --extra monitor --env-file monitor/.env mao-45m subref control \
    --feed_model ... --feed_origin ... --feed_pattern ... \
    --database
```

The database never stops the control: each statement times out after
200 ms, errors are logged rather than raised (a repeated error only
once), and a lost connection is retried every 10 s.

## Tables

| table     | one row per                 | `time` is                                         |
| --------- | --------------------------- | ------------------------------------------------- |
| `cosmos`  | distinct time from COSMOS   | the observation time reported by COSMOS           |
| `control` | control period              | the last VDIF sample of the EPL integration (UTC) |
| `run`     | start of the control loop   | `started_at`: the clock of this PC (UTC)          |

* `cosmos`: wind speed (m/s), wind direction (deg), temperature (deg C),
  elevation and azimuth (deg).
* `control`: the EPL of each feed (m), always from this period's
  measurement; dX and dZ (m) computed by the controller, which were sent
  to COSMOS only where `sent` is true; and the COSMOS and calibration
  times of the same period.
* `run`: the settings of the run (times in s, lengths in m, frequencies
  in Hz; each (min, max) argument as two columns), the controller, and
  how the run ended. `finished_at` is empty while the run goes on or if
  the process was killed; `error` holds the exception that ended it.

A value that is missing or NaN is stored as NULL. Each row is built from
a dataclass whose field names are the column names: `State` in
`mao_45m/cosmos.py` for `cosmos`, and `Control` and `Run` in
`mao_45m/subref/records.py` for the others.

## Changing the tables

`init.sql` runs only when the database volume is first created. To add
a column to an existing database:

* `cosmos`: `ALTER TABLE cosmos ADD COLUMN <name> REAL;`, add the name to
  `STATE_TABLES` in `mao_45m/database.py`, and add the field to `State`
  in `mao_45m/cosmos.py` (read it with `get_optional` if COSMOS may not
  report it).
* `control` or `run`: `ALTER TABLE ...`, add the field to `Control` or
  `Run` and set it in `from_cycle` or `from_args`.

Add the column to `init.sql` as well, so that new databases get it.
Existing rows keep NULL. If the database holds test data only, it is
simpler to recreate it (see Stopping).

## Replay tests

When a recorded VDIF file is replayed, the `control` times are those of
the recording (in the past), while `cosmos` and `run` are in the
present. Set the dashboard variable `shift` to `on` to move the latest
run's `control` rows to now; leave it `off` at the telescope.
Restarting the VDIF sender rewinds the `control` times, so to find a
test's rows, order them by `cosmos_time` or use the times in `run`.

## At the telescope

Before going (these need the internet): clone the repository, run
`uv sync --extra monitor`, create `.env`, and run
`podman-compose up -d` once so that the images are pulled.

On this PC, open the Grafana port and keep the address fixed, so that
the URL does not change on reboot. On AlmaLinux:

```
sudo firewall-cmd --add-port=3000/tcp --permanent
sudo firewall-cmd --reload
```

Keep the clock synchronised (NTP): `run` uses it.

Things that could not be checked remotely:

* The keys of COSMOS. `dir` (wind direction) and `az` (azimuth) are
  assumed; if one is missing, the control goes on with NaN and logs once
  the keys COSMOS did report. The keys of the subreflector position,
  `subx` and `subz` (in mm), are provisional; see `mao_45m/cosmos.py`.
* Does the COSMOS time advance on every poll, or hold for a minute? Only
  one `cosmos` row is kept per distinct time, so in the latter case the
  elevation is stored once a minute.
* Is the wind direction within 0-360 deg? A 2020 dataset held values
  above 360, which would break averaging.

## Stopping

```
podman-compose down                               # keeps the data
podman-compose down -v                            # deletes the data and the dashboards
podman volume rm nobeyama-monitor_db-data         # after down: deletes the data only
```