"""Extract bridge=yes car-road ways from the MA PBF into PostGIS.

Bridges freeze before adjacent roadway, so the winter layer multiplies
ice risk by bridge exposure. Uses the same PBF the OSRM engine is built
from, so bridge geometry matches the routable network.

    python etl/load_bridges.py --pbf data/massachusetts-latest.osm.pbf
"""

import argparse
import os

import osmium
import psycopg

CAR_HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "motorway_link", "trunk_link", "primary_link",
    "secondary_link", "tertiary_link",
}

DDL = """
CREATE TABLE IF NOT EXISTS bridges (
    way_id   bigint PRIMARY KEY,
    highway  text,
    name     text,
    geom     geometry(LineString, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS bridges_geom_gist ON bridges USING gist (geom);
"""

UPSERT = """
INSERT INTO bridges (way_id, highway, name, geom)
VALUES (%(way_id)s, %(highway)s, %(name)s, ST_GeomFromText(%(wkt)s, 4326))
ON CONFLICT (way_id) DO UPDATE SET
    highway = EXCLUDED.highway, name = EXCLUDED.name, geom = EXCLUDED.geom
"""


class BridgeHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def way(self, w):
        if w.tags.get("bridge") != "yes":
            return
        if w.tags.get("highway") not in CAR_HIGHWAYS:
            return
        try:
            coords = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        wkt = "LINESTRING(" + ", ".join(f"{lon:.7f} {lat:.7f}" for lon, lat in coords) + ")"
        self.rows.append({
            "way_id": w.id,
            "highway": w.tags.get("highway"),
            "name": w.tags.get("name"),
            "wkt": wkt,
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", required=True)
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("Set DATABASE_URL or pass --dsn")

    handler = BridgeHandler()
    handler.apply_file(args.pbf, locations=True, idx="flex_mem")
    print(f"Found {len(handler.rows)} bridge ways.")

    with psycopg.connect(args.dsn) as conn:
        conn.execute(DDL)
        with conn.cursor() as cur:
            cur.executemany(UPSERT, handler.rows)
        conn.commit()
    print("Loaded into bridges table.")


if __name__ == "__main__":
    main()
