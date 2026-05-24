"""Optitrek web frontend (Tier 2 Phase 6).

FastAPI + Jinja2 + htmx + Tailwind CDN. Serves a structured form for
TripConfig, runs the same `src.trip.run_trip` pipeline the CLI uses,
and returns the rendered Folium map for preview/download.

Stage 1 scope: local dev only, synchronous solve. Stages 2 (async +
email) and 3 (deploy) layer on top without changing the form or the
solve pipeline.
"""
