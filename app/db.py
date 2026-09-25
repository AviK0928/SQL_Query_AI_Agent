"""Compatibility import: schema reading moved to app/sql/schema.py in Phase 5."""

from app.sql.schema import Database

__all__ = ["Database"]
