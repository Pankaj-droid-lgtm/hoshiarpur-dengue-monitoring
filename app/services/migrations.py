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


def migrate_operational_refactor(database_path: str | Path) -> Path:
    """Back up SQLite and add the non-destructive operational-refactor schema.

    Legacy GPS columns and records are deliberately left intact for audit/history,
    but the application no longer reads or writes them.
    """
    source_path = Path(database_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = source_path.with_name(f"{source_path.stem}.pre-operational-refactor-{timestamp}{source_path.suffix}")
    source = sqlite3.connect(source_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
        source.execute(
            "CREATE TABLE IF NOT EXISTS sub_centres ("
            "id INTEGER PRIMARY KEY, block_id INTEGER NOT NULL REFERENCES blocks(id), "
            "name VARCHAR(160) NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "CONSTRAINT uq_sub_centre_block_name UNIQUE (block_id, name))"
        )
        house_columns = {row[1] for row in source.execute("PRAGMA table_info(houses)")}
        if "mobile_number" not in house_columns:
            source.execute("ALTER TABLE houses ADD COLUMN mobile_number VARCHAR(20)")
        locality_columns = {row[1] for row in source.execute("PRAGMA table_info(localities)")}
        if "sub_centre_id" not in locality_columns:
            source.execute("ALTER TABLE localities ADD COLUMN sub_centre_id INTEGER REFERENCES sub_centres(id)")
        worker_columns = {row[1] for row in source.execute("PRAGMA table_info(workers)")}
        if "assigned_locality_id" not in worker_columns:
            source.execute("ALTER TABLE workers ADD COLUMN assigned_locality_id INTEGER REFERENCES localities(id)")
        source.commit()
    finally:
        backup.close()
        source.close()
    return backup_path
