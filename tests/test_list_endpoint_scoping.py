"""The list endpoints that take no id, and so were never swept.

Three of them returned every institute's students to any admin who asked:

    GET /api/students/list            the roster behind /people
    GET /api/attendance/today/slots   names, rolls, emails, four slot times
    GET /api/attendance/bulk-export   the same, as a CSV file to keep

They were missed because every earlier sweep looked for endpoints taking an
id from the URL. These take nothing, so there was no id to check and nothing
flagged them. Being authenticated is not the same as being entitled, and
`/api/students/list` was in fact the first entry in this suite's own list of
"endpoints whose exposure mattered most" -- guarded for authentication, never
scoped for tenancy.

Two restrictions have to hold at once, which is the other half of why this
went wrong: the institute, and for a teacher their assigned batches.
teacher_allowed_course_ids() returns None for an admin meaning "every batch",
so applying it alone checks nothing for exactly the role that has the most to
see. visible_student_filter() combines both.

These tests attempt the access and read the bytes that come back. An earlier
static sweep cleared bulk-export, because a StreamingResponse has no .body and
the check was silently inspecting a repr.
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from conftest import session_for                  # noqa: E402

_app = None
try:
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        import importlib
        _app = importlib.import_module("main_with_face_recognition")
    finally:
        os.chdir(cwd)
except ImportError:
    _app = None

needs_app = pytest.mark.skipif(_app is None, reason="run from venv_win")

TODAY = date.today().strftime("%Y-%m-%d")


@needs_app
class TestListEndpointsAreScoped:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def world(self, db):
        """Institute 1 runs two batches, institute 2 runs one.

        Two batches at institute 1 is what makes the teacher case meaningful:
        a teacher assigned to one of them must not see the other, which is a
        different restriction from the institute boundary and fails
        separately.

            institute 1  NG-A (course 1)  NG001      NG-B (course 3)  NG002
            institute 2  SP-BATCH (2)     SP001
        """
        cur = db.cursor()
        for iid, name, slug in ((1, "Northgate", "northgate"),
                                (2, "Southpoint", "southpoint")):
            cur.execute("INSERT INTO institutes (id, name, slug, is_active, "
                        "created_at) VALUES (?, ?, ?, 1, ?)",
                        (iid, name, slug, datetime.now().isoformat()))
        start = (date.today() - timedelta(days=20)).strftime("%Y-%m-%d")
        for cid, iid, nm in ((1, 1, "NG-A"), (3, 1, "NG-B"), (2, 2, "SP-BATCH")):
            cur.execute("INSERT INTO courses (id, name, start_date, end_date, "
                        "is_active, created_at, institute_id) "
                        "VALUES (?, ?, ?, '2027-12-31', 1, ?, ?)",
                        (cid, nm, start, datetime.now().isoformat(), iid))
        for sid, (roll, cid, nm, em) in enumerate(
                (("NG001", 1, "Northgate A Student", "nga@x.test"),
                 ("NG002", 3, "Northgate B Student", "ngb@x.test"),
                 ("SP001", 2, "Southpoint Student", "sp@x.test")), start=1):
            cur.execute("INSERT INTO students (id, student_id, name, email, "
                        "status, course_id, registration_date) "
                        "VALUES (?, ?, ?, ?, 'active', ?, ?)",
                        (sid, roll, nm, em, cid, datetime.now().isoformat()))
            cur.execute("INSERT INTO attendance (student_id, date, time_in, "
                        "status, course_id, session_type) VALUES "
                        "(?, ?, '09:00:00', 'present', ?, 'morning_1')",
                        (sid, TODAY, cid))
            for slot in ("morning_1", "afternoon_1"):
                cur.execute("INSERT INTO slot_attendance (student_id, date, "
                            "slot_id, course_id) VALUES (?, ?, ?, ?)",
                            (sid, TODAY, slot, cid))
        cur.execute("INSERT INTO users (id, username, password_hash, role, "
                    "is_active, institute_id, name, email) VALUES "
                    "(9, 't', 'x', 'teacher', 1, 1, 'T', 't@x.test')")
        cur.execute("INSERT INTO teacher_batches (user_id, course_id) VALUES (9, 1)")
        db.commit()
        _app.attendance_system.conn = db
        return {"loop": asyncio.new_event_loop()}

    @staticmethod
    def _teacher():
        return {"user_type": "teacher",
                "user_info": {"id": 9, "role": "teacher", "institute_id": 1}}

    def _text(self, world, coro):
        """Whatever came back, as text -- dict, list or streamed CSV."""
        r = world["loop"].run_until_complete(coro)
        if hasattr(r, "body_iterator"):
            async def drain():
                out = []
                async for c in r.body_iterator:
                    out.append(c if isinstance(c, bytes) else str(c).encode())
                return b"".join(out).decode("utf-8", "replace")
            return world["loop"].run_until_complete(drain())
        return str(r)

    # ---- the institute boundary ------------------------------------------

    @pytest.mark.parametrize("label,call", [
        ("students/list", lambda a, s: a.list_students(session=s)),
        ("today/slots", lambda a, s: a.get_today_slot_attendance(session=s)),
        ("bulk-export", lambda a, s: a.bulk_export_attendance(
            start_date=TODAY, end_date=TODAY, format="student_summary", session=s)),
    ])
    def test_an_admin_sees_only_their_own_institute(self, world, label, call):
        body = self._text(world, call(_app, session_for("admin", 1)))
        for leaked in ("SP001", "sp@x.test", "Southpoint Student"):
            assert leaked not in body, f"{label} leaked {leaked}"

    @pytest.mark.parametrize("label,call", [
        ("students/list", lambda a, s: a.list_students(session=s)),
        ("today/slots", lambda a, s: a.get_today_slot_attendance(session=s)),
        ("bulk-export", lambda a, s: a.bulk_export_attendance(
            start_date=TODAY, end_date=TODAY, format="student_summary", session=s)),
    ])
    def test_an_admin_still_sees_all_of_their_own(self, world, label, call):
        """The control. A filter that returns nothing passes every leak test
        above while making the product useless."""
        body = self._text(world, call(_app, session_for("admin", 1)))
        for own in ("NG001", "NG002"):
            assert own in body, f"{label} hid the caller's own student {own}"

    # ---- the batch boundary ----------------------------------------------

    @pytest.mark.parametrize("label,call", [
        ("students/list", lambda a, s: a.list_students(session=s)),
        ("today/slots", lambda a, s: a.get_today_slot_attendance(session=s)),
        ("bulk-export", lambda a, s: a.bulk_export_attendance(
            start_date=TODAY, end_date=TODAY, format="student_summary", session=s)),
    ])
    def test_a_teacher_sees_only_their_assigned_batch(self, world, label, call):
        body = self._text(world, call(_app, self._teacher()))
        assert "NG001" in body, f"{label} hid the teacher's own batch"
        assert "NG002" not in body, f"{label} showed an unassigned batch"
        assert "SP001" not in body, f"{label} crossed the institute boundary"

    def test_a_teacher_with_no_batches_sees_nobody(self, world, db):
        """Not everybody. teacher_allowed_course_ids() returns [] here, and an
        empty IN () has to mean none rather than no filter."""
        db.execute("DELETE FROM teacher_batches WHERE user_id = 9")
        db.commit()
        body = self._text(world, _app.list_students(session=self._teacher()))
        for roll in ("NG001", "NG002", "SP001"):
            assert roll not in body

    # ---- the aggregate, which leaks a headcount rather than a name -------

    def test_the_daily_summary_counts_only_the_callers_students(self, world):
        """It prints no names, so it reads as safe -- but it was counting
        every institute into one total, which is both a disclosure and simply
        the wrong number on a report someone files."""
        body = self._text(world, _app.bulk_export_attendance(
            start_date=TODAY, end_date=TODAY, format="daily_summary",
            session=session_for("admin", 1)))
        row = [ln for ln in body.splitlines() if ln.startswith(TODAY)]
        assert row, "no data row in the daily summary"
        assert row[0].split(",")[2] == "2", (
            "Total Students should be Northgate's 2, got: " + row[0])


@needs_app
class TestTheHelperItself:
    """visible_student_filter() is the single place three endpoints get this
    right, so it is worth testing apart from them."""

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def test_it_is_inert_at_a_single_institute(self, db, seeded):
        """The whole tenancy design rests on this: at one institute every
        scoping helper must return no filter at all, or every list in the app
        narrows the day the feature ships."""
        _app.attendance_system.conn = db
        where, params = _app.visible_student_filter(session_for("admin", 1))
        assert where == ""
        assert params == []

    def test_it_combines_both_restrictions(self, db, two_institutes):
        _app.attendance_system.conn = db
        where, _ = _app.visible_student_filter(session_for("admin", 2))
        assert "course_id IN" in where

    def test_it_honours_the_column_name(self, db, two_institutes):
        """Called with s.course_id from queries that alias students as s."""
        _app.attendance_system.conn = db
        where, _ = _app.visible_student_filter(session_for("admin", 2), "s.course_id")
        assert "s.course_id" in where
