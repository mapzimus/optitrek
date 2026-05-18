-- Optitrek schema. One table holds POIs from every source.
-- Designed to grow: Tier 2 adds OSM and Amtrak rows without schema changes.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS pois (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'nps',       -- nps | osm | amtrak
    category TEXT,                             -- national_park, national_monument, museum, ...
    state CHAR(2),                             -- USPS code, assigned by spatial_join.py
    geom GEOMETRY(Point, 4326) NOT NULL,
    tags JSONB DEFAULT '{}'::jsonb,            -- source-specific metadata (e.g. NPS park_code)
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pois_geom     ON pois USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_pois_source   ON pois (source);
CREATE INDEX IF NOT EXISTS idx_pois_state    ON pois (state);
CREATE INDEX IF NOT EXISTS idx_pois_category ON pois (category);

-- Upsert key for NPS rows: source + park_code stored in tags.
-- Partial unique index so it doesn't conflict with future OSM/Amtrak rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pois_nps_park_code
    ON pois ((tags->>'park_code'))
    WHERE source = 'nps';
