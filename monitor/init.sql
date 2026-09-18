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
CREATE TABLE IF NOT EXISTS weather (
    time        TIMESTAMPTZ NOT NULL,
    wind_speed  REAL,
    wind_direction REAL,
    temperature REAL,
    PRIMARY KEY (time)
);

SELECT create_hypertable('weather', 'time', if_not_exists => TRUE);

COMMENT ON TABLE  weather             IS 'Meteorological station readings';
COMMENT ON COLUMN weather.time        IS 'Observation time reported by COSMOS';
COMMENT ON COLUMN weather.wind_speed  IS 'm/s';
COMMENT ON COLUMN weather.temperature IS 'degree Celsius';


-- ---------------------------------------------------------------------
-- pointing: where the antenna is looking.
-- Kept separate from weather because it may update far more often.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pointing (
    time      TIMESTAMPTZ NOT NULL,
    elevation REAL,
    azimuth   REAL,
    PRIMARY KEY (time)
);

SELECT create_hypertable('pointing', 'time', if_not_exists => TRUE);

COMMENT ON TABLE  pointing           IS 'Antenna pointing state';
COMMENT ON COLUMN pointing.time      IS 'Observation time reported by COSMOS';
COMMENT ON COLUMN pointing.elevation IS 'degree';


-- ---------------------------------------------------------------------
-- Adding a field later (e.g. wind direction, azimuth) is a one-liner and
-- completes instantly even on a large table:
--
--   ALTER TABLE weather  ADD COLUMN wind_direction REAL;
--   ALTER TABLE pointing ADD COLUMN azimuth        REAL;
--
-- Then add the same name to TABLES in collector.py and to State in
-- mao_45m/cosmos.py. Existing rows keep NULL for the new column.
-- ---------------------------------------------------------------------


-- ---------------------------------------------------------------------
-- Compression. Harmless at this data rate, and it keeps things tidy if
-- the collector is ever left running for months.
-- ---------------------------------------------------------------------
ALTER TABLE weather  SET (timescaledb.compress);
ALTER TABLE pointing SET (timescaledb.compress);

SELECT add_compression_policy('weather',  INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_compression_policy('pointing', INTERVAL '30 days', if_not_exists => TRUE);
