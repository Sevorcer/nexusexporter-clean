"""One-shot database bootstrap.

Replaces the public `/force_create_tables` HTTP endpoint that previously let
anyone on the internet create tables.

Usage:
    python -m scripts.init_db          # create tables + run inline migrations
    python -m scripts.init_db --reset  # DROP and recreate (destructive!)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

if "DATABASE_URL" not in os.environ:
    sys.exit("DATABASE_URL is not set. Copy .env.example to .env and edit it first.")

from sqlmodel import SQLModel  # noqa: E402

import main  # noqa: E402  (imports trigger create_db() side-effect)


def reset() -> None:
    confirm = input(
        "This will DROP every table in the database. Type 'yes' to continue: "
    ).strip()
    if confirm != "yes":
        sys.exit("Aborted.")
    SQLModel.metadata.drop_all(main.engine)
    SQLModel.metadata.create_all(main.engine)
    print("Database reset complete.")


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Initialise the NexusLeague database.")
    parser.add_argument(
        "--reset", action="store_true", help="Drop and recreate all tables."
    )
    args = parser.parse_args()

    if args.reset:
        reset()
        return

    print("Tables created (or already present). Inline migrations applied.")


if __name__ == "__main__":
    main_cli()
