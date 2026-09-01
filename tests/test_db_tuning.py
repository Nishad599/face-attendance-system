"""SQLite concurrency settings.

The default DELETE journal makes a single writer block every reader for the
duration of the write. Attendance arrives in bursts — sixty students marking
at the start of a slot while teachers have the portal open — which is exactly
the shape that produces "database is locked". WAL lets readers proceed while a
write is in flight.
"""
import os
import sqlite3

import pytest

import db as db_module


@pytest.fixture
def tuned(tmp_path):
    return db_module.get_connection(str(tmp_path / "t.db"))


class TestSqlitePragmas:

    def test_wal_is_enabled(self, tuned):
        assert tuned.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    def test_busy_timeout_is_generous(self, tuned):
        """A short timeout turns ordinary contention into a user-visible error."""
        assert tuned.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000

    def test_synchronous_is_normal(self, tuned):
        """NORMAL is the correct companion to WAL: crash-safe without an fsync
        on every commit."""
        assert tuned.execute("PRAGMA synchronous").fetchone()[0] == 1

    def test_foreign_keys_on(self, tuned):
        assert tuned.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_wal_persists_for_other_connections(self, tmp_path):
        """journal_mode is a property of the file, so a second connection sees
        it too — including one opened by a cron script."""
        path = str(tmp_path / "p.db")
        first = db_module.get_connection(path)
        first.execute("CREATE TABLE t (x INTEGER)")
        first.commit()
        first.close()

        plain = sqlite3.connect(path)
        assert plain.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        plain.close()


class TestConcurrentAccess:

    def test_reader_is_not_blocked_by_an_open_write(self, tmp_path):
        """The whole point of WAL. Under the old DELETE journal this read
        raises 'database is locked'."""
        path = str(tmp_path / "c.db")
        setup = db_module.get_connection(path)
        setup.execute("CREATE TABLE t (x INTEGER)")
        setup.execute("INSERT INTO t VALUES (1)")
        setup.commit()
        setup.close()

        writer = db_module.get_connection(path)
        reader = db_module.get_connection(path)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO t VALUES (2)")      # uncommitted
            # Reader sees the pre-write state rather than blocking.
            assert reader.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
            writer.commit()
            assert reader.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
        finally:
            writer.close()
            reader.close()

    def test_tuning_failure_still_returns_a_usable_connection(self, capsys):
        """_tune_sqlite is best-effort — a database that rejects the pragmas
        must still open, or the app cannot start at all.

        sqlite3.Connection.execute is read-only, so stand in a stub object.
        """
        class Refuses:
            def execute(self, *a, **k):
                raise sqlite3.OperationalError("nope")

        stub = Refuses()
        assert db_module._tune_sqlite(stub) is stub
        assert "could not tune SQLite" in capsys.readouterr().out


class TestBusyTimeoutIsConfigurable:

    def test_env_var_is_read(self, monkeypatch, tmp_path):
        """Reload the module so the module-level constant picks the new value."""
        import importlib
        monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "22000")
        reloaded = importlib.reload(db_module)
        try:
            conn = reloaded.get_connection(str(tmp_path / "e.db"))
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 22000
            conn.close()
        finally:
            monkeypatch.delenv("SQLITE_BUSY_TIMEOUT_MS", raising=False)
            importlib.reload(db_module)
