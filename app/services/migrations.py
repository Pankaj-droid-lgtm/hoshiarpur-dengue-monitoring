"""Small, explicit SQLite migrations for the existing production database."""

from datetime import datetime
from pathlib import Path
import sqlite3


def migrate_field_house_schema(database_path: str | Path) -> Path:
    """Back up SQLite then add non-destructive field-house columns once."""
    source_path = Path(database_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = source_path.with_name(f"{source_path.stem}.pre-field-house-{timestamp}{source_path.suffix}")
    source = sqlite3.connect(source_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
        columns = {row[1] for row in source.execute("PRAGMA table_info(houses)")}
        additions = {
            "household_member_name": "VARCHAR(160)",
            "reference_photo_key": "VARCHAR(255)",
            "reference_photo_content_type": "VARCHAR(100)",
            "registered_by_worker_id": "INTEGER",
        }
        for name, definition in additions.items():
            if name not in columns:
                source.execute(f"ALTER TABLE houses ADD COLUMN {name} {definition}")
        source.commit()
    finally:
        backup.close()
        source.close()
    return backup_path


def add_household_member_column(database_path: str | Path) -> Path:
    """Backward-compatible alias for the field-house migration."""
    return migrate_field_house_schema(database_path)
