-- Schema for the Nobeyama 45m monitoring database.
-- This file runs automatically the first time the db container starts.
-- It does NOT run again if the volume already exists; drop the volume
-- (docker compose down -v) to re-initialise, which erases all data.

CREATE EXTENSION IF NOT EXISTS timescaledb;


-- ---------------------------------------------------------------------
-- weather: wind and temperature from COSMOS (pullwtaexz).
-- COSMOS updates them about once a minute; time is wind_time, so the
-- primary key keeps one row per update.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather (
    time           TIMESTAMPTZ NOT NULL,
    wind_speed     REAL,
    wind_direction REAL,
    temperature    REAL,
    PRIMARY KEY (time)
);

SELECT create_hypertable('weather', 'time', if_not_exists => TRUE);

COMMENT ON TABLE  weather                IS 'Wind and temperature from COSMOS, about once a minute';
COMMENT ON COLUMN weather.time           IS 'wind_time reported by COSMOS';
COMMENT ON COLUMN weather.wind_speed     IS 'm/s; maximum in the last minute';
COMMENT ON COLUMN weather.wind_direction IS 'degree; at the time of the maximum';
COMMENT ON COLUMN weather.temperature    IS 'degree Celsius';

-- ---------------------------------------------------------------------
-- antenna: antenna angles and subreflector X, Z1, Z2 from COSMOS.
-- time is the time of these values reported by COSMOS.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS antenna (
    time      TIMESTAMPTZ NOT NULL,
    elevation REAL,
    azimuth   REAL,
    dx        REAL,
    dz1       REAL,
    dz2       REAL,
    PRIMARY KEY (time)
);

SELECT create_hypertable('antenna', 'time', if_not_exists => TRUE);

COMMENT ON TABLE  antenna           IS 'Antenna angles and subreflector X, Z1, Z2 from COSMOS';
COMMENT ON COLUMN antenna.time      IS 'Time of the values reported by COSMOS';
COMMENT ON COLUMN antenna.elevation IS 'degree';
COMMENT ON COLUMN antenna.azimuth   IS 'degree';
COMMENT ON COLUMN antenna.dx        IS 'm; subreflector X reported by COSMOS';
COMMENT ON COLUMN antenna.dz1       IS 'm; subreflector Z1 reported by COSMOS';
COMMENT ON COLUMN antenna.dz2       IS 'm; subreflector Z2 reported by COSMOS';

-- ---------------------------------------------------------------------
-- control: one row per subreflector control period (0.5 s), written by
-- `mao-45m subref control --database`.
-- time is the last VDIF sample of the EPL integration (UTC), not the
-- COSMOS time; that is kept in cosmos_time for comparison.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS control (
    time        TIMESTAMPTZ NOT NULL,
    cosmos_time TIMESTAMPTZ,
    cal_time    TIMESTAMPTZ,
    epl_c       REAL,
    epl_t       REAL,
    epl_r       REAL,
    epl_b       REAL,
    epl_l       REAL,
    dx          REAL,
    dz          REAL,
    sent        BOOLEAN,
    PRIMARY KEY (time)
);

SELECT create_hypertable('control', 'time', if_not_exists => TRUE);

COMMENT ON TABLE  control             IS 'Subreflector control, one row per control period';
COMMENT ON COLUMN control.time        IS 'Last VDIF sample of the EPL integration (UTC)';
COMMENT ON COLUMN control.cosmos_time IS 'State time from COSMOS in the same period';
COMMENT ON COLUMN control.cal_time    IS 'Time of the reference spectrum the EPL is relative to';
COMMENT ON COLUMN control.epl_c       IS 'm';
COMMENT ON COLUMN control.epl_t       IS 'm';
COMMENT ON COLUMN control.epl_r       IS 'm';
COMMENT ON COLUMN control.epl_b       IS 'm';
COMMENT ON COLUMN control.epl_l       IS 'm';
COMMENT ON COLUMN control.dx          IS 'm; subreflector X offset computed in this period';
COMMENT ON COLUMN control.dz          IS 'm; subreflector Z offset computed in this period';
COMMENT ON COLUMN control.sent        IS 'false in a dry run: dx, dz were computed but not sent';

-- ---------------------------------------------------------------------
-- run: one row per run of `mao-45m subref control --database`, holding
-- the settings given at the start. Times are in s, lengths in m and
-- frequencies in Hz; each (min, max) argument is split into two columns.
-- started_at / finished_at are the clock of the observation PC (UTC).
-- finished_at stays NULL while the run is going on, or if the process
-- was killed (kill, power loss) before it could write it.
-- error holds the exception that ended the run; NULL means a normal
-- end or Ctrl+C.
-- A few rows per day, so an ordinary table (not a hypertable).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run (
    started_at             TIMESTAMPTZ NOT NULL,
    finished_at            TIMESTAMPTZ,
    error                  TEXT,
    -- EPL estimates
    cal_interval           DOUBLE PRECISION,
    freq_binning           INTEGER,
    freq_range_min         DOUBLE PRECISION,
    freq_range_max         DOUBLE PRECISION,
    integ_per_sample       DOUBLE PRECISION,
    integ_per_epl          DOUBLE PRECISION,
    -- subreflector control
    dry_run                BOOLEAN,
    control_duration       DOUBLE PRECISION,
    control_origin         TIMESTAMPTZ,
    control_period         DOUBLE PRECISION,
    epl_interval_tolerance DOUBLE PRECISION,
    epl_offset_schedule    TEXT,
    controller             TEXT,
    time_constant_dx       DOUBLE PRECISION,
    time_constant_dz       DOUBLE PRECISION,
    integral_gain_dx       DOUBLE PRECISION,
    integral_gain_dz       DOUBLE PRECISION,
    proportional_gain_dx   DOUBLE PRECISION,
    proportional_gain_dz   DOUBLE PRECISION,
    anti_windup_gain_dx    DOUBLE PRECISION,
    anti_windup_gain_dz    DOUBLE PRECISION,
    range_dx_min           DOUBLE PRECISION,
    range_dx_max           DOUBLE PRECISION,
    range_dz_min           DOUBLE PRECISION,
    range_dz_max           DOUBLE PRECISION,
    range_ddx_min          DOUBLE PRECISION,
    range_ddx_max          DOUBLE PRECISION,
    range_ddz_min          DOUBLE PRECISION,
    range_ddz_max          DOUBLE PRECISION,
    PRIMARY KEY (started_at)
);

COMMENT ON TABLE run IS 'Settings of each control run (times in s, lengths in m, frequencies in Hz)';
-- ---------------------------------------------------------------------
-- Adding a field later (e.g. pressure) is a one-liner and completes
-- instantly even on a large table:
--
--   ALTER TABLE cosmos ADD COLUMN pressure REAL;
--
-- Then add the same name to STATE_TABLES in mao_45m/database.py and to
-- State in mao_45m/cosmos.py. Existing rows keep NULL for the new column.
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- Compression. Harmless at this data rate, and it keeps things tidy if
-- the collector is ever left running for months.
-- ---------------------------------------------------------------------
ALTER TABLE weather SET (timescaledb.compress);

ALTER TABLE antenna SET (timescaledb.compress);

ALTER TABLE control SET (timescaledb.compress);

SELECT add_compression_policy('weather', INTERVAL '30 days', if_not_exists => TRUE);

SELECT add_compression_policy('antenna', INTERVAL '30 days', if_not_exists => TRUE);

SELECT add_compression_policy('control', INTERVAL '30 days', if_not_exists => TRUE);
