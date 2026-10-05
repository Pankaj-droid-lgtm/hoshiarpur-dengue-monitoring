"""Add read-performance indexes to the configured SQLite database safely."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3


INDEXES = {
    "house_visits": {
        "ix_house_visits_visited_at": "visited_at",
        "ix_house_visits_house_id": "house_id",
        "ix_house_visits_worker_id": "worker_id",
        "ix_house_visits_positive_containers": "positive_containers",
    },
    "houses": {"ix_houses_locality_id": "locality_id"},
    "localities": {"ix_localities_block_id": "block_id"},
    "deployments": {
        "ix_deployments_block_id": "block_id",
        "ix_deployments_locality_id": "locality_id",
        "ix_deployments_worker_id": "worker_id",
        "ix_deployments_deployment_date": "deployment_date",
    },
    "reinspection_tasks": {
        "ix_reinspection_tasks_status": "status",
        "ix_reinspection_tasks_due_date": "due_date",
    },
}


def database_path(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    url = os.environ.get("DENGUE_DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return Path(url.removeprefix("sqlite:///")).expanduser().resolve()
    if url:
        raise ValueError("This script supports SQLite URLs only.")
    return (Path(__file__).resolve().parents[1] / "instance" / "dengue.db").resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Add safe SQLite monitoring indexes.")
    parser.add_argument("--database", help="SQLite database path; defaults to DENGUE_DATABASE_URL or instance/dengue.db")
    args = parser.parse_args()
    path = database_path(args.database)
    if not path.exists():
        print(f"Database not found: {path}")
        return 1
    try:
        connection = sqlite3.connect(path, timeout=5)
        try:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            print(f"SQLite journal mode: {mode}")
            for table, indexes in INDEXES.items():
                existing = {row[1] for row in connection.execute(f"PRAGMA index_list({table})")}
                for name, columns in indexes.items():
                    if name in existing:
                        print(f"Already exists: {name}")
                    else:
                        connection.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
                        print(f"Created: {name}")
            connection.commit()
        finally:
            connection.close()
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            print("Database is locked; no index changes were applied. Retry when Gunicorn has released the SQLite write lock.")
            return 0
        print(f"SQLite error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
