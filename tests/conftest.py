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
-- migrate_phase10: the tenant. Everything else reaches its institute through
-- courses.institute_id.
CREATE TABLE institutes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    contact_email TEXT,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP,
    -- migrate_phase12: what /about renders. Optional; an institute that fills
    -- in nothing gets its name and nothing else.
    tagline TEXT,
    about TEXT,
    address TEXT,
    phone TEXT,
    website TEXT
);
CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP,
    terminal_pin_hash TEXT,
    half_day_enabled INTEGER DEFAULT 0,
    institute_id INTEGER
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
    course_id INTEGER,
    subject_id INTEGER
);
-- migrate_phase9: `attendance` has no UNIQUE constraint of its own, so the
-- INSERT OR IGNORE writers only dedupe while this index exists. COALESCE
-- because a whole-day manual/bulk mark carries no session_type, and both
-- backends treat NULLs as distinct in a unique index.
CREATE UNIQUE INDEX idx_attendance_unique_slot
    ON attendance (student_id, date, COALESCE(session_type, ''));
-- Mirrors production, including `type NOT NULL` — a laxer fixture here let
-- import_calendar.py ship without it and crash on the real database.
-- UNIQUE is (date, course_id) as of migrate_phase8: the old date-only
-- constraint stopped two batches taking the same public holiday.
-- institute_id (migrate_phase10): course_id NULL means "every batch", but only
-- within this institute. Without it one tenant's holiday wipes another's day.
CREATE TABLE holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    course_id INTEGER,
    institute_id INTEGER
);
CREATE UNIQUE INDEX idx_holidays_date_course ON holidays (date, course_id);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    name TEXT, email TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('superadmin','admin','teacher')),
    is_active INTEGER DEFAULT 1,
    must_change_password INTEGER DEFAULT 0,
    created_at TIMESTAMP,
    institute_id INTEGER
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
    course_id INTEGER, created_at TIMESTAMP,
    -- migrate_phase11: a number is the institute the action belongs to, NULL
    -- is platform level and only a superadmin may read it.
    institute_id INTEGER
);
CREATE TABLE session_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER, session_type TEXT NOT NULL,
    start_time TIME NOT NULL, end_time TIME NOT NULL, is_active BOOLEAN DEFAULT 1
);
CREATE TABLE login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identifier TEXT NOT NULL, attempted_at TIMESTAMP NOT NULL
);
CREATE TABLE leave_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL, course_id INTEGER,
    start_date DATE NOT NULL, end_date DATE NOT NULL,
    reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP, reviewed_by INTEGER, reviewed_at TIMESTAMP, review_note TEXT
);
CREATE TABLE alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL,
    rate REAL, sent_at TIMESTAMP
);
CREATE UNIQUE INDEX idx_alert_log_unique ON alert_log (student_id, kind, period);
CREATE TABLE subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL, name TEXT NOT NULL, code TEXT,
    min_attendance REAL DEFAULT 75, start_date DATE, end_date DATE,
    is_active INTEGER DEFAULT 1, created_at TIMESTAMP,
    sequence INTEGER, faculty TEXT, coordinator TEXT, hours TEXT,
    teaching_days INTEGER, exam_date DATE, mid_quiz_date DATE
);
CREATE TABLE academic_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL, title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'event',
    start_date DATE NOT NULL, end_date DATE,
    notes TEXT, coordinator TEXT, created_at TIMESTAMP
);
CREATE UNIQUE INDEX idx_events_unique ON academic_events (course_id, title, start_date);
CREATE TABLE timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL, weekday INTEGER NOT NULL,
    session_type TEXT NOT NULL, subject_id INTEGER, room TEXT, created_at TIMESTAMP
);
CREATE UNIQUE INDEX idx_timetable_slot ON timetable (course_id, weekday, session_type);
-- Needed by clear_all_data, which deletes from each of these in turn.
CREATE TABLE slot_attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL, date DATE NOT NULL, slot_id TEXT NOT NULL,
    time_marked TEXT, detection_confidence REAL,
    is_manual BOOLEAN DEFAULT 0, manual_reason TEXT,
    created_at TEXT, course_id INTEGER,
    UNIQUE(student_id, date, slot_id)
);
CREATE TABLE face_encodings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER, encoding_data BLOB, photo_path TEXT,
    quality_score REAL, created_at TIMESTAMP
);
CREATE TABLE registration_sessions (
    session_id TEXT PRIMARY KEY, student_data TEXT,
    photos_uploaded INTEGER DEFAULT 0, status TEXT DEFAULT 'in_progress',
    created_at TIMESTAMP, expires_at TIMESTAMP
);
CREATE TABLE daily_attendance_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL UNIQUE, total_students INTEGER DEFAULT 0,
    present_morning INTEGER DEFAULT 0, present_afternoon INTEGER DEFAULT 0,
    total_present INTEGER DEFAULT 0, last_updated TEXT
);
CREATE TABLE consent_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL, purpose TEXT NOT NULL DEFAULT 'biometric_attendance',
    policy_version TEXT NOT NULL, granted INTEGER NOT NULL,
    granted_at TIMESTAMP, withdrawn_at TIMESTAMP, ip_address TEXT, user_agent TEXT
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
def two_institutes(db):
    """Two institutes that must never see each other.

    Deliberately adversarial: both run a batch, both declare an institute-wide
    holiday on the SAME date with different names, both have a terminal PIN set,
    and both have a student. Anything that leaks shows up as the other
    institute's name or roll number.

        institute 1  Northgate      course 1  NG-BATCH   student NG001
        institute 2  Southpoint     course 2  SP-BATCH   student SP001
    """
    cur = db.cursor()
    for iid, name, slug in ((1, "Northgate Institute", "northgate"),
                            (2, "Southpoint Academy", "southpoint")):
        cur.execute(
            "INSERT INTO institutes (id, name, slug, is_active, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (iid, name, slug, datetime.now().isoformat()),
        )

    start = (date.today() - timedelta(days=20)).strftime("%Y-%m-%d")
    for cid, iid, name in ((1, 1, "NG-BATCH"), (2, 2, "SP-BATCH")):
        cur.execute(
            "INSERT INTO courses (id, name, start_date, end_date, is_active, "
            "created_at, institute_id, terminal_pin_hash) "
            "VALUES (?, ?, ?, '2027-12-31', 1, ?, ?, 'x')",
            (cid, name, start, datetime.now().isoformat(), iid),
        )

    students = {}
    for roll, cid in (("NG001", 1), ("SP001", 2)):
        cur.execute(
            "INSERT INTO students (student_id, name, email, course_id, status, "
            "joining_date) VALUES (?, ?, ?, ?, 'active', ?)",
            (roll, f"Student {roll}", f"{roll.lower()}@test.local", cid, start),
        )
        students[roll] = cur.lastrowid

    # Staff, one per institute, plus a platform-level superadmin.
    for uname, role, iid in (("ng_admin", "admin", 1),
                             ("sp_admin", "admin", 2),
                             ("owner", "superadmin", None)):
        cur.execute(
            "INSERT INTO users (username, name, password_hash, role, is_active, "
            "institute_id) VALUES (?, ?, 'x', ?, 1, ?)",
            (uname, uname, role, iid),
        )

    # The collision that matters: same date, institute-wide in BOTH.
    clash = (date.today() - timedelta(days=3)).strftime("%Y-%m-%d")
    cur.execute(
        "INSERT INTO holidays (date, name, type, course_id, institute_id) "
        "VALUES (?, 'Northgate Founders Day', 'institute', NULL, 1)", (clash,))
    cur.execute(
        "INSERT INTO holidays (date, name, type, course_id, institute_id) "
        "VALUES (?, 'Southpoint Sports Day', 'institute', NULL, 2)", (clash,))

    # Slot timings differ, so a leak changes whether a student is "in slot".
    for cid, start_t, end_t in ((1, "08:30:00", "09:30:00"),
                                (2, "14:00:00", "15:00:00")):
        cur.execute(
            "INSERT INTO session_configs (course_id, session_type, start_time, "
            "end_time, is_active) VALUES (?, 'morning_1', ?, ?, 1)",
            (cid, start_t, end_t))

    db.commit()
    return {"clash_date": clash, "students": students,
            "courses": {1: 1, 2: 2}, "start": start}


def session_for(role, institute_id=None, user_id=1):
    """A session dict shaped the way SessionManager stores one."""
    return {
        "user_type": role,
        "user_info": {"id": user_id, "role": role, "institute_id": institute_id},
    }


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
