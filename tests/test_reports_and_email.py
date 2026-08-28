"""Monthly report figures, email rendering, and safe SMTP behaviour.

The email tests deliberately never send: they assert that an unconfigured mailer
fails closed with a clear message instead of raising.
"""

from datetime import date, datetime, timedelta

import pytest

import mailer
import reports


class TestMonthBounds:
    def test_last_day_capped_at_today_for_current_month(self):
        today = date.today()
        _first, last = reports.month_bounds(today.year, today.month)
        assert last <= today

    def test_past_month_uses_real_month_end(self):
        first, last = reports.month_bounds(2026, 1)
        assert first == date(2026, 1, 1) and last == date(2026, 1, 31)

    def test_february_leap_year(self):
        _f, last = reports.month_bounds(2024, 2)
        assert last.day == 29

    def test_previous_month_wraps_january(self):
        assert reports.previous_month(date(2026, 1, 15)) == (2025, 12)

    def test_previous_month_normal(self):
        assert reports.previous_month(date(2026, 8, 15)) == (2026, 7)


class TestStudentReport:
    def test_report_for_perfect_attendance(self, patched_db, db, seeded):
        today = date.today()
        rep = reports.student_monthly_report(seeded["ids"]["A"], today.year, today.month)
        assert rep is not None
        assert rep["roll_no"] == "T001"
        assert rep["batch"] == "TEST-BATCH"
        assert rep["present_days"] > 0
        assert rep["rate"] > 0
        assert rep["absent_days"] == rep["working_days"] - rep["present_days"]

    def test_report_for_absent_student(self, patched_db, db, seeded):
        today = date.today()
        rep = reports.student_monthly_report(seeded["ids"]["C"], today.year, today.month)
        assert rep["present_days"] == 0
        assert rep["rate"] == 0.0
        assert len(rep["absent_dates"]) == rep["absent_days"]

    def test_missing_student_returns_none(self, patched_db):
        assert reports.student_monthly_report(999999, 2026, 8) is None

    def test_holiday_excluded_from_working_days(self, patched_db, db, seeded):
        today = date.today()
        before = reports.student_monthly_report(seeded["ids"]["A"], today.year, today.month)
        target = next((d for d in seeded["work_days"] if d.month == today.month), None)
        if target is None:
            pytest.skip("no working day inside the current month to mark as a holiday")
        db.execute("INSERT INTO holidays (date, name, type, course_id) VALUES (?, 'Test', 'holiday', 1)",
                   (target.strftime("%Y-%m-%d"),))
        db.commit()
        after = reports.student_monthly_report(seeded["ids"]["A"], today.year, today.month)
        assert after["working_days"] == before["working_days"] - 1


class TestTeacherReport:
    def test_batch_summary(self, patched_db, db, seeded):
        today = date.today()
        rep = reports.teacher_monthly_report(seeded["course_id"], today.year, today.month)
        assert rep["total_students"] == 3
        assert rep["batch"] == "TEST-BATCH"
        assert 0 <= rep["avg_rate"] <= 100

    def test_at_risk_flags_low_attendance(self, patched_db, db, seeded):
        today = date.today()
        rep = reports.teacher_monthly_report(seeded["course_id"], today.year, today.month)
        at_risk_rolls = [s["roll_no"] for s in rep["at_risk"]]
        assert "T003" in at_risk_rolls          # never present
        assert "T001" not in at_risk_rolls      # perfect attendance

    def test_students_sorted_worst_first(self, patched_db, db, seeded):
        today = date.today()
        rep = reports.teacher_monthly_report(seeded["course_id"], today.year, today.month)
        rates = [s["rate"] for s in rep["students"]]
        assert rates == sorted(rates)

    def test_mid_month_joiner_not_penalised(self, patched_db, db, seeded):
        """Regression: a student who joined mid-month was counted absent for the
        days before they enrolled, wrongly dragging their rate to ~0 and putting
        them on the at-risk list."""
        today = date.today()
        joined = today - timedelta(days=1)
        db.execute(
            "INSERT INTO students (student_id, name, email, course_id, status, joining_date) "
            "VALUES ('LATE1', 'Late Joiner', 'late@test.local', 1, 'active', ?)",
            (joined.strftime("%Y-%m-%d"),))
        sid = db.execute("SELECT id FROM students WHERE student_id='LATE1'").fetchone()[0]
        # present on every working day since joining
        d = joined
        while d <= today:
            if d.weekday() != 6:
                db.execute("INSERT INTO attendance (student_id, date, time_in, status, course_id) "
                           "VALUES (?, ?, '09:00:00', 'present', 1)", (sid, d.strftime("%Y-%m-%d")))
            d += timedelta(days=1)
        db.commit()

        rep = reports.student_monthly_report(sid, today.year, today.month)
        assert rep["rate"] == 100.0, "joiner present every day since joining must be 100%"

        batch = reports.teacher_monthly_report(seeded["course_id"], today.year, today.month)
        assert "LATE1" not in [s["roll_no"] for s in batch["at_risk"]]

    def test_threshold_is_respected(self, patched_db, db, seeded):
        today = date.today()
        none_at_risk = reports.teacher_monthly_report(
            seeded["course_id"], today.year, today.month, at_risk_threshold=0)
        assert none_at_risk["at_risk_count"] == 0


