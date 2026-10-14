"""Filesystem permission regressions for SQLite approval data."""
import os
import stat
import sqlite3

import pytest

from albra_seed.approvals.sqlite_store import SQLiteApprovalStore


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes are not portable")
def test_new_approval_database_is_owner_only(tmp_path):
    database = tmp_path / "private" / "approvals.sqlite3"
    SQLiteApprovalStore(database)
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes are not portable")
def test_existing_approval_database_permissions_are_tightened(tmp_path):
    database = tmp_path / "approvals.sqlite3"
    with sqlite3.connect(database):
        pass
    os.chmod(database, 0o644)

    SQLiteApprovalStore(database)

    assert stat.S_IMODE(database.stat().st_mode) == 0o600


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are not supported")
def test_approval_database_symlink_is_rejected(tmp_path):
    target = tmp_path / "target.sqlite3"
    with sqlite3.connect(target):
        pass
    link = tmp_path / "approvals.sqlite3"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("creating a symlink is not permitted")
    with pytest.raises(ValueError, match="symlink"):
        SQLiteApprovalStore(link)
