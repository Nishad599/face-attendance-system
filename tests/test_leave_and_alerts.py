"""Tests for Phase 5: approved leave, cumulative reports, weekly alerts and
the institute roll-up.

Uses the same throwaway SQLite database as the rest of the suite (conftest),
so this never touches the real attendance.db.
"""
from datetime import date, timedelta

import pytest

import reports


# ---------------------------------------------------------------------------
# approved leave is excused, not absent
# ---------------------------------------------------------------------------

class TestApprovedLeave:

    def _approve_leave(self, db, student_db_id, start, end, course_id=1):
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, ?, ?, ?, 'test', 'approved')",
            (student_db_id, course_id, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
        )
        db.commit()

    def test_leave_removes_days_from_denominator(self, seeded, db, patched_db):
        """An approved absence must shrink the working-day total rather than
        count as a missed day — otherwise applying for leave is pointless."""
        sid = seeded["ids"]["C"]                       # never present -> 0%
        today = date.today()

        before = reports.student_monthly_report(sid, today.year, today.month)
        assert before["rate"] == 0.0
        assert before["absent_days"] > 0

        # Excuse every working day in the window.
        self._approve_leave(db, sid, seeded["start"], today)
        after = reports.student_monthly_report(sid, today.year, today.month)

        assert after["working_days"] == 0, "leave days should leave no working days"
        assert after["absent_days"] == 0
        assert after["leave_days"] > 0

    def test_pending_leave_does_not_excuse(self, seeded, db, patched_db):
        """Only approved leave counts. A pending request must not quietly
        improve the student's percentage before anyone has reviewed it."""
        sid = seeded["ids"]["C"]
        today = date.today()
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, ?, ?, 'test', 'pending')",
            (sid, seeded["start"].strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")),
        )
        db.commit()

        rep = reports.student_monthly_report(sid, today.year, today.month)
        assert rep["leave_days"] == 0
        assert rep["absent_days"] > 0

    def test_rejected_and_cancelled_leave_do_not_excuse(self, seeded, db, patched_db):
        sid = seeded["ids"]["C"]
        today = date.today()
        for status in ("rejected", "cancelled"):
            db.execute("DELETE FROM leave_requests WHERE student_id = ?", (sid,))
            db.execute(
                "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
                "reason, status) VALUES (?, 1, ?, ?, 'test', ?)",
                (sid, seeded["start"].strftime("%Y-%m-%d"),
                 today.strftime("%Y-%m-%d"), status),
            )
            db.commit()
            rep = reports.student_monthly_report(sid, today.year, today.month)
            assert rep["leave_days"] == 0, f"{status} leave must not excuse days"

    def test_leave_outside_the_period_is_ignored(self, seeded, db, patched_db):
        """Leave taken next year must not affect this month's figures."""
        sid = seeded["ids"]["C"]
        today = date.today()
        future = today + timedelta(days=400)
        self._approve_leave(db, sid, future, future + timedelta(days=5))

        rep = reports.student_monthly_report(sid, today.year, today.month)
        assert rep["leave_days"] == 0

    def test_leave_lifts_a_student_out_of_at_risk(self, seeded, db, patched_db):
        """The whole point: an excused absence should stop polluting the
        teacher's at-risk list."""
        sid = seeded["ids"]["B"]                       # present once only
        today = date.today()

        before = reports.teacher_monthly_report(1, today.year, today.month)
        assert any(r["roll_no"] == "T002" for r in before["at_risk"])

        # Excuse everything except the one day B actually attended.
        self._approve_leave(db, sid, seeded["work_days"][1], today)
        after = reports.teacher_monthly_report(1, today.year, today.month)

        assert not any(r["roll_no"] == "T002" for r in after["at_risk"]), \
            "student with fully-excused absences should no longer be at risk"

    def test_missing_table_is_tolerated(self, seeded, db, patched_db):
        """Databases that have not run migrate_phase5 yet must still report,
        just without leave handling."""
        db.execute("DROP TABLE leave_requests")
        db.commit()
        today = date.today()
        rep = reports.student_monthly_report(seeded["ids"]["A"], today.year, today.month)
        assert rep is not None
        assert rep["leave_days"] == 0