class TestEmailRendering:
    def test_student_email_contains_key_figures(self, patched_db, db, seeded):
        today = date.today()
        rep = reports.student_monthly_report(seeded["ids"]["A"], today.year, today.month)
        html = reports.student_report_email(rep)
        assert str(rep["rate"]) in html
        assert "TEST-BATCH" in html
        assert "<div" in html

    def test_low_attendance_warning_appears(self, patched_db, db, seeded):
        today = date.today()
        rep = reports.student_monthly_report(seeded["ids"]["C"], today.year, today.month)
        html = reports.student_report_email(rep)
        assert "below 75%" in html

    def test_no_warning_for_good_attendance(self, patched_db, db, seeded):
        today = date.today()
        rep = reports.student_monthly_report(seeded["ids"]["A"], today.year, today.month)
        if rep["rate"] >= 75:
            assert "below 75%" not in reports.student_report_email(rep)

    def test_welcome_email_has_credentials(self):
        html = reports.welcome_email("Ravi Kumar", "T001", "20010514", "TEST-BATCH")
        assert "T001" in html and "20010514" in html and "TEST-BATCH" in html

    def test_otp_email_shows_code_and_expiry(self):
        html = reports.otp_email("Ravi", "483920", minutes=15)
        assert "483920" in html and "15" in html

    def test_teacher_email_lists_at_risk_student(self, patched_db, db, seeded):
        today = date.today()
        rep = reports.teacher_monthly_report(seeded["course_id"], today.year, today.month)
        html = reports.teacher_report_email(rep)
        assert "Charlie Student" in html


class TestMailerSafety:
    def test_unconfigured_returns_error_not_raise(self, monkeypatch):
        monkeypatch.setattr(mailer, "is_configured", lambda: False)
        ok, msg = mailer.send_email("someone@example.com", "Subject", "<b>body</b>")
        assert ok is False and "not configured" in msg.lower()

    @pytest.mark.parametrize("bad", ["", None, "not-an-email"])
    def test_invalid_recipient_rejected(self, bad):
        ok, msg = mailer.send_email(bad, "Subject", "<b>body</b>")
        assert ok is False and "recipient" in msg.lower()

    def test_never_sends_during_tests(self, monkeypatch):
        """Guard: a misconfigured test must not open an SMTP connection."""
        def explode(*a, **k):
            raise AssertionError("SMTP connection attempted during tests!")
        monkeypatch.setattr(mailer.smtplib, "SMTP", explode)
        monkeypatch.setattr(mailer, "is_configured", lambda: False)
        ok, _ = mailer.send_email("a@b.com", "s", "<b>b</b>")
        assert ok is False

    def test_render_email_escapes_into_shell(self):
        html = mailer.render_email("Title", "Intro", mailer.stat_table([("K", "V")]))
        assert "Title" in html and "Intro" in html and "K" in html and "V" in html

    def test_html_to_text_strips_tags(self):
        text = mailer._html_to_text("<p>Hello</p><br><b>World</b>")
        assert "Hello" in text and "World" in text and "<" not in text


class TestSendReportsHelpers:
    @pytest.mark.parametrize("value,expected", [
        ("a@b.com", True), ("student.name@institute.ac.in", True),
        ("", False), (None, False), ("nope", False), ("a@b", False),
    ])
    def test_email_validation(self, value, expected):
        import send_reports
        assert send_reports.looks_like_email(value) is expected

    def test_month_parsing(self):
        import send_reports
        assert send_reports.parse_month("2026-08") == (2026, 8)

    def test_month_parsing_defaults_to_previous(self):
        import send_reports
        assert send_reports.parse_month(None) == reports.previous_month()

    @pytest.mark.parametrize("bad", ["2026-13", "garbage", "2026"])
    def test_bad_month_exits(self, bad):
        import send_reports
        with pytest.raises(SystemExit):
            send_reports.parse_month(bad)
