# monitor

Records the telescope state from COSMOS into TimescaleDB and shows it in
Grafana. Runs on the PC at the antenna; the PC in the building only needs
a browser.

```
COSMOS --(TCP pullwte)--> collector --> PostgreSQL/TimescaleDB --> Grafana :3000
```

## First run, against the mock

Start the mock COSMOS server in its own terminal:

```
python sandbox/mock_cosmos.py
```

Then, from the repository root:

```
cd monitor
cp .env.example .env      # edit the passwords
docker compose up -d
docker compose logs -f collector
```

Open <http://localhost:3000> (admin / the password in `.env`). The
database is already registered as a data source named `Nobeyama`.

To check what landed in the tables:

```
docker compose exec db psql -U nobeyama -c "SELECT * FROM weather ORDER BY time DESC LIMIT 5"
docker compose exec db psql -U nobeyama -c "SELECT count(*) FROM pointing"
```

## At the telescope

Set the real address in `.env`:

```
COSMOS_HOST=<COSMOS server address>
```

and bring it up the same way. From the other PC, open
`http://<this PC's address>:3000`. Nothing needs installing there.

Open TCP 3000 on the firewall (`sudo ufw allow 3000/tcp`) and give this
machine a fixed address, otherwise the URL changes on reboot.

## Tables

| table      | holds                          |
| ---------- | ------------------------------ |
| `weather`  | wind speed, temperature        |
| `pointing` | elevation                      |

Both are keyed on `time`, which is the observation time COSMOS reports,
not the time we asked. Polling faster than the source updates is
therefore safe: repeats collide on the key and are dropped. The two
tables are separate because the elevation may update far more often than
the weather station does, and mixing them would leave one column mostly
null.

## Adding a field

Three edits, in this order:

1. `ALTER TABLE weather ADD COLUMN wind_direction REAL;`
2. add `"wind_direction"` to `TABLES` in `collector.py`
3. add the field to `State` in `mao_45m/cosmos.py`

The insert statement is built from those names, so nothing else changes.
Existing rows keep NULL for the new column. If step 3 happens without
step 2, the collector logs a warning at startup naming the field.

## Things to check on site

The design assumes some things that could not be verified remotely.
Point `sandbox/try_receive.py` at the real server for a minute and watch:

- does `time` advance on every poll, or hold still for a minute? If it
  advances while the values repeat, it is the response time rather than
  the observation time, and the primary key will not deduplicate.
- does the elevation change faster than the weather values?
- if wind direction has been added, is it 0-360 or an unwrapped angle?
  A 2020 dataset held values above 360, which would look wrong on a
  gauge and would break averaging.
- is the response newline-terminated, and how long is it?

## Stopping

```
docker compose down       # keeps the data
docker compose down -v    # deletes the data and the dashboards
```
