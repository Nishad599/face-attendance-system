"""The per-day attendance calendar shown to students.

The endpoint predates approved leave and half-day counting, so it classified
an excused day as "absent" and a morning-only day as a full "present" — both
contradicting the percentage displayed right above the calendar.

The endpoint itself lives in the app module (which needs the face model), so
these tests exercise the classification rule against the same database the
endpoint queries.
"""
from datetime import date, timedelta

import pytest

import reports


def classify(db, sid, course_id, day, today=None):
    """Mirror of the endpoint's per-day decision, in the same order.

    Order matters: a Sunday that is also within an approved leave range is a
    Sunday, not leave — it was never a working day to excuse.
    """
    today = today or date.today()

    if day > today:
        return "future"

    row = db.execute("SELECT joining_date FROM students WHERE id = ?", (sid,)).fetchone()
    if row and row[0]:
        joined = date.fromisoformat(str(row[0])[:10])
        if day < joined:
            return "before_joining"

    if day.weekday() == 6:
        return "sunday"

    hol = db.execute(
        "SELECT 1 FROM holidays WHERE date = ? AND (course_id IS NULL OR course_id = ?)",
        (day.isoformat(), course_id)).fetchone()
    if hol:
        return "holiday"

    on_leave = db.execute(
        "SELECT 1 FROM leave_requests WHERE student_id = ? AND status = 'approved' "
        "AND start_date <= ? AND end_date >= ?",
        (sid, day.isoformat(), day.isoformat())).fetchone()
    if on_leave:
        return "leave"

    sessions = [r[0] for r in db.execute(
        "SELECT session_type FROM attendance WHERE student_id = ? AND date = ?",
        (sid, day.isoformat())).fetchall()]
    if sessions:
        hd = db.execute("SELECT half_day_enabled FROM courses WHERE id = ?",
                        (course_id,)).fetchone()
        credit = reports.day_credit(sessions, bool(hd and hd[0]))
        return "half_day" if credit < 1 else "present"

    return "absent"


@pytest.fixture
def cal(seeded, db):
    """A working day in the seeded window, and the student with no attendance."""
    workday = next(d for d in seeded["work_days"] if d.weekday() != 6)
    return {"sid": seeded["ids"]["C"], "day": workday, **seeded}


class TestDayClassification:

    def test_no_attendance_is_absent(self, cal, db):
        assert classify(db, cal["sid"], 1, cal["day"]) == "absent"

    def test_attendance_is_present(self, cal, db):
        db.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, course_id) "
            "VALUES (?, ?, '09:00:00', 'present', 1)", (cal["sid"], cal["day"].isoformat()))
        db.commit()
        assert classify(db, cal["sid"], 1, cal["day"]) == "present"

    def test_sunday_is_not_an_absence(self, cal, db):
        sunday = next(d for d in
                      (cal["day"] + timedelta(days=i) for i in range(8))
                      if d.weekday() == 6)
        assert classify(db, cal["sid"], 1, sunday, today=sunday) == "sunday"

    def test_holiday_is_not_an_absence(self, cal, db):
        db.execute("INSERT INTO holidays (date, name, type, course_id) "
                   "VALUES (?, 'Diwali', 'holiday', 1)", (cal["day"].isoformat(),))
        db.commit()
        assert classify(db, cal["sid"], 1, cal["day"]) == "holiday"

    def test_approved_leave_shows_as_leave_not_absent(self, cal, db):
        """The bug this fixes: an excused day was drawn red, contradicting the
        percentage shown above the calendar."""
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, ?, ?, 'trip', 'approved')",
            (cal["sid"], cal["day"].isoformat(), cal["day"].isoformat()))
        db.commit()
        assert classify(db, cal["sid"], 1, cal["day"]) == "leave"

    def test_pending_leave_is_still_an_absence(self, cal, db):
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, ?, ?, 'trip', 'pending')",
            (cal["sid"], cal["day"].isoformat(), cal["day"].isoformat()))
        db.commit()
        assert classify(db, cal["sid"], 1, cal["day"]) == "absent"

    def test_morning_only_is_a_half_day_when_enabled(self, cal, db):
        db.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, session_type, course_id) "
            "VALUES (?, ?, '09:00:00', 'present', 'morning_1', 1)",
            (cal["sid"], cal["day"].isoformat()))
        db.execute("UPDATE courses SET half_day_enabled = 1 WHERE id = 1")
        db.commit()
        assert classify(db, cal["sid"], 1, cal["day"]) == "half_day"

    def test_morning_only_is_full_present_when_half_day_is_off(self, cal, db):
        db.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, session_type, course_id) "
            "VALUES (?, ?, '09:00:00', 'present', 'morning_1', 1)",
            (cal["sid"], cal["day"].isoformat()))
        db.commit()
        assert classify(db, cal["sid"], 1, cal["day"]) == "present"

    def test_future_days_are_not_absences(self, cal, db):
        future = date.today() + timedelta(days=3)
        assert classify(db, cal["sid"], 1, future) == "future"

    def test_days_before_joining_are_not_absences(self, cal, db):
        db.execute("UPDATE students SET joining_date = ? WHERE id = ?",
                   (date.today().isoformat(), cal["sid"]))
        db.commit()
        before = date.today() - timedelta(days=5)
        assert classify(db, cal["sid"], 1, before) == "before_joining"

    def test_holiday_takes_precedence_over_leave(self, cal, db):
        """Leave on a day that is already a holiday should read as the
        holiday — the student did not spend any leave on it."""
        db.execute("INSERT INTO holidays (date, name, type, course_id) "
                   "VALUES (?, 'Holi', 'holiday', 1)", (cal["day"].isoformat(),))
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, ?, ?, 'x', 'approved')",
            (cal["sid"], cal["day"].isoformat(), cal["day"].isoformat()))
        db.commit()
        assert classify(db, cal["sid"], 1, cal["day"]) == "holiday"


class TestCalendarMatchesTheReportedRate:
    """The calendar and the percentage above it are computed by different
    code, so they can disagree. These pin the cases that used to."""

    def test_leave_days_are_excluded_from_both(self, cal, db, patched_db):
        sid, day = cal["sid"], cal["day"]
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, ?, ?, 'x', 'approved')",
            (sid, day.isoformat(), day.isoformat()))
        db.commit()

        # calendar says "leave"
        assert classify(db, sid, 1, day) == "leave"
        # and the report drops it from working days rather than counting it absent
        rep = reports.student_monthly_report(sid, day.year, day.month)
        assert day.strftime("%d %b (%a)") not in rep["absent_dates"]
        assert rep["leave_days"] >= 1

    def test_half_day_appears_in_both(self, cal, db, patched_db):
        sid, day = cal["sid"], cal["day"]
        db.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, session_type, course_id) "
            "VALUES (?, ?, '09:00:00', 'present', 'morning_1', 1)", (sid, day.isoformat()))
        db.execute("UPDATE courses SET half_day_enabled = 1 WHERE id = 1")
        db.commit()

        assert classify(db, sid, 1, day) == "half_day"
        rep = reports.student_monthly_report(sid, day.year, day.month)
        assert rep["present_days"] == 0.5
        assert rep["partial_days"] == 1
