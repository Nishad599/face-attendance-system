"""The terminal's live count must be scoped to its own batch.

Regression guard for two bugs that showed together on the kiosk screen:
  1. `total_students` counted every active student in the database, so a
     terminal running the BDA batch displayed the AI batch's headcount.
  2. presence was read from the legacy `slot_attendance` table, which the app
     never writes to, so "Students Present" and "Attendance %" were always 0.
"""

import sqlite3
from datetime import date

import pytest


@pytest.fixture
def two_batches(db):
    """Batch 1: 3 students, 2 present today. Batch 2: 2 students, none present."""
    cur = db.cursor()
    today = date.today().strftime("%Y-%m-%d")
    for cid, name in ((1, "PGCP-AI"), (2, "PGCP-BDA")):
        cur.execute(
            "INSERT INTO courses (id, name, start_date, end_date, is_active, created_at) "
            "VALUES (?, ?, '2026-08-24', '2027-12-31', 1, '2026-08-24T00:00:00')",
            (cid, name),
        )
    ids = {}
    for roll, cid in (("A1", 1), ("A2", 1), ("A3", 1), ("B1", 2), ("B2", 2)):
        cur.execute(
            "INSERT INTO students (student_id, name, email, course_id, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            (roll, roll, f"{roll.lower()}@test.local", cid),
        )
        ids[roll] = cur.lastrowid
    for roll in ("A1", "A2"):
        cur.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, session_type, course_id) "
            "VALUES (?, ?, '09:30:00', 'present', 'morning_1', 1)",
            (ids[roll], today),
        )
    db.commit()
    return ids


def _manager(db_path):
    from attendance_manager import AttendanceSlotManager
    return AttendanceSlotManager(db_path)


def test_count_is_scoped_to_the_batch(db_path, two_batches):
    m = _manager(db_path)

    ai = m.get_live_student_count(course_id=1)
    assert ai["total_students"] == 3
    assert ai["total_present"] == 2
    assert ai["attendance_percentage"] == pytest.approx(66.7)

    bda = m.get_live_student_count(course_id=2)
    assert bda["total_students"] == 2, "BDA terminal must not see AI students"
    assert bda["total_present"] == 0
    assert bda["attendance_percentage"] == 0


def test_unscoped_call_still_covers_every_batch(db_path, two_batches):
    """Admins with more than one batch keep the site-wide view."""
    m = _manager(db_path)
    allb = m.get_live_student_count()
    assert allb["total_students"] == 5
    assert allb["total_present"] == 2


def test_presence_reads_the_attendance_table(db_path, two_batches):
    """`slot_attendance` is legacy and empty; a non-zero count proves we
    are no longer reading it."""
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='slot_attendance'"
    ).fetchone()[0] in (0, 1)
    conn.close()

    m = _manager(db_path)
    assert m.get_live_student_count(course_id=1)["total_present"] == 2
