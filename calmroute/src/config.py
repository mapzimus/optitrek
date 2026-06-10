"""Central configuration for Calm Route.

Scoring weights and thresholds live here so they can be tuned without
touching the scoring code. Environment variables override service URLs
(OSRM, database) for dev vs. deploy; the scoring constants are code-level
config by design — changing them should be a reviewed commit, not a
silent env tweak.
"""

import os

# ---------------------------------------------------------------------------
# Geography — Massachusetts only, hardcoded by design.
# (west, south, east, north) in lon/lat. Used for the Nominatim viewbox and
# for rejecting out-of-state geocodes.
MA_BBOX = (-73.508142, 41.187053, -69.858861, 42.886778)

# Planar CRS for metric buffering/measuring: NAD83 / Massachusetts Mainland.
METRIC_CRS = "EPSG:26986"

# ---------------------------------------------------------------------------
# External services
OSRM_URL = os.environ.get("CALMROUTE_OSRM_URL", "http://127.0.0.1:5002")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
NOMINATIM_URL = os.environ.get("CALMROUTE_NOMINATIM_URL", "https://nominatim.openstreetmap.org")
NWS_API_URL = "https://api.weather.gov"
MASS511_GRAPHQL_URL = "https://mass511.com/api/graphql"

# Identify ourselves to public APIs (Nominatim and NWS both require this).
USER_AGENT = "CalmRoute/0.1 (route-safety research tool; contact: mhowe.gis@gmail.com)"

# Max retries on any external API before failing gracefully with a
# partial-result warning.
MAX_RETRIES = 3
HTTP_TIMEOUT_S = 12.0

# ---------------------------------------------------------------------------
# Time budget
DEFAULT_BUFFER_MINUTES = 10
MAX_BUFFER_MINUTES = 20

# ---------------------------------------------------------------------------
# Candidate generation
TARGET_CANDIDATES = 4          # fastest + up to 3 alternatives
MAX_CANDIDATES = 6
PERTURBATION_OFFSETS_M = (1500.0, -1500.0, 2500.0, -2500.0)
PERTURBATION_POSITIONS = (0.35, 0.5, 0.65)   # fraction along the fastest route
DEDUP_OVERLAP_THRESHOLD = 0.80               # routes sharing >80% geometry are dupes
DEDUP_BUFFER_M = 40.0

# ---------------------------------------------------------------------------
# Layer 1 — crash density
CRASH_BUFFER_M = 30.0
CRASH_YEARS_WINDOW = 5
SEVERITY_WEIGHTS = {"fatal": 5.0, "injury": 2.0, "pdo": 1.0}
# Linear recency decay: most recent full year x1.0 down to year-5 x0.4.
RECENCY_WEIGHT_NEWEST = 1.0
RECENCY_WEIGHT_OLDEST = 0.4

# ---------------------------------------------------------------------------
# Layer 2 — winter / ice risk
WINTER_SAMPLE_SPACING_M = 10_000.0
WINTER_MAX_SAMPLE_POINTS = 6      # caps NWS calls per route
FREEZE_TEMP_F = 34.0              # at/below this, ice risk is on the table
HARD_FREEZE_TEMP_F = 32.0
REFREEZE_HOURS = set(range(20, 24)) | set(range(0, 9))   # overnight refreeze window
BRIDGE_BUFFER_M = 30.0
BRIDGE_MULTIPLIER_MAX = 1.5       # bridges freeze first; cap the uplift at +50%
WINTER_ACTIVE_THRESHOLD = 0.05    # below this max risk, the layer is inactive/hidden

# ---------------------------------------------------------------------------
# Layer 3 — construction / incidents (Mass511)
CONSTRUCTION_BUFFER_M = 50.0
MASS511_CACHE_TTL_S = 300         # 5-minute feed cache
EVENT_WEIGHTS = {
    "full_closure": None,         # disqualifies the route entirely (None = no score, drop)
    "lane_closure": 3.0,
    "roadwork": 2.0,
    "incident": 2.5,
    "other": 1.0,
}

# ---------------------------------------------------------------------------
# Composite score
# safety = 100 - (w_crash*crash_norm + w_ice*ice_norm + w_constr*constr_norm)
# When the ice layer is inactive its weight redistributes to crash.
WEIGHTS = {"crash": 0.60, "ice": 0.25, "construction": 0.15}

# ---------------------------------------------------------------------------
# API
RATE_LIMIT = "10/minute"
