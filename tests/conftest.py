"""Shared fixtures for trading tracker tests."""

import sqlite3
from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


@pytest.fixture
def conn():
    """In-memory SQLite database with all migrations applied."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in sorted(SCHEMA_DIR.glob("*.sql")):
        connection.executescript(migration.read_text())
    yield connection
    connection.close()
