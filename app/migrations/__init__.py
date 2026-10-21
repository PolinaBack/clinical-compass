"""Numbered, checksummed, transactional additions to the existing schema."""

import hashlib
import sqlite3
from pathlib import Path


def apply_migrations(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations(
                version TEXT PRIMARY KEY, checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL)"""
        )
        for path in sorted(Path(__file__).parent.glob("[0-9]*.sql")):
            contents = path.read_bytes()
            checksum = hashlib.sha256(contents).hexdigest()
            previous = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version=?", (path.name,)
            ).fetchone()
            if previous:
                if previous[0] != checksum:
                    raise RuntimeError(f"Applied migration changed: {path.name}")
                continue
            statement = ""
            for line in contents.decode("utf-8").splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    connection.execute(statement)
                    statement = ""
            if statement.strip():
                raise RuntimeError(f"Incomplete migration: {path.name}")
            connection.execute(
                "INSERT INTO schema_migrations VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (path.name, checksum),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
