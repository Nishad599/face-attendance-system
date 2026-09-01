"""Subjects, the weekly timetable, and subject-wise attendance (Phase 6).

The timetable maps (course, weekday, slot) -> subject. Attendance stores the
subject_id at MARK time, so rearranging the timetable next term must never
rewrite what past attendance was for.
"""
from datetime import date, timedelta

import pytest

import timetable as tt


@pytest.fixture
def with_subjects(seeded, db):
    """Two modules, and a timetable putting ML in every morning_1 slot and
    Big Data in every afternoon_1 slot, Monday to Saturday."""
    cur = db.cursor()
    cur.execute("INSERT INTO subjects (course_id, name, code, min_attendance) "
                "VALUES (1, 'Machine Learning', 'ML-101', 75)")
    ml = cur.lastrowid
    cur.execute("INSERT INTO subjects (course_id, name, code, min_attendance) "
                "VALUES (1, 'Big Data', 'BD-201', 80)")
    bd = cur.lastrowid
    for wd in range(6):                       # Mon-Sat
        cur.execute("INSERT INTO timetable (course_id, weekday, session_type, subject_id, room) "
                    "VALUES (1, ?, 'morning_1', ?, 'Lab 1')", (wd, ml))
        cur.execute("INSERT INTO timetable (course_id, weekday, session_type, subject_id) "
                    "VALUES (1, ?, 'afternoon_1', ?)", (wd, bd))
    db.commit()
    return {"ml": ml, "bd": bd, **seeded}


class TestSubjectForSlot:

    def test_maps_a_slot_to_its_subject(self, with_subjects, db):
        monday = date(2026, 8, 31)            # a Monday
        assert tt.subject_for_slot(db, 1, monday, "morning_1") == with_subjects["ml"]
        assert tt.subject_for_slot(db, 1, monday, "afternoon_1") == with_subjects["bd"]

    def test_unmapped_slot_returns_none(self, with_subjects, db):
        monday = date(2026, 8, 31)
        assert tt.subject_for_slot(db, 1, monday, "morning_2") is None

    def test_sunday_is_unmapped(self, with_subjects, db):
        sunday = date(2026, 9, 6)
        assert tt.subject_for_slot(db, 1, sunday, "morning_1") is None

    def test_accepts_a_date_string(self, with_subjects, db):
        assert tt.subject_for_slot(db, 1, "2026-08-31", "morning_1") == with_subjects["ml"]

    def test_case_insensitive_slot(self, with_subjects, db):
        assert tt.subject_for_slot(db, 1, date(2026, 8, 31), "MORNING_1") == with_subjects["ml"]

    def test_missing_inputs_return_none_not_an_error(self, with_subjects, db):
        """Marking attendance must never fail because of the timetable."""
        assert tt.subject_for_slot(db, None, date(2026, 8, 31), "morning_1") is None
        assert tt.subject_for_slot(db, 1, None, "morning_1") is None
        assert tt.subject_for_slot(db, 1, date(2026, 8, 31), None) is None
        assert tt.subject_for_slot(db, 1, "not-a-date", "morning_1") is None

    def test_unknown_course_returns_none(self, with_subjects, db):
        assert tt.subject_for_slot(db, 9999, date(2026, 8, 31), "morning_1") is None


class TestGrid:

    def test_grid_has_seven_days_and_all_slots(self, with_subjects, db):
        grid = tt.get_grid(db, 1)
        assert len(grid) == 7
        assert [r["day"] for r in grid][:2] == ["Monday", "Tuesday"]
        assert all(len(r["slots"]) == len(tt.SLOTS) for r in grid)

    def test_grid_resolves_subject_names_and_rooms(self, with_subjects, db):
        monday = tt.get_grid(db, 1)[0]
        cell = next(c for c in monday["slots"] if c["session_type"] == "morning_1")
        assert cell["subject_name"] == "Machine Learning"
        assert cell["subject_code"] == "ML-101"
        assert cell["room"] == "Lab 1"

    def test_empty_cells_are_blank_not_missing(self, with_subjects, db):
        monday = tt.get_grid(db, 1)[0]
        cell = next(c for c in monday["slots"] if c["session_type"] == "morning_2")
        assert cell["subject_id"] is None
        assert cell["subject_name"] is None

    def test_grid_for_a_batch_with_no_timetable(self, seeded, db):
        grid = tt.get_grid(db, 1)
        assert len(grid) == 7
        assert all(c["subject_id"] is None for r in grid for c in r["slots"])


class TestSetGrid:

    def test_assigns_and_clears_cells(self, with_subjects, db):
        ml = with_subjects["ml"]
        tt.set_grid(db, 1, [
            {"weekday": 0, "session_type": "morning_2", "subject_id": ml, "room": "Lab 9"},
            {"weekday": 0, "session_type": "morning_1", "subject_id": None},
        ])
        assert tt.subject_for_slot(db, 1, date(2026, 8, 31), "morning_2") == ml
        assert tt.subject_for_slot(db, 1, date(2026, 8, 31), "morning_1") is None

    def test_replacing_a_cell_does_not_duplicate_it(self, with_subjects, db):
        ml, bd = with_subjects["ml"], with_subjects["bd"]
        tt.set_grid(db, 1, [{"weekday": 0, "session_type": "morning_1", "subject_id": bd}])
        n = db.execute(
            "SELECT COUNT(*) FROM timetable WHERE course_id=1 AND weekday=0 "
            "AND session_type='morning_1'").fetchone()[0]
        assert n == 1
        assert tt.subject_for_slot(db, 1, date(2026, 8, 31), "morning_1") == bd

    def test_invalid_entries_are_skipped(self, with_subjects, db):
        written = tt.set_grid(db, 1, [
            {"weekday": 9, "session_type": "morning_1", "subject_id": 1},   # bad weekday
            {"weekday": 0, "session_type": "lunchtime", "subject_id": 1},   # bad slot
            {"weekday": "x", "session_type": "morning_1", "subject_id": 1}, # unparseable
        ])
        assert written == 0

    def test_empty_entry_list_is_a_no_op(self, with_subjects, db):
        assert tt.set_grid(db, 1, []) == 0
        assert tt.set_grid(db, 1, None) == 0


