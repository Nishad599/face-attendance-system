"""Tenant isolation: one institute must never read another's data.

This is the test suite Phase A exists for. Every other bug in this project
costs someone an afternoon; a hole here hands one customer another customer's
students, so these tests are written adversarially — two institutes, colliding
dates, colliding slot timings — and they assert the leak does not happen rather
than that the happy path works.

The governing rule, which several tests pin directly:

    TENANCY IS INERT AT ONE INSTITUTE.

    Every scoping function falls back to unscoped behaviour while there is a
    single institute, so this phase cannot change what a running single-tenant
    deployment does. That fallback is load-bearing, and it is also exactly the
    kind of thing that quietly stops working, so it is tested on both sides.
"""

import os
import sys
from datetime import date, datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tenancy                                    # noqa: E402
import working_days                               # noqa: E402
from conftest import session_for                  # noqa: E402


# ---------------------------------------------------------------------------
# The inert-at-one-institute rule
# ---------------------------------------------------------------------------

class TestSingleTenantStaysUnscoped:
    """A one-institute deployment must behave exactly as it did before."""

    def test_no_institutes_at_all_is_single_tenant(self, db):
        """A database that has not run migrate_phase10 must still work."""
        assert tenancy.institute_count(db) == 0
        assert tenancy.is_single_tenant(db) is True

    def test_one_institute_is_single_tenant(self, db):
        db.execute("INSERT INTO institutes (id, name, slug) VALUES (1, 'Only', 'only')")
        db.commit()
        assert tenancy.is_single_tenant(db) is True

    def test_two_institutes_is_not(self, db, two_institutes):
        assert tenancy.institute_count(db) == 2
        assert tenancy.is_single_tenant(db) is False

    def test_visible_courses_unrestricted_at_one_institute(self, db):
        db.execute("INSERT INTO institutes (id, name, slug) VALUES (1, 'Only', 'only')")
        db.commit()
        # None means "no restriction" — what every existing caller expects.
        assert tenancy.visible_course_ids(db, session_for("admin", 1)) is None

    def test_holiday_clause_is_empty_at_one_institute(self, db):
        db.execute("INSERT INTO institutes (id, name, slug) VALUES (1, 'Only', 'only')")
        db.commit()
        clause, params = tenancy.holiday_scope_clause(db, 1)
        assert clause == "" and params == []


# ---------------------------------------------------------------------------
# Course scoping
# ---------------------------------------------------------------------------

class TestCourseScoping:
    def test_each_institute_sees_only_its_own_batches(self, db, two_institutes):
        assert tenancy.course_ids_for_institute(db, 1) == [1]
        assert tenancy.course_ids_for_institute(db, 2) == [2]

    def test_visible_courses_are_scoped_when_multi_tenant(self, db, two_institutes):
        assert tenancy.visible_course_ids(db, session_for("admin", 1)) == [1]
        assert tenancy.visible_course_ids(db, session_for("admin", 2)) == [2]

    def test_superadmin_is_unrestricted(self, db, two_institutes):
        assert tenancy.visible_course_ids(db, session_for("superadmin", None)) is None
        assert tenancy.institute_for_session(session_for("superadmin", None)) is None

    def test_a_session_with_no_institute_sees_nothing(self, db, two_institutes):
        """A session issued before Phase 10, on a deployment that has since
        gained a second institute. Guessing would mean showing everything, so
        the scope is empty instead."""
        stale = {"user_type": "admin", "user_info": {"id": 1, "role": "admin"}}
        assert tenancy.visible_course_ids(db, stale) == []

    def test_cannot_act_on_another_institutes_batch(self, db, two_institutes):
        ng = session_for("admin", 1)
        assert tenancy.session_may_touch_course(db, ng, 1) is True
        assert tenancy.session_may_touch_course(db, ng, 2) is False

    def test_superadmin_may_touch_any_batch(self, db, two_institutes):
        boss = session_for("superadmin", None)
        assert tenancy.session_may_touch_course(db, boss, 1) is True
        assert tenancy.session_may_touch_course(db, boss, 2) is True

    def test_institute_resolves_from_course_and_student(self, db, two_institutes):
        assert tenancy.institute_of_course(db, 1) == 1
        assert tenancy.institute_of_course(db, 2) == 2
        assert tenancy.institute_of_student(db, two_institutes["students"]["NG001"]) == 1
        assert tenancy.institute_of_student(db, two_institutes["students"]["SP001"]) == 2

    def test_slug_lookup_does_not_match_across_institutes(self, db, two_institutes):
        assert tenancy.institute_by_slug(db, "northgate") == 1
        assert tenancy.institute_by_slug(db, "southpoint") == 2
        assert tenancy.institute_by_slug(db, "nope") is None
        assert tenancy.institute_by_slug(db, None) is None


# ---------------------------------------------------------------------------
# Holidays — the quiet one
# ---------------------------------------------------------------------------

