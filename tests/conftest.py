"""
Shared pytest fixtures.

IMPORTANT: every test runs against a throwaway SQLite database in a temp
directory. Nothing here ever opens the real attendance.db, so the suite is safe
to run on the production machine.
"""

import os
import sys
import sqlite3
from datetime import date, datetime, timedelta

import pytest

# Make the project root importable regardless of where pytest is invoked from
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


SCHEMA = """
CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    face_encoding BLOB,
    registration_date TIMESTAMP,
    status TEXT DEFAULT 'active',
    photo_count INTEGER DEFAULT 0,
    verification_score REAL DEFAULT 0.0,
    joining_date DATE,
    course_id INTEGER,
    password_hash TEXT,
    must_change_password INTEGER DEFAULT 1,
    dob TEXT
);
CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP,
    terminal_pin_hash TEXT
);
CREATE TABLE attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    date DATE NOT NULL,
    time_in TIME,
    status TEXT DEFAULT 'present',
    created_at TIMESTAMP,
    manual_reason TEXT,
    is_manual BOOLEAN DEFAULT 0,
    session_type TEXT,
    is_late BOOLEAN DEFAULT 0,
    course_id INTEGER
);
CREATE TABLE holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE, name TEXT, type TEXT,
    created_at TIMESTAMP, course_id INTEGER
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    name TEXT, email TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    must_change_password INTEGER DEFAULT 0,
    created_at TIMESTAMP
);
CREATE TABLE teacher_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, course_id INTEGER NOT NULL,
    created_at TIMESTAMP, UNIQUE(user_id, course_id)
);
CREATE TABLE sessions (
    token TEXT PRIMARY KEY, user_type TEXT NOT NULL, user_info TEXT NOT NULL,
    created_at TIMESTAMP, expires_at TIMESTAMP NOT NULL, last_activity TIMESTAMP
);
CREATE TABLE grievances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL, course_id INTEGER,
    date DATE NOT NULL, session_type TEXT, reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP, reviewed_by INTEGER, reviewed_at TIMESTAMP, review_note TEXT
);
CREATE TABLE email_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_email TEXT NOT NULL, subject TEXT, kind TEXT,
    status TEXT, error TEXT, sent_at TIMESTAMP
);
CREATE TABLE password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    principal_type TEXT NOT NULL, principal_id INTEGER NOT NULL,
    otp_hash TEXT NOT NULL, expires_at TIMESTAMP NOT NULL,
    used INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0, created_at TIMESTAMP
);
CREATE TABLE face_registration_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL, course_id INTEGER, session_id TEXT,
    photo_count INTEGER DEFAULT 0, encoding_blob BLOB,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP, reviewed_by INTEGER, reviewed_at TIMESTAMP, review_note TEXT
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_type TEXT, actor_id INTEGER, actor_name TEXT,
    action TEXT NOT NULL, target TEXT, details TEXT,
    course_id INTEGER, created_at TIMESTAMP
);
CREATE TABLE session_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER, session_type TEXT NOT NULL,
    start_time TIME NOT NULL, end_time TIME NOT NULL, is_active BOOLEAN DEFAULT 1
);
"""


@pytest.fixture
def db_path(tmp_path):
    """Path to a fresh, fully-schema'd throwaway database."""
    path = str(tmp_path / "test_attendance.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db(db_path):
    """Open connection to the throwaway database."""
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


@pytest.fixture
def seeded(db):
    """A batch with three students and some attendance.

    student A: present on every working day in the window (100%)
    student B: present on one day only (low attendance)
    student C: never present (0%)
    """
    from auth_utils import hash_password

    cur = db.cursor()
    start = date.today() - timedelta(days=13)
    cur.execute(
        "INSERT INTO courses (id, name, start_date, end_date, is_active, created_at) "
        "VALUES (1, 'TEST-BATCH', ?, '2027-12-31', 1, ?)",
        (start.strftime("%Y-%m-%d"), datetime.now().isoformat()),
    )
    ids = {}
    for key, roll, name, dob in (("A", "T001", "Alpha Student", "2001-05-14"),
                                 ("B", "T002", "Bravo Student", "2002-11-02"),
                                 ("C", "T003", "Charlie Student", "")):
        cur.execute(
            "INSERT INTO students (student_id, name, email, course_id, status, "
            "joining_date, dob, password_hash, must_change_password) "
            "VALUES (?, ?, ?, 1, 'active', ?, ?, ?, 1)",
            (roll, name, f"{roll.lower()}@test.local", start.strftime("%Y-%m-%d"),
             dob or None, hash_password(dob if dob else "changeme123")),
        )
        ids[key] = cur.lastrowid

    # working days in the window (Mon-Sat)
    work = []
    d = start
    while d <= date.today():
        if d.weekday() != 6:
            work.append(d)
        d += timedelta(days=1)

    for d in work:                                  # A present every day
        cur.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, course_id) "
            "VALUES (?, ?, '09:00:00', 'present', 1)",
            (ids["A"], d.strftime("%Y-%m-%d")),
        )
    if work:                                        # B present once
        cur.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, course_id) "
            "VALUES (?, ?, '09:00:00', 'present', 1)",
            (ids["B"], work[0].strftime("%Y-%m-%d")),
        )
    db.commit()
    return {"ids": ids, "course_id": 1, "start": start, "work_days": work}


@pytest.fixture
def patched_db(monkeypatch, db_path):
    """Point reports.py's get_connection at the throwaway database."""
    import db as db_module
    import reports

    def _fake_get_connection(path=None, dict_rows=False):
        conn = sqlite3.connect(db_path)
        if dict_rows:
            conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(reports, "get_connection", _fake_get_connection)
    monkeypatch.setattr(db_module, "get_connection", _fake_get_connection)
    return db_path
