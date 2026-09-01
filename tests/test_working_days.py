"""Attendance must never be recorded on a Sunday or a holiday.

Before this, only one path checked holidays — and it ignored Sundays entirely
and matched any batch's holiday rather than the student's own. The camera,
bulk marking, the manual endpoint, grievance approval and the terminal
fallback all had no guard at all.
"""
from datetime import date, timedelta

import pytest

import working_days as wd


# 2026-08-31 is a Monday; 2026-09-06 is a Sunday.
MONDAY = date(2026, 8, 31)
SATURDAY = date(2026, 9, 5)
SUNDAY = date(2026, 9, 6)


class TestAsDate:

    def test_accepts_a_date(self):
        assert wd.as_date(MONDAY) == MONDAY

    def test_accepts_an_iso_string(self):
        assert wd.as_date("2026-08-31") == MONDAY

    def test_accepts_a_timestamp_string(self):
        assert wd.as_date("2026-08-31 09:30:00") == MONDAY

    def test_rejects_rubbish(self):
        assert wd.as_date("not a date") is None
        assert wd.as_date(None) is None
        assert wd.as_date("") is None


class TestDayName:

    def test_names_the_weekday(self):
        assert wd.day_name(MONDAY) == "Monday"
        assert wd.day_name(SUNDAY) == "Sunday"

    def test_works_from_a_string(self):
        assert wd.day_name("2026-09-05") == "Saturday"

    def test_blank_for_a_bad_value(self):
        assert wd.day_name("rubbish") == ""
        assert wd.day_name(None) == ""


class TestWorkingDayRule:

    def test_monday_to_saturday_are_working_days(self, seeded, db):
        d = MONDAY
        for _ in range(6):                       # Mon..Sat
            ok, why = wd.check(db, 1, d)
            assert ok, f"{d:%A} should be a working day ({why})"
            d += timedelta(days=1)

    def test_saturday_is_a_working_day(self, seeded, db):
        """Explicit: this institute works Saturdays."""
        assert wd.is_working_day(db, 1, SATURDAY)

    def test_sunday_is_not(self, seeded, db):
        ok, why = wd.check(db, 1, SUNDAY)
        assert not ok
        assert "Sunday" in why

    def test_batch_holiday_blocks_that_batch(self, seeded, db):
        db.execute("INSERT INTO holidays (date, name, type, course_id) VALUES (?, 'Diwali', 'holiday', 1)",
                   (MONDAY.strftime("%Y-%m-%d"),))
        db.commit()
        ok, why = wd.check(db, 1, MONDAY)
        assert not ok
        assert "Diwali" in why

    def test_a_batch_holiday_does_not_block_another_batch(self, seeded, db):
        """The old check matched any holiday row regardless of batch, so one
        batch's day off silently blocked everyone."""
        db.execute("INSERT INTO holidays (date, name, type, course_id) VALUES (?, 'BDA only', 'holiday', 1)",
                   (MONDAY.strftime("%Y-%m-%d"),))
        db.commit()
        assert wd.is_working_day(db, 2, MONDAY), "batch 2 should be unaffected"

    def test_institute_wide_holiday_blocks_every_batch(self, seeded, db):
        db.execute("INSERT INTO holidays (date, name, type, course_id) VALUES (?, 'Republic Day', 'holiday', NULL)",
                   (MONDAY.strftime("%Y-%m-%d"),))
        db.commit()
        assert not wd.is_working_day(db, 1, MONDAY)
        assert not wd.is_working_day(db, 2, MONDAY)

    def test_invalid_date_is_rejected(self, seeded, db):
        ok, why = wd.check(db, 1, "not-a-date")
        assert not ok
        assert "valid date" in why

    def test_reason_is_human_readable(self, seeded, db):
        """The message goes straight back to the user, so it must read well."""
        _ok, why = wd.check(db, 1, SUNDAY)
        assert why.startswith("06 Sep 2026")
        assert "cannot be marked" in why

    def test_holiday_with_a_blank_name_still_blocks(self, seeded, db):
        """`holidays.name` is NOT NULL in production, but it can be empty —
        the day must still block, with a generic label."""
        db.execute("INSERT INTO holidays (date, name, type, course_id) VALUES (?, '', 'holiday', 1)",
                   (MONDAY.strftime("%Y-%m-%d"),))
        db.commit()
        ok, why = wd.check(db, 1, MONDAY)
        assert not ok
        assert "Holiday" in why

    def test_two_batches_can_share_a_holiday_date(self, seeded, db):
        """Both batches take Independence Day off. The old schema had UNIQUE on
        `date` alone, so the second batch's calendar import crashed."""
        for cid, label in ((1, "Independence Day"), (2, "Independence Day")):
            db.execute(
                "INSERT INTO holidays (date, name, type, course_id) VALUES (?, ?, 'holiday', ?)",
                (MONDAY.strftime("%Y-%m-%d"), label, cid))
        db.commit()
        assert not wd.is_working_day(db, 1, MONDAY)
        assert not wd.is_working_day(db, 2, MONDAY)

    def test_missing_table_does_not_raise(self, seeded, db):
        """A broken holidays table must not stop attendance being marked on an
        ordinary Monday — but Sunday must still be blocked."""
        db.execute("DROP TABLE holidays")
        db.commit()
        assert wd.is_working_day(db, 1, MONDAY)
        assert not wd.is_working_day(db, 1, SUNDAY)


class TestHolidayName:

    def test_returns_the_name(self, seeded, db):
        db.execute("INSERT INTO holidays (date, name, type, course_id) VALUES (?, 'Holi', 'holiday', 1)",
                   (MONDAY.strftime("%Y-%m-%d"),))
        db.commit()
        assert wd.holiday_name(db, 1, MONDAY) == "Holi"

    def test_none_when_not_a_holiday(self, seeded, db):
        assert wd.holiday_name(db, 1, MONDAY) is None

    def test_none_for_a_bad_date(self, seeded, db):
        assert wd.holiday_name(db, 1, "rubbish") is None
