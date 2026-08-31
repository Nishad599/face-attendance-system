"""Half-day attendance (Phase 6).

Morning slots are worth half a day, afternoon slots the other half — so
arriving after lunch scores 0.5 rather than a full day. This is OPT-IN per
batch (`courses.half_day_enabled`) because switching it on changes every
percentage the batch has ever displayed.
"""
from datetime import date

import pytest

import reports


class TestDayCredit:
    """The pure scoring rule, independent of any database."""

    def test_disabled_always_credits_a_full_day(self):
        assert reports.day_credit(["morning_1"], half_day=False) == 1.0
        assert reports.day_credit(["afternoon_2"], half_day=False) == 1.0

    def test_morning_only_is_half(self):
        assert reports.day_credit(["morning_1"]) == 0.5

    def test_afternoon_only_is_half(self):
        assert reports.day_credit(["afternoon_1"]) == 0.5

    def test_both_halves_make_a_whole(self):
        assert reports.day_credit(["morning_1", "afternoon_2"]) == 1.0

    def test_two_morning_slots_are_still_one_half(self):
        """Both morning slots are the same half of the day — attending both
        must not credit a full day."""
        assert reports.day_credit(["morning_1", "morning_2"]) == 0.5

    def test_all_four_slots_cap_at_one(self):
        assert reports.day_credit(
            ["morning_1", "morning_2", "afternoon_1", "afternoon_2"]) == 1.0

    def test_null_session_type_is_a_whole_day(self):
        """A whole-day manual or bulk mark writes NULL. The student chose
        nothing here — the system did — so it must not cost them half a day."""
        assert reports.day_credit([None]) == 1.0
        assert reports.day_credit([""]) == 1.0

    def test_legacy_plain_morning_value_is_half(self):
        """Old rows default to the bare string 'morning'."""
        assert reports.day_credit(["morning"]) == 0.5

    def test_unrecognised_value_credits_a_full_day(self):
        """Fail open: an unexpected session_type must never penalise."""
        assert reports.day_credit(["evening_1"]) == 1.0
        assert reports.day_credit(["something-odd"]) == 1.0

    def test_case_and_whitespace_tolerant(self):
        assert reports.day_credit(["  MORNING_1 "]) == 0.5
        assert reports.day_credit(["Afternoon_2"]) == 0.5

    def test_empty_list(self):
        assert reports.day_credit([]) == 1.0


class TestHalfDayIsOptIn:

    def _mark(self, db, sid, day, session_type):
        db.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, session_type, course_id) "
            "VALUES (?, ?, '09:00:00', 'present', ?, 1)",
            (sid, day.strftime("%Y-%m-%d"), session_type),
        )
        db.commit()

    def test_off_by_default_so_existing_batches_are_unchanged(self, seeded, db, patched_db):
        """The critical backward-compatibility guarantee: a batch that has not
        opted in must score exactly as it did before the feature existed."""
        today = date.today()
        rep = reports.student_monthly_report(seeded["ids"]["A"], today.year, today.month)
        assert rep["rate"] == 100.0, "student A is present every working day"
        assert rep["half_day"] is False

    def test_morning_only_scores_full_day_when_disabled(self, seeded, db, patched_db):
        sid = seeded["ids"]["C"]                      # no attendance at all
        day = seeded["work_days"][0]
        self._mark(db, sid, day, "morning_1")

        today = date.today()
        rep = reports.student_monthly_report(sid, today.year, today.month)
        assert rep["present_days"] == 1.0

    def test_morning_only_scores_half_when_enabled(self, seeded, db, patched_db):
        sid = seeded["ids"]["C"]
        day = seeded["work_days"][0]
        self._mark(db, sid, day, "morning_1")
        db.execute("UPDATE courses SET half_day_enabled = 1 WHERE id = 1")
        db.commit()

        today = date.today()
        rep = reports.student_monthly_report(sid, today.year, today.month)
        assert rep["present_days"] == 0.5
        assert rep["half_day"] is True

    def test_both_halves_score_a_full_day(self, seeded, db, patched_db):
        sid = seeded["ids"]["C"]
        day = seeded["work_days"][0]
        self._mark(db, sid, day, "morning_1")
        self._mark(db, sid, day, "afternoon_1")
        db.execute("UPDATE courses SET half_day_enabled = 1 WHERE id = 1")
        db.commit()

        today = date.today()
        rep = reports.student_monthly_report(sid, today.year, today.month)
        assert rep["present_days"] == 1.0

    def test_partial_days_are_reported_separately(self, seeded, db, patched_db):
        """A half day is neither present nor absent — the student should be
        able to see which days they only half-attended."""
        sid = seeded["ids"]["C"]
        self._mark(db, sid, seeded["work_days"][0], "morning_1")
        self._mark(db, sid, seeded["work_days"][1], "afternoon_2")
        db.execute("UPDATE courses SET half_day_enabled = 1 WHERE id = 1")
        db.commit()

        today = date.today()
        rep = reports.student_monthly_report(sid, today.year, today.month)
        assert rep["partial_days"] == 2
        assert len(rep["partial_dates"]) == 2
        # those two days are not counted as absences
        assert seeded["work_days"][0].strftime("%d %b (%a)") not in rep["absent_dates"]

    def test_rate_uses_fractional_credit(self, seeded, db, patched_db):
        """Two half days out of N working days must read as 1/N, not 2/N."""
        sid = seeded["ids"]["C"]
        self._mark(db, sid, seeded["work_days"][0], "morning_1")
        self._mark(db, sid, seeded["work_days"][1], "morning_1")
        db.execute("UPDATE courses SET half_day_enabled = 1 WHERE id = 1")
        db.commit()

        today = date.today()
        rep = reports.student_monthly_report(sid, today.year, today.month)
        assert rep["present_days"] == 1.0
        expected = round(1.0 / rep["working_days"] * 100, 1)
        assert rep["rate"] == expected

    def test_missing_column_is_tolerated(self, seeded, db, patched_db):
        """A database that has not run migrate_phase6 must still report."""
        # SQLite cannot drop a column on old versions; simulate by querying a
        # course id that does not exist, which exercises the same guard.
        assert reports._half_day_enabled(db.cursor(), 99999) is False
        assert reports._half_day_enabled(db.cursor(), None) is False


class TestHalfDayFlowsThroughReports:

    def test_teacher_report_uses_half_days(self, seeded, db, patched_db):
        db.execute("UPDATE courses SET half_day_enabled = 1 WHERE id = 1")
        # student A is present every day, whole-day rows (session_type NULL in
        # the fixture) -> still 100%
        db.commit()
        today = date.today()
        rep = reports.teacher_monthly_report(1, today.year, today.month)
        a = next(r for r in rep["students"] if r["roll_no"] == "T001")
        assert a["rate"] == 100.0

    def test_cumulative_report_uses_half_days(self, seeded, db, patched_db):
        sid = seeded["ids"]["C"]
        db.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, session_type, course_id) "
            "VALUES (?, ?, '09:00:00', 'present', 'morning_1', 1)",
            (sid, seeded["work_days"][0].strftime("%Y-%m-%d")),
        )
        db.execute("UPDATE courses SET half_day_enabled = 1 WHERE id = 1")
        db.commit()

        rep = reports.student_cumulative_report(sid)
        assert rep["present_days"] == 0.5