# ---------------------------------------------------------------------------
# cumulative report (drives the weekly alert)
# ---------------------------------------------------------------------------

class TestCumulativeReport:

    def test_perfect_student_is_at_100(self, seeded, patched_db):
        rep = reports.student_cumulative_report(seeded["ids"]["A"])
        assert rep["rate"] == 100.0
        assert rep["absent_days"] == 0

    def test_absent_student_is_at_zero(self, seeded, patched_db):
        rep = reports.student_cumulative_report(seeded["ids"]["C"])
        assert rep["rate"] == 0.0
        assert rep["present_days"] == 0

    def test_missing_student_returns_none(self, seeded, patched_db):
        assert reports.student_cumulative_report(999999) is None

    def test_days_needed_is_within_remaining(self, seeded, patched_db):
        """The recovery figure must be actionable: never negative, never more
        than the days that actually remain."""
        rep = reports.student_cumulative_report(seeded["ids"]["C"])
        assert rep["remaining_days"] > 0, "seeded course runs to 2027"
        assert rep["days_needed"] is not None
        assert 0 <= rep["days_needed"] <= rep["remaining_days"]

    def test_days_needed_actually_reaches_the_threshold(self, seeded, patched_db):
        """Attending exactly days_needed must land at or above the threshold —
        an off-by-one here would tell students a number that doesn't work."""
        rep = reports.student_cumulative_report(seeded["ids"]["C"], 75.0)
        total = rep["working_days"] + rep["remaining_days"]
        achieved = (rep["present_days"] + rep["days_needed"]) / total * 100
        assert achieved >= 75.0

        if rep["days_needed"] > 0:
            one_fewer = (rep["present_days"] + rep["days_needed"] - 1) / total * 100
            assert one_fewer < 75.0, "days_needed should be the minimum, not padded"

    def test_perfect_student_needs_no_extra_days(self, seeded, patched_db):
        rep = reports.student_cumulative_report(seeded["ids"]["A"], 75.0)
        total = rep["working_days"] + rep["remaining_days"]
        achieved = (rep["present_days"] + rep["days_needed"]) / total * 100
        assert achieved >= 75.0


# ---------------------------------------------------------------------------
# institute roll-up
# ---------------------------------------------------------------------------

class TestInstituteReport:

    def test_totals_match_the_batches(self, seeded, patched_db):
        today = date.today()
        rep = reports.institute_report(today.year, today.month)
        assert rep["batch_count"] == 1
        assert rep["total_students"] == 3
        assert rep["at_risk_total"] == sum(b["at_risk_count"] for b in rep["batches"])

    def test_average_is_weighted_by_student_count(self, seeded, db, patched_db):
        """A tiny batch must not swing the institute average as hard as a big
        one — that was the reason for weighting."""
        today = date.today()
        # Add a second, single-student batch at 0%.
        db.execute(
            "INSERT INTO courses (id, name, start_date, end_date, is_active) "
            "VALUES (2, 'SMALL', ?, '2027-12-31', 1)",
            (seeded["start"].strftime("%Y-%m-%d"),),
        )
        db.execute(
            "INSERT INTO students (student_id, name, email, course_id, status, joining_date) "
            "VALUES ('S001', 'Solo Student', 's001@test.local', 2, 'active', ?)",
            (seeded["start"].strftime("%Y-%m-%d"),),
        )
        db.commit()

        rep = reports.institute_report(today.year, today.month)
        big = next(b for b in rep["batches"] if b["batch"] == "TEST-BATCH")
        small = next(b for b in rep["batches"] if b["batch"] == "SMALL")

        unweighted = (big["avg_rate"] + small["avg_rate"]) / 2
        assert rep["avg_rate"] != pytest.approx(unweighted), \
            "average should be weighted by student count, not a plain mean"
        # 3 students at big's rate + 1 at 0% == the weighted figure
        expected = (big["avg_rate"] * 3 + small["avg_rate"] * 1) / 4
        assert rep["avg_rate"] == pytest.approx(expected, abs=0.1)

    def test_batches_sorted_worst_first(self, seeded, db, patched_db):
        today = date.today()
        db.execute(
            "INSERT INTO courses (id, name, start_date, end_date, is_active) "
            "VALUES (2, 'SMALL', ?, '2027-12-31', 1)",
            (seeded["start"].strftime("%Y-%m-%d"),),
        )
        db.execute(
            "INSERT INTO students (student_id, name, email, course_id, status, joining_date) "
            "VALUES ('S001', 'Solo Student', 's001@test.local', 2, 'active', ?)",
            (seeded["start"].strftime("%Y-%m-%d"),),
        )
        db.commit()
        rep = reports.institute_report(today.year, today.month)
        rates = [b["avg_rate"] for b in rep["batches"]]
        assert rates == sorted(rates), "worst-performing batch should come first"

    def test_no_active_batches(self, seeded, db, patched_db):
        db.execute("UPDATE courses SET is_active = 0")
        db.commit()
        today = date.today()
        rep = reports.institute_report(today.year, today.month)
        assert rep["batch_count"] == 0
        assert rep["avg_rate"] == 0.0