class TestSubjectAttendance:

    def _mark(self, db, sid, day, slot, subject_id):
        db.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, session_type, "
            "course_id, subject_id) VALUES (?, ?, '09:00:00', 'present', ?, 1, ?)",
            (sid, day.strftime("%Y-%m-%d"), slot, subject_id),
        )
        db.commit()

    def test_no_subjects_returns_empty(self, seeded, db):
        assert tt.subject_attendance(db, 1) == []

    def test_counts_attendance_per_subject(self, with_subjects, db):
        sid = with_subjects["ids"]["C"]
        workdays = [d for d in with_subjects["work_days"] if d.weekday() != 6][:3]
        for d in workdays:
            self._mark(db, sid, d, "morning_1", with_subjects["ml"])

        rows = tt.subject_attendance(db, 1, student_db_id=sid)
        ml = next(r for r in rows if r["code"] == "ML-101")
        assert ml["attended"] == len(workdays)
        assert ml["expected"] > 0

    def test_at_risk_uses_the_subjects_own_minimum(self, with_subjects, db):
        """Big Data requires 80%, Machine Learning 75% — the flag must respect
        each module's own threshold, not a global one."""
        sid = with_subjects["ids"]["C"]
        rows = tt.subject_attendance(db, 1, student_db_id=sid)
        bd = next(r for r in rows if r["code"] == "BD-201")
        assert bd["min_attendance"] == 80
        assert bd["at_risk"] is True          # zero attendance

    def test_sorted_worst_first(self, with_subjects, db):
        sid = with_subjects["ids"]["C"]
        workdays = [d for d in with_subjects["work_days"] if d.weekday() != 6][:3]
        for d in workdays:
            self._mark(db, sid, d, "morning_1", with_subjects["ml"])
        rows = tt.subject_attendance(db, 1, student_db_id=sid)
        rates = [r["rate"] for r in rows]
        assert rates == sorted(rates)

    def test_holidays_do_not_count_as_expected_classes(self, with_subjects, db):
        sid = with_subjects["ids"]["C"]
        before = next(r for r in tt.subject_attendance(db, 1, student_db_id=sid)
                      if r["code"] == "ML-101")["expected"]

        workday = next(d for d in with_subjects["work_days"] if d.weekday() != 6)
        db.execute("INSERT INTO holidays (date, name, type, course_id) VALUES (?, 'Test', 'holiday', 1)",
                   (workday.strftime("%Y-%m-%d"),))
        db.commit()

        after = next(r for r in tt.subject_attendance(db, 1, student_db_id=sid)
                     if r["code"] == "ML-101")["expected"]
        assert after < before, "a holiday must not be an expected class"

    def test_batch_wide_scales_by_student_count(self, with_subjects, db):
        """Batch-level expected classes cover every enrolled student."""
        solo = tt.subject_attendance(db, 1, student_db_id=with_subjects["ids"]["C"])
        batch = tt.subject_attendance(db, 1)
        s_ml = next(r for r in solo if r["code"] == "ML-101")["expected"]
        b_ml = next(r for r in batch if r["code"] == "ML-101")["expected"]
        assert b_ml == s_ml * 3, "seeded fixture has 3 students"

    def test_inactive_subject_is_excluded(self, with_subjects, db):
        db.execute("UPDATE subjects SET is_active = 0 WHERE code = 'BD-201'")
        db.commit()
        rows = tt.subject_attendance(db, 1)
        assert all(r["code"] != "BD-201" for r in rows)


class TestHistoryIsNotRewritten:

    def test_moving_a_subject_does_not_change_past_attendance(self, with_subjects, db):
        """The reason subject_id is stored on the attendance row rather than
        joined through the timetable at read time."""
        sid = with_subjects["ids"]["C"]
        ml, bd = with_subjects["ml"], with_subjects["bd"]
        day = next(d for d in with_subjects["work_days"] if d.weekday() != 6)
        db.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, session_type, "
            "course_id, subject_id) VALUES (?, ?, '09:00:00', 'present', 'morning_1', 1, ?)",
            (sid, day.strftime("%Y-%m-%d"), ml))
        db.commit()

        # Next term, that slot becomes Big Data.
        tt.set_grid(db, 1, [{"weekday": day.weekday(), "session_type": "morning_1",
                             "subject_id": bd}])

        stored = db.execute(
            "SELECT subject_id FROM attendance WHERE student_id = ? AND date = ?",
            (sid, day.strftime("%Y-%m-%d"))).fetchone()[0]
        assert stored == ml, "past attendance must stay attached to the module it was taken for"
