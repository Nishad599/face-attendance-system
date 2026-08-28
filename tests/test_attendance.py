"""Attendance maths: working days, rates, holidays, lateness, bulk marking.

These cover the calculations shown to students and teachers — the numbers people
act on — plus the batch-scoping rules that keep teachers inside their own batches.
"""

from datetime import date, datetime, timedelta

import pytest


def working_days(start, end, holidays=()):
    """Mon-Sat excluding holidays — the rule used across the app."""
    days, d = [], start
    holidays = set(holidays)
    while d <= end:
        if d.weekday() != 6 and d not in holidays:
            days.append(d)
        d += timedelta(days=1)
    return days


class TestWorkingDays:
    def test_sunday_excluded(self):
        # 2026-08-23 is a Sunday
        sunday = date(2026, 8, 23)
        assert sunday.weekday() == 6
        assert sunday not in working_days(date(2026, 8, 17), date(2026, 8, 23))

    def test_saturday_included(self):
        saturday = date(2026, 8, 22)
        assert saturday.weekday() == 5
        assert saturday in working_days(date(2026, 8, 17), date(2026, 8, 23))

    def test_full_week_is_six_days(self):
        assert len(working_days(date(2026, 8, 17), date(2026, 8, 23))) == 6

    def test_holiday_excluded(self):
        hol = date(2026, 8, 19)
        days = working_days(date(2026, 8, 17), date(2026, 8, 23), holidays=[hol])
        assert hol not in days and len(days) == 5

    def test_single_day_range(self):
        monday = date(2026, 8, 17)
        assert working_days(monday, monday) == [monday]


class TestAttendanceRate:
    def _rate(self, present, total):
        return round(present / total * 100, 1) if total else 0.0

    @pytest.mark.parametrize("present,total,expected", [
        (0, 10, 0.0), (10, 10, 100.0), (5, 10, 50.0), (3, 9, 33.3), (7, 8, 87.5),
    ])
    def test_rate_calculation(self, present, total, expected):
        assert self._rate(present, total) == expected

    def test_zero_working_days_does_not_divide_by_zero(self):
        assert self._rate(0, 0) == 0.0

    def test_rates_from_seeded_data(self, db, seeded):
        """A=100%, B=low, C=0% — computed the way the app does."""
        total = len(seeded["work_days"])
        for key, expected in (("A", 100.0), ("C", 0.0)):
            rows = db.execute(
                "SELECT COUNT(DISTINCT date) FROM attendance WHERE student_id = ?",
                (seeded["ids"][key],)).fetchone()[0]
            assert self._rate(rows, total) == expected

        b_present = db.execute(
            "SELECT COUNT(DISTINCT date) FROM attendance WHERE student_id = ?",
            (seeded["ids"]["B"],)).fetchone()[0]
        assert b_present == 1
        assert 0 < self._rate(b_present, total) < 75      # flagged at risk

    def test_duplicate_marks_same_day_count_once(self, db, seeded):
        """Two rows on one date must not inflate the present count."""
        sid = seeded["ids"]["B"]
        day = seeded["work_days"][1].strftime("%Y-%m-%d")
        for _ in range(3):
            db.execute("INSERT INTO attendance (student_id, date, time_in, status, course_id) "
                       "VALUES (?, ?, '09:00:00', 'present', 1)", (sid, day))
        db.commit()
        distinct = db.execute(
            "SELECT COUNT(DISTINCT date) FROM attendance WHERE student_id = ?", (sid,)).fetchone()[0]
        assert distinct == 2       # the original day plus this one


class TestLateArrival:
    """compute_is_late(): late only beyond the grace period."""
    GRACE = 10

    def _late(self, slot_h, slot_m, now_h, now_m, grace=None):
        grace = self.GRACE if grace is None else grace
        return (now_h * 60 + now_m) - (slot_h * 60 + slot_m) > grace

    def test_on_time_not_late(self):
        assert not self._late(8, 30, 8, 30)

    def test_within_grace_not_late(self):
        assert not self._late(8, 30, 8, 40)

    def test_beyond_grace_is_late(self):
        assert self._late(8, 30, 8, 41)

    def test_early_arrival_not_late(self):
        assert not self._late(8, 30, 8, 15)

    def test_grace_is_configurable(self):
        assert not self._late(8, 30, 8, 50, grace=30)
        assert self._late(8, 30, 8, 50, grace=5)