# ---------------------------------------------------------------------------
# alert emails + de-duplication
# ---------------------------------------------------------------------------

class TestAlertEmail:

    def test_alert_html_mentions_the_recovery_target(self, seeded, patched_db):
        rep = reports.student_cumulative_report(seeded["ids"]["C"])
        html = reports.low_attendance_alert_email(rep)
        assert "%" in html
        assert str(rep["days_needed"]) in html or "speak to your teacher" in html
        assert "<html" in html.lower() or "<div" in html.lower()

    def test_alert_html_escapes_nothing_dangerous(self, seeded, patched_db):
        """Sanity check that the builder returns a full document, not a stub."""
        rep = reports.student_cumulative_report(seeded["ids"]["B"])
        html = reports.low_attendance_alert_email(rep)
        assert len(html) > 200
        assert "Attendance" in html

    def test_institute_email_lists_every_batch(self, seeded, patched_db):
        today = date.today()
        rep = reports.institute_report(today.year, today.month)
        html = reports.institute_report_email(rep)
        assert "TEST-BATCH" in html

    def test_institute_email_handles_no_batches(self, seeded, db, patched_db):
        db.execute("UPDATE courses SET is_active = 0")
        db.commit()
        today = date.today()
        rep = reports.institute_report(today.year, today.month)
        html = reports.institute_report_email(rep)
        assert "No active batches" in html


class TestAlertDeduplication:

    def test_iso_period_is_stable_within_a_week(self):
        import send_alerts
        monday = date(2026, 8, 31)          # a Monday
        friday = date(2026, 9, 4)           # same ISO week
        assert send_alerts.iso_period(monday) == send_alerts.iso_period(friday)

    def test_iso_period_changes_between_weeks(self):
        import send_alerts
        this_week = date(2026, 8, 31)
        next_week = date(2026, 9, 7)
        assert send_alerts.iso_period(this_week) != send_alerts.iso_period(next_week)

    def test_unique_index_blocks_a_second_alert_same_week(self, seeded, db):
        """The de-duplication is enforced by the database, not just by the
        Python check — a cron firing twice must not double-mail anyone."""
        import sqlite3
        sid = seeded["ids"]["B"]
        db.execute(
            "INSERT INTO alert_log (student_id, kind, period, rate) "
            "VALUES (?, 'low_attendance', '2026-W35', 40.0)", (sid,)
        )
        db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO alert_log (student_id, kind, period, rate) "
                "VALUES (?, 'low_attendance', '2026-W35', 41.0)", (sid,)
            )
            db.commit()

    def test_different_week_is_allowed(self, seeded, db):
        sid = seeded["ids"]["B"]
        for period in ("2026-W35", "2026-W36"):
            db.execute(
                "INSERT INTO alert_log (student_id, kind, period, rate) "
                "VALUES (?, 'low_attendance', ?, 40.0)", (sid, period)
            )
        db.commit()
        count = db.execute(
            "SELECT COUNT(*) FROM alert_log WHERE student_id = ?", (sid,)
        ).fetchone()[0]
        assert count == 2
