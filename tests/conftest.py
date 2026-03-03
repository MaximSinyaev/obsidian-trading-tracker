"""Shared fixtures for trading tracker tests."""

import sqlite3
from pathlib import Path

import pytest

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema" / "001_initial.sql"


@pytest.fixture
def conn():
    """In-memory SQLite database with schema applied."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA_FILE.read_text())
    yield connection
    connection.close()
