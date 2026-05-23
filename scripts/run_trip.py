"""CLI runner for Tier 2 config-driven trips. See spec §5 (Library and CLI conventions).

Usage:
    python -m scripts.run_trip trips/southwest_parks.yaml
    python -m scripts.run_trip trips/southwest_parks.yaml --dry-run
    python -m scripts.run_trip trips/southwest_parks.yaml --time-limit-override 30
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from src.config import TripConfigError, load_config
from src.trip import run_trip


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run_trip",
        description="Run a Tier 2 Optitrek trip from a YAML config",
    )
    parser.add_argument("yaml_path", type=Path,
                        help="Path to the trip YAML config file")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Override the output directory (default: ./output)")
    parser.add_argument("--time-limit-override", type=int, default=None,
                        metavar="SECONDS",
                        help="Override config.time_limit_seconds (useful for quick smoke tests)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print resolved depot + candidate count, then exit (no matrix/solve/render)")
    parser.add_argument("--verbose", action="store_true",
                        help="Echo SQL + OSRM URLs as they run")

    args = parser.parse_args()

    try:
        config = load_config(args.yaml_path)
    except (TripConfigError, FileNotFoundError) as e:
        print(f"ERROR loading {args.yaml_path}: {e}", file=sys.stderr)
        return 1

    if args.time_limit_override is not None:
        config = replace(config, time_limit_seconds=args.time_limit_override)

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    try:
        out_path = run_trip(
            config=config,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
        )
    except TripConfigError as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if not args.dry_run:
        print(f"\n=> Output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