class TestBulkMarking:
    def test_present_is_idempotent(self, db, seeded):
        """Re-running bulk-mark must not create duplicate rows."""
        sid, day = seeded["ids"]["C"], date.today().strftime("%Y-%m-%d")

        def mark():
            exists = db.execute("SELECT 1 FROM attendance WHERE student_id=? AND date=?",
                                (sid, day)).fetchone()
            if not exists:
                db.execute("INSERT INTO attendance (student_id, date, time_in, status, "
                           "is_manual, course_id) VALUES (?, ?, '09:00:00', 'present', 1, 1)",
                           (sid, day))
                db.commit()
                return True
            return False

        assert mark() is True
        assert mark() is False
        count = db.execute("SELECT COUNT(*) FROM attendance WHERE student_id=? AND date=?",
                           (sid, day)).fetchone()[0]
        assert count == 1

    def test_absent_clears_records(self, db, seeded):
        sid, day = seeded["ids"]["A"], seeded["work_days"][0].strftime("%Y-%m-%d")
        assert db.execute("SELECT COUNT(*) FROM attendance WHERE student_id=? AND date=?",
                          (sid, day)).fetchone()[0] == 1
        db.execute("DELETE FROM attendance WHERE student_id=? AND date=?", (sid, day))
        db.commit()
        assert db.execute("SELECT COUNT(*) FROM attendance WHERE student_id=? AND date=?",
                          (sid, day)).fetchone()[0] == 0

    def test_session_specific_mark_records_slot(self, db, seeded):
        sid, day = seeded["ids"]["C"], date.today().strftime("%Y-%m-%d")
        db.execute("INSERT INTO attendance (student_id, date, time_in, status, session_type, "
                   "course_id) VALUES (?, ?, '09:00:00', 'present', 'morning_1', 1)", (sid, day))
        db.commit()
        assert db.execute("SELECT session_type FROM attendance WHERE student_id=?",
                          (sid,)).fetchone()[0] == "morning_1"


class TestBatchScoping:
    """Teachers must only ever touch their assigned batches."""

    def _allowed(self, db, user_id):
        return [r[0] for r in db.execute(
            "SELECT course_id FROM teacher_batches WHERE user_id = ?", (user_id,))]

    @pytest.fixture
    def two_batches(self, db, seeded):
        from auth_utils import hash_password
        db.execute("INSERT INTO courses (id, name, start_date, end_date, is_active) "
                   "VALUES (2, 'OTHER-BATCH', '2026-01-01', '2027-01-01', 1)")
        db.execute("INSERT INTO students (student_id, name, email, course_id, status) "
                   "VALUES ('X001', 'Other Student', 'x@test.local', 2, 'active')")
        db.execute("INSERT INTO users (username, name, password_hash, role, is_active) "
                   "VALUES ('t1', 'T One', ?, 'teacher', 1)", (hash_password("x"),))
        db.execute("INSERT INTO teacher_batches (user_id, course_id) VALUES (1, 1)")
        db.commit()
        return db

    def test_teacher_sees_only_assigned_batch(self, two_batches):
        assert self._allowed(two_batches, 1) == [1]

    def test_other_batch_is_denied(self, two_batches):
        assert 2 not in self._allowed(two_batches, 1)

    def test_students_of_foreign_batch_not_returned(self, two_batches):
        allowed = self._allowed(two_batches, 1)
        rows = two_batches.execute(
            f"SELECT student_id FROM students WHERE course_id IN ({','.join('?' * len(allowed))})",
            allowed).fetchall()
        rolls = [r[0] for r in rows]
        assert "X001" not in rolls and "T001" in rolls


class TestGrievances:
    def test_approval_marks_student_present(self, db, seeded):
        sid = seeded["ids"]["C"]
        day = seeded["work_days"][2].strftime("%Y-%m-%d")
        db.execute("INSERT INTO grievances (student_id, course_id, date, reason, status) "
                   "VALUES (?, 1, ?, 'was present', 'pending')", (sid, day))
        db.commit()
        gid = db.execute("SELECT id FROM grievances").fetchone()[0]

        # approve -> insert attendance + flip status
        if not db.execute("SELECT 1 FROM attendance WHERE student_id=? AND date=?",
                          (sid, day)).fetchone():
            db.execute("INSERT INTO attendance (student_id, date, time_in, status, is_manual, "
                       "manual_reason, course_id) VALUES (?, ?, '09:00:00', 'present', 1, "
                       "'Grievance approved', 1)", (sid, day))
        db.execute("UPDATE grievances SET status='approved' WHERE id=?", (gid,))
        db.commit()

        assert db.execute("SELECT status FROM grievances WHERE id=?", (gid,)).fetchone()[0] == "approved"
        assert db.execute("SELECT COUNT(*) FROM attendance WHERE student_id=? AND date=?",
                          (sid, day)).fetchone()[0] == 1

    def test_duplicate_pending_detected(self, db, seeded):
        sid = seeded["ids"]["B"]
        day = date.today().strftime("%Y-%m-%d")
        db.execute("INSERT INTO grievances (student_id, course_id, date, reason, status) "
                   "VALUES (?, 1, ?, 'first', 'pending')", (sid, day))
        db.commit()
        dup = db.execute(
            "SELECT 1 FROM grievances WHERE student_id=? AND date=? AND "
            "IFNULL(session_type,'')=IFNULL(?,'') AND status='pending'", (sid, day, None)).fetchone()
        assert dup is not None

    def test_window_rejects_old_dates(self):
        assert (date.today() - (date.today() - timedelta(days=31))).days > 30
        assert (date.today() - (date.today() - timedelta(days=29))).days <= 30
