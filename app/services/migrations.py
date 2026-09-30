"""Small, explicit SQLite migrations for the existing production database."""

from datetime import datetime
from pathlib import Path
import sqlite3


def add_household_member_column(database_path: str | Path) -> Path:
    """Back up a SQLite database then add the nullable house member field once."""
    source_path = Path(database_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = source_path.with_name(f"{source_path.stem}.pre-household-member-{timestamp}{source_path.suffix}")
    source = sqlite3.connect(source_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
        columns = {row[1] for row in source.execute("PRAGMA table_info(houses)")}
        if "household_member_name" not in columns:
            source.execute("ALTER TABLE houses ADD COLUMN household_member_name VARCHAR(160)")
            source.commit()
    finally:
        backup.close()
        source.close()
    return backup_path
