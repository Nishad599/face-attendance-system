"""
db.py — Unified database abstraction for SQLite (dev) and PostgreSQL (production).

Backend selection via DATABASE_URL env var:
  - Not set / empty / "sqlite:///..."  →  SQLite  (default: attendance.db)
  - "postgresql://user:pass@host/db"   →  PostgreSQL

Usage:
    from db import get_connection, is_postgres, table_exists, column_exists

    conn = get_connection()            # tuple rows (index access)
    conn = get_connection(dict_rows=True)  # dict rows (name access)
"""

import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_is_pg = DATABASE_URL.lower().startswith("postgres")


def is_postgres():
    """Return True when the active backend is PostgreSQL."""
    return _is_pg


# ---------------------------------------------------------------------------
# SQL translation helpers
# ---------------------------------------------------------------------------

def _translate_placeholders(sql):
    """Convert SQLite '?' placeholders to PostgreSQL '%s', skipping strings."""
    out = []
    in_str = False
    quote_ch = None
    for ch in sql:
        if in_str:
            out.append(ch)
            if ch == quote_ch:
                in_str = False
        elif ch in ("'", '"'):
            in_str = True
            quote_ch = ch
            out.append(ch)
        elif ch == '?':
            out.append('%s')
        else:
            out.append(ch)
    return ''.join(out)


# ---------------------------------------------------------------------------
# PostgreSQL cursor / connection wrappers
# ---------------------------------------------------------------------------

class _PgCursor:
    """Wraps a psycopg2 cursor so it accepts '?' placeholders."""

    def __init__(self, real_cursor):
        self._cur = real_cursor

    def execute(self, sql, params=None):
        sql = _translate_placeholders(sql)
        if params:
            self._cur.execute(sql, params)
        else:
            self._cur.execute(sql)
        return self

    def executemany(self, sql, seq):
        sql = _translate_placeholders(sql)
        for p in seq:
            self._cur.execute(sql, p)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        """Emulate sqlite3 lastrowid via PostgreSQL lastval()."""
        try:
            saved = self._cur.connection.cursor()
            saved.execute("SELECT lastval()")
            val = saved.fetchone()[0]
            saved.close()
            return val
        except Exception:
            return None

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description


class _PgConnection:
    """Wraps a psycopg2 connection to match sqlite3.Connection surface."""

    def __init__(self, pg_conn, dict_rows=False):
        self._conn = pg_conn
        self._dict_rows = dict_rows
        self.row_factory = None          # compat attribute

    def cursor(self):
        if self._dict_rows or self.row_factory is not None:
            from psycopg2.extras import RealDictCursor
            return _PgCursor(self._conn.cursor(cursor_factory=RealDictCursor))
        return _PgCursor(self._conn.cursor())

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def rollback(self):
        self._conn.rollback()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_connection(db_path='attendance.db', dict_rows=False):
    """Return a database connection for the active backend.

    Args:
        db_path:   SQLite file path (ignored when PostgreSQL is active).
        dict_rows: When True rows are dict-like (column-name access).
    """
    if _is_pg:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        return _PgConnection(conn, dict_rows=dict_rows)
    else:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        if dict_rows:
            conn.row_factory = sqlite3.Row
        return conn


def table_exists(conn, table_name):
    """Check whether a table exists in the database."""
    if _is_pg:
        cur = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ?",
            (table_name,),
        )
        return cur.fetchone() is not None
    else:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return cur.fetchone() is not None


def column_exists(conn, table_name, column_name):
    """Check whether a column exists on a table."""
    if _is_pg:
        cur = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            (table_name, column_name),
        )
        return cur.fetchone() is not None
    else:
        cur = conn.execute(f"PRAGMA table_info({table_name})")
        return any(row[1] == column_name for row in cur.fetchall())


def add_column_safe(conn, table, column, col_type, default=None):
    """Add a column only if it does not already exist."""
    if column_exists(conn, table, column):
        return
    defclause = f" DEFAULT {default}" if default is not None else ""
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{defclause}")


def get_placeholder():
    """Return the parameter placeholder for the active backend."""
    return "%s" if _is_pg else "?"
