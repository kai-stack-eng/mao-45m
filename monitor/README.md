# monitor

Records the telescope state from COSMOS into TimescaleDB and shows it in
Grafana. Runs on the PC at the antenna; the PC in the building only needs
a browser.

```
COSMOS --(TCP pullwte)--> collector --> PostgreSQL/TimescaleDB --> Grafana :3000
```

The commands below are written for Docker. With Podman, read
`docker compose` as `podman-compose` and see [Podman](#podman) for the
differences.

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

Open <http://localhost:3000> and log in as admin with `GRAFANA_PASSWORD`
from `.env`.

### Registering the database in Grafana

The data source is not provisioned automatically. Add it once by hand
under Connections → Data sources → Add new data source → PostgreSQL:

| setting     | value                              |
| ----------- | ---------------------------------- |
| Host        | `db:5432`                          |
| Database    | `nobeyama` (`POSTGRES_DB`)         |
| User        | `nobeyama` (`POSTGRES_USER`)       |
| Password    | `POSTGRES_PASSWORD` from `.env`    |
| TLS/SSL     | disable                            |
| TimescaleDB | on                                 |

The host is `db`, the compose service name, not `localhost`: Grafana
reaches the database over the compose network.

### Dashboards

The data source and the dashboards are kept only in the `grafana-data`
volume. They survive `docker compose down` but not `down -v`. After
changing a dashboard, export it as JSON and commit it under
`grafana/provisioning/dashboards/`. To restore it on a fresh install,
register the data source as above, then use Dashboards → New → Import.

### Checking the tables

```
docker compose exec db psql -U nobeyama -c "SELECT * FROM weather ORDER BY time DESC LIMIT 5"
docker compose exec db psql -U nobeyama -c "SELECT count(*) FROM pointing"
```

## At the telescope

Set the real server in `.env`:

```
COSMOS_HOST=<COSMOS server address>
COSMOS_PORT=<COSMOS port>
```

The port is not the default 11111; use the one the subref control loop
is given with `--cosmos_port`. Keep the real address out of committed
files.

Bring it up the same way. From the other PC, open
`http://<this PC's address>:3000`. Nothing needs installing there.

Open TCP 3000 on the firewall. AlmaLinux uses firewalld:

```
sudo firewall-cmd --add-port=3000/tcp --permanent
sudo firewall-cmd --reload
```

Give this machine a fixed address, otherwise the URL changes on reboot.

If the network stops working after switching profiles, more than one may
be active at once. `nmcli connection show` lists the active ones in
green; take the extra ones down with `nmcli connection down "<name>"`.

## Podman

Everything runs rootless under Podman 5 with podman-compose. No sudo is
needed except for lingering.

- Install podman-compose with `uv tool install podman-compose`.
- To reach a mock on the same machine, set
  `COSMOS_HOST=host.containers.internal` in `.env`.
  `host.docker.internal` is Docker's name.
- Bind mounts need `,Z` because of SELinux. `init.sql` already has it in
  `docker-compose.yml`; without it the database fails to start with
  "Permission denied". Add it to any new bind mount.
- When pulling an image, Podman may ask which registry to use. Choose the
  `docker.io/...` entry.
- The build cache sometimes misses a change to `collector.py` or
  `cosmos.py`. Remove the image and rebuild:

  ```
  podman rmi localhost/nobeyama-monitor_collector:latest
  podman-compose up -d --build
  ```

- Rootless containers stop when the user logs out unless lingering is
  enabled. This needs sudo, once: `sudo loginctl enable-linger mao`.
- After a reboot, check with `podman ps` that the containers are running.

## Tables

| table      | column           | unit           |
| ---------- | ---------------- | -------------- |
| `weather`  | `wind_speed`     | m/s            |
|            | `wind_direction` | degree         |
|            | `temperature`    | degree Celsius |
| `pointing` | `elevation`      | degree         |
|            | `azimuth`        | degree         |

Values are stored as COSMOS reports them, as REAL.

Both tables are keyed on `time`, which is the observation time COSMOS
reports, not the time we asked. Polling faster than the source updates
is therefore safe: repeats collide on the key and are dropped. The two
tables are separate because the elevation may update far more often than
the weather station does, and mixing them would leave one column mostly
null.

## Adding a field

For example, humidity in `weather`:

1. Add the column to the running database, and to `init.sql` so a fresh
   install gets it too:

   ```
   docker compose exec db psql -U nobeyama -c "ALTER TABLE weather ADD COLUMN humidity REAL"
   ```

2. Add `"humidity"` to `TABLES` in `collector.py`.
3. Add the field to `State` in `mao_45m/cosmos.py`, and a line in
   `State.from_cosmos` that reads it from the key COSMOS uses.
4. Rebuild the collector, since the code is copied into the image:

   ```
   docker compose up -d --build collector
   ```

Do step 1 before step 4, otherwise every insert fails on the unknown
column. The insert statement is built from the names in `TABLES`, so
nothing else changes. Existing rows keep NULL for the new column. If a
`State` field is missing from `TABLES`, the collector logs a warning at
startup naming it.

`State.from_cosmos` requires every key it reads. Add a field only once
the server actually sends it; until then every response is rejected as
unparseable.

## Reading the log

- `COSMOS error ...; reconnecting`: the server refused the connection or
  did not answer within 5 s. The collector retries every 5 s.
- `Could not parse the COSMOS response ...; reconnecting`: the response
  was incomplete or lacked a key. The connection is reopened so that the
  unread rest of a cut-off response cannot be taken for the next answer.
  An occasional one is harmless. If it appears on every poll, check the
  key names (see below).
- `database error ...; reconnecting`: usually the database container is
  restarting. The collector retries every 5 s.
- `these State fields are not stored anywhere`: see "Adding a field".

## Things to check on site

The design assumes some things that could not be verified remotely.
Point `sandbox/try_receive.py` at the real server for a minute and watch:

- Does `time` advance on every poll, or hold still for a minute? If it
  advances while the values repeat, it is the response time rather than
  the observation time, and the primary key will not deduplicate.
- Does the elevation change faster than the weather values?
- Wind direction and azimuth are read from the keys `dir` and `az`,
  which are guesses. Record the raw response with `repr()` and correct
  the keys in `State.from_cosmos` if they differ.
- Is wind direction 0-360 or an unwrapped angle? A 2020 dataset held
  values above 360, which would look wrong on a gauge and would break
  averaging.
- Is the response newline-terminated, and how long is it? It is read
  with a single `recv(1024)`, so a longer response, or one split across
  packets, is rejected.
- The subref control loop also polls COSMOS, every 0.5 s. Does the
  server accept the collector's connection while the control loop is
  connected?

## Stopping

```
docker compose down       # keeps the data
docker compose down -v    # deletes the data, the data source and the dashboards
```