class TestHolidaysDoNotCross:
    """Both institutes declared an institute-wide holiday on the same date.

    `holidays.course_id IS NULL` means "every batch". Before Phase 10 that was
    read literally, so each institute would have picked up the other's holiday
    and lost a working day it never declared — silently changing every
    attendance percentage that batch has ever shown.
    """

    def test_each_batch_gets_only_its_own_institutes_holiday(self, db, two_institutes):
        clash = two_institutes["clash_date"]
        assert working_days.holiday_name(db, 1, clash) == "Northgate Founders Day"
        assert working_days.holiday_name(db, 2, clash) == "Southpoint Sports Day"

    def test_holiday_dates_do_not_bleed(self, db, two_institutes):
        """A date that is a holiday for ONE institute only."""
        solo = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        db.execute(
            "INSERT INTO holidays (date, name, type, course_id, institute_id) "
            "VALUES (?, 'Northgate Only', 'institute', NULL, 1)", (solo,))
        db.commit()

        solo_date = datetime.strptime(solo, "%Y-%m-%d").date()
        assert solo_date in working_days.holiday_dates(db, 1)
        assert solo_date not in working_days.holiday_dates(db, 2)

    def test_check_blocks_the_right_institute_only(self, db, two_institutes):
        clash = two_institutes["clash_date"]
        ok1, why1 = working_days.check(db, 1, clash)
        ok2, why2 = working_days.check(db, 2, clash)
        assert ok1 is False and "Northgate" in why1
        assert ok2 is False and "Southpoint" in why2
        assert "Southpoint" not in why1
        assert "Northgate" not in why2

    def test_a_batch_scoped_holiday_still_only_hits_that_batch(self, db, two_institutes):
        """Scoping by institute must not accidentally widen a per-batch holiday."""
        d = (date.today() - timedelta(days=6)).strftime("%Y-%m-%d")
        db.execute(
            "INSERT INTO holidays (date, name, type, course_id, institute_id) "
            "VALUES (?, 'NG batch only', 'batch', 1, 1)", (d,))
        db.commit()
        assert working_days.holiday_name(db, 1, d) == "NG batch only"
        assert working_days.holiday_name(db, 2, d) is None

    def test_every_reader_agrees(self, db, two_institutes):
        """reports, timetable and analytics each had their own copy of the
        holiday query. All four must scope identically, or a batch's percentage
        would differ depending on which screen you opened.

        Note the clash date alone cannot prove this: both institutes declared a
        holiday on it, so both correctly see that date. The discriminator is a
        holiday only ONE institute declared.
        """
        import analytics_manager
        import reports
        import timetable

        solo = (date.today() - timedelta(days=7))
        db.execute(
            "INSERT INTO holidays (date, name, type, course_id, institute_id) "
            "VALUES (?, 'Northgate Only', 'institute', NULL, 1)",
            (solo.strftime("%Y-%m-%d"),))
        db.commit()

        analytics = analytics_manager.AnalyticsManager()
        readers = {
            "working_days": lambda cid: working_days.holiday_dates(db, cid),
            "reports": lambda cid: reports._holiday_dates(db.cursor(), cid),
            "timetable": lambda cid: timetable._holiday_dates(db, cid),
            "analytics": lambda cid: analytics._holiday_dates(db.cursor(), cid),
        }

        for name, read in readers.items():
            assert solo in read(1), f"{name} lost institute 1's own holiday"
            assert solo not in read(2), f"{name} leaked institute 1's holiday to 2"

        # And they agree with each other, batch for batch.
        for cid in (1, 2):
            sets = {name: read(cid) for name, read in readers.items()}
            assert len(set(map(frozenset, sets.values()))) == 1, (
                f"holiday readers disagree for batch {cid}: {sets}")


# ---------------------------------------------------------------------------
# Slot timings
# ---------------------------------------------------------------------------

class TestSlotTimingsDoNotCross:
    """Northgate runs mornings, Southpoint runs afternoons.

    The all-batches fallback in load_session_configs() used to read every
    batch's rows, so one institute's hours could decide whether the other's
    student was inside their slot.
    """

    def _manager(self, db):
        from attendance_manager import AttendanceSlotManager
        mgr = AttendanceSlotManager.__new__(AttendanceSlotManager)
        mgr.conn = db
        mgr.attendance_slots = {}
        return mgr

    def test_each_batch_reads_its_own_timings(self, db, two_institutes):
        mgr = self._manager(db)
        assert mgr.load_session_configs(1)["morning_1"]["start_time"].hour == 8
        assert mgr.load_session_configs(2)["morning_1"]["start_time"].hour == 14

    def test_the_fallback_is_scoped_to_one_institute(self, db, two_institutes):
        """The merged view, asked for institute 2, must not see institute 1."""
        mgr = self._manager(db)
        merged = mgr.load_session_configs(None, institute_id=2)
        assert merged["morning_1"]["start_time"].hour == 14

        merged1 = mgr.load_session_configs(None, institute_id=1)
        assert merged1["morning_1"]["start_time"].hour == 8

    def test_unscoped_merge_still_works_at_one_institute(self, db):
        db.execute("INSERT INTO institutes (id, name, slug) VALUES (1, 'Only', 'only')")
        db.execute("INSERT INTO courses (id, name, start_date, end_date, institute_id) "
                   "VALUES (1, 'B', '2026-01-01', '2027-01-01', 1)")
        db.execute("INSERT INTO session_configs (course_id, session_type, start_time, "
                   "end_time, is_active) VALUES (1, 'morning_1', '08:30:00', '09:30:00', 1)")
        db.commit()
        mgr = self._manager(db)
        assert mgr.load_session_configs(None)["morning_1"]["start_time"].hour == 8


