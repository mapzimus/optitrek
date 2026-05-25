"""Live verification: AK POIs surface only when routing_network='us_canada'.

Hits the real Neon DB. Used once after the D5 AK-opt-in change to confirm
the conditional exclusion takes effect end-to-end. Safe to keep in scripts/
as a diagnostic — re-run any time you want to verify AK is still wired up.
"""
from src.config import TripConfig
from src.poi_query import fetch_pois


def main() -> int:
    print("--- routing_network='us' (default) ---")
    cfg_us = TripConfig(name="probe_us")
    pois_us = fetch_pois(cfg_us)
    ak_us = [p for p in pois_us if p["state"] == "AK"]
    print(f"  total POIs: {len(pois_us)}")
    print(f"  AK POIs:    {len(ak_us)}  (expected 0)")

    print()
    print("--- routing_network='us_canada' ---")
    cfg_ca = TripConfig(name="probe_ca", routing_network="us_canada")
    pois_ca = fetch_pois(cfg_ca)
    ak_ca = [p for p in pois_ca if p["state"] == "AK"]
    print(f"  total POIs: {len(pois_ca)}")
    print(f"  AK POIs:    {len(ak_ca)}  (expected > 0)")
    for p in ak_ca[:5]:
        print(f"    #{p['id']:>3}  {p['name']} ({p['category']})")
    if len(ak_ca) > 5:
        print(f"    … and {len(ak_ca) - 5} more")

    return 0 if (len(ak_us) == 0 and len(ak_ca) > 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
