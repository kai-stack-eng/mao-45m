-- Schema for the Nobeyama 45m monitoring database.
-- This file runs automatically the first time the db container starts.
-- It does NOT run again if the volume already exists; drop the volume
-- (docker compose down -v) to re-initialise, which erases all data.

CREATE EXTENSION IF NOT EXISTS timescaledb;


-- ---------------------------------------------------------------------
-- weather: values from the meteorological station.
-- The station is believed to update once a minute, so a fast poll loop
-- will see the same row repeatedly; the primary key drops those.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cosmos (
    time        TIMESTAMPTZ NOT NULL,
    wind_speed  REAL,
    wind_direction REAL,
    temperature REAL,
    elevation      REAL,
    azimuth        REAL,
    PRIMARY KEY (time)
);

SELECT create_hypertable('cosmos', 'time', if_not_exists => TRUE);

COMMENT ON TABLE  cosmos             IS 'COSMOS state readings';
COMMENT ON COLUMN cosmos.time        IS 'Observation time reported by COSMOS';
COMMENT ON COLUMN cosmos.wind_speed  IS 'm/s';
COMMENT ON COLUMN cosmos.wind_direction IS 'degree; NULL if COSMOS does not report it';
COMMENT ON COLUMN cosmos.temperature IS 'degree Celsius';
COMMENT ON COLUMN cosmos.elevation      IS 'degree';
COMMENT ON COLUMN cosmos.azimuth        IS 'degree; NULL if COSMOS does not report it';

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
ALTER TABLE cosmos  SET (timescaledb.compress);

ALTER TABLE control SET (timescaledb.compress);

SELECT add_compression_policy('cosmos',  INTERVAL '30 days', if_not_exists => TRUE);

SELECT add_compression_policy('control', INTERVAL '30 days', if_not_exists => TRUE);
