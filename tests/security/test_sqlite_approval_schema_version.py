"""Schema-version migration and fail-closed compatibility tests."""
import sqlite3

import pytest

from albra_seed.approvals.sqlite_store import SQLiteApprovalStore


def _user_version(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("PRAGMA user_version").fetchone()[0]


def test_new_approval_store_records_schema_version(tmp_path):
    database = tmp_path / "approvals.sqlite3"
    SQLiteApprovalStore(database)
    assert _user_version(database) == 1


def test_existing_unversioned_approval_store_migrates_idempotently(tmp_path):
    database = tmp_path / "approvals.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE approvals (approval_id TEXT PRIMARY KEY, call_id TEXT, "
            "status TEXT NOT NULL, request_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute("PRAGMA user_version = 0")

    SQLiteApprovalStore(database)
    assert _user_version(database) == 1
    # Reopening the already-migrated store is safe and idempotent.
    SQLiteApprovalStore(database)
    assert _user_version(database) == 1


def test_unknown_approval_schema_version_fails_closed(tmp_path):
    database = tmp_path / "approvals.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(RuntimeError, match="unsupported SQLite approval schema version"):
        SQLiteApprovalStore(database)