# ---------------------------------------------------------------------------
# Terminal batch list — public, so the worst one to leak
# ---------------------------------------------------------------------------

class TestTerminalBatchListing:
    """`/api/terminal/batches` is unauthenticated by design: the kiosk shows the
    picker before anyone signs in. It must therefore never return more than one
    institute's batch names."""

    def _batches(self, db, institute_slug):
        """The query the endpoint runs, exercised directly."""
        sql = ("SELECT id, name FROM courses "
               "WHERE is_active = 1 AND terminal_pin_hash IS NOT NULL")
        params = []
        if not tenancy.is_single_tenant(db):
            institute_id = tenancy.institute_by_slug(db, institute_slug)
            if institute_id is None:
                return None            # endpoint returns the institute picker
            sql += " AND institute_id = ?"
            params.append(institute_id)
        return [r[1] for r in db.execute(sql + " ORDER BY name", params).fetchall()]

    def test_without_an_institute_nothing_is_listed(self, db, two_institutes):
        assert self._batches(db, None) is None

    def test_each_institute_sees_only_its_own(self, db, two_institutes):
        assert self._batches(db, "northgate") == ["NG-BATCH"]
        assert self._batches(db, "southpoint") == ["SP-BATCH"]

    def test_single_tenant_lists_everything_as_before(self, db):
        db.execute("INSERT INTO institutes (id, name, slug) VALUES (1, 'Only', 'only')")
        db.execute("INSERT INTO courses (id, name, start_date, end_date, is_active, "
                   "institute_id, terminal_pin_hash) "
                   "VALUES (1, 'B', '2026-01-01', '2027-01-01', 1, 1, 'x')")
        db.commit()
        assert self._batches(db, None) == ["B"]


# ---------------------------------------------------------------------------
# Face gallery — needs the real app module
# ---------------------------------------------------------------------------

_app = None
_err = None
try:
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        import importlib
        _app = importlib.import_module("main_with_face_recognition")
    finally:
        os.chdir(cwd)
except ImportError as e:
    _err = e

needs_app = pytest.mark.skipif(
    _app is None, reason=f"cannot import the app here ({_err}); run from venv_win")


@needs_app
class TestFaceGalleryIsPerInstitute:
    """THE hazard. Detection used to match against every active student's
    embedding on the deployment, so a student enrolled at one institute would
    be recognised — and marked present — at another institute's terminal."""

    def _system(self, db):
        import numpy as np

        sysobj = _app.AttendanceSystem.__new__(_app.AttendanceSystem)
        sysobj.conn = db
        # Two students with distinguishable embeddings, one per institute.
        for roll, vec in (("NG001", np.ones(512, dtype=np.float64)),
                          ("SP001", np.full(512, 2.0, dtype=np.float64))):
            db.execute("UPDATE students SET face_encoding = ? WHERE student_id = ?",
                       (vec.tobytes(), roll))
        db.commit()
        sysobj.load_student_faces()
        return sysobj

    def test_galleries_are_indexed_by_institute(self, db, two_institutes):
        s = self._system(db)
        assert set(s.face_index.keys()) == {1, 2}
        assert len(s.face_index[1]["ids"]) == 1
        assert len(s.face_index[2]["ids"]) == 1

    def test_one_institute_never_sees_the_others_faces(self, db, two_institutes):
        s = self._system(db)
        names1 = s.gallery_for(1)[1]
        names2 = s.gallery_for(2)[1]
        assert names1 == ["Student NG001"]
        assert names2 == ["Student SP001"]
        assert "Student SP001" not in names1
        assert "Student NG001" not in names2

    def test_unscoped_gallery_sees_everyone(self, db, two_institutes):
        """What a superadmin gets, and what a single-institute deployment uses."""
        s = self._system(db)
        assert len(s.gallery_for(None)[0]) == 2

    def test_single_institute_is_unaffected(self, db):
        """The fallback: with one institute, a scoped call still returns the
        whole gallery, so nothing about today's deployment changes."""
        import numpy as np

        db.execute("INSERT INTO institutes (id, name, slug) VALUES (1, 'Only', 'only')")
        db.execute("INSERT INTO courses (id, name, start_date, end_date, institute_id) "
                   "VALUES (1, 'B', '2026-01-01', '2027-01-01', 1)")
        db.execute("INSERT INTO students (student_id, name, email, course_id, status, "
                   "face_encoding) VALUES ('S1', 'Solo', 's1@t.local', 1, 'active', ?)",
                   (np.ones(512, dtype=np.float64).tobytes(),))
        db.commit()

        s = _app.AttendanceSystem.__new__(_app.AttendanceSystem)
        s.conn = db
        s.load_student_faces()
        assert len(s.gallery_for(1)[0]) == 1
        assert len(s.gallery_for(None)[0]) == 1
