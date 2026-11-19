"""CLI command to idempotently seed the 12 department workflow templates.

Usage (from backend/):
    uv run python scripts/seed_templates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.workflows.template_seed import seed_workflow_templates


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as db, db.begin():
            count = seed_workflow_templates(db)
            print(f"Seeded/upserted {count} workflow templates.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
