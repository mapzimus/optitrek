"""Database connection helper. Reads DATABASE_URL from .env."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
# override=True so the project's .env always wins over any DATABASE_URL the
# user happens to have set in their shell (saw this bite during BRONTOSAURUS
# bring-up — a stale shell var was silently routing queries to a different
# Neon project). .env is the source of truth for this project.
load_dotenv(REPO_ROOT / ".env", override=True)


def get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL not set. Copy .env.example to .env and paste your Neon connection string."
        )
    return dsn


def get_conn() -> psycopg.Connection:
    """Open a new connection. Caller is responsible for closing (use `with`)."""
    return psycopg.connect(get_dsn())


def apply_schema() -> None:
    """Apply src/schema.sql. Idempotent."""
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(schema_path.read_text(encoding="utf-8"))
        conn.commit()
