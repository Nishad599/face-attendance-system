"""Student reads — and the wipe — stay inside one institute.

A raw grep found 50 `FROM students` queries with no batch filter, but most are
inherently scoped: a lookup by student id, or a uniqueness check that must span
the whole table. Classifying by actual risk left 17, and of those the one that
mattered was not a read at all:

    POST /api/admin/clear_all_data  ->  DELETE FROM students

Unscoped, an admin at one institute clicking "delete all student data" took
every other institute's students, attendance and face encodings with it. It is
the most destructive action in the app and it did not know tenancy existed.
"""

import asyncio
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tenancy                                   # noqa: E402
from conftest import session_for                 # noqa: E402


class TestStudentFilter:
    def test_unrestricted_at_one_institute(self, db):
        db.execute("INSERT INTO institutes (id, name, slug) VALUES (1, 'Only', 'only')")
        db.commit()
        assert tenancy.student_filter(db, session_for("admin", 1)) == ("", [])

    def test_restricted_when_several(self, db, two_institutes):
        sql, params = tenancy.student_filter(db, session_for("admin", 1))
        assert "course_id IN" in sql
        assert params == [1]

    def test_superadmin_unrestricted(self, db, two_institutes):
        assert tenancy.student_filter(db, session_for("superadmin", None)) == ("", [])

    def test_unknown_scope_matches_nothing(self, db, two_institutes):
        """A session issued before tenancy existed, on a deployment that has
        since gained a second institute. Showing everything would be the leak;
        showing nothing is merely a re-login."""
        stale = {"user_type": "admin", "user_info": {"id": 1, "role": "admin"}}
        sql, params = tenancy.student_filter(db, stale)
        assert "SELECT NULL" in sql

    def test_column_can_be_aliased(self, db, two_institutes):
        sql, _ = tenancy.student_filter(db, session_for("admin", 1), "s.course_id")
        assert "s.course_id IN" in sql

    def test_the_filter_actually_filters(self, db, two_institutes):
        """Run it, do not just inspect the string."""
        for institute, expected in ((1, "NG001"), (2, "SP001")):
            where, params = tenancy.student_filter(db, session_for("admin", institute))
            rows = db.execute(
                "SELECT student_id FROM students WHERE 1=1" + where, tuple(params)
            ).fetchall()
            assert [r[0] for r in rows] == [expected]


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


@needs_app
class TestScopedReads:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _run(self, db, coro_fn, institute_id):
        _app.attendance_system.conn = db
        return asyncio.new_event_loop().run_until_complete(
            coro_fn(session_for("admin", institute_id)))

    def test_student_count_is_scoped(self, db, two_institutes):
        assert self._run(db, _app.get_student_count, 1)["total_students"] == 1
        assert self._run(db, _app.get_student_count, 2)["total_students"] == 1

    def test_today_attendance_is_scoped(self, db, two_institutes):
        """It returns every active student's name and email."""
        _app.attendance_system.conn = db
        for institute, expected in ((1, "NG001"), (2, "SP001")):
            rows = _app.attendance_system.get_today_attendance(
                session_for("admin", institute))
            assert [r[1] for r in rows] == [expected]


@needs_app
class TestTheWipeCannotCrossInstitutes:
    """The one that would have been unrecoverable."""

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _clear(self, db, institute_id):
        _app.attendance_system.conn = db
        # load_student_faces() runs afterwards and wants the model; the stub
        # keeps the endpoint's own logic under test without it.
        original = _app.attendance_system.load_student_faces
        _app.attendance_system.load_student_faces = lambda: None
        try:
            return asyncio.new_event_loop().run_until_complete(
                _app.clear_all_data(session_for("admin", institute_id)))
        finally:
            _app.attendance_system.load_student_faces = original

    def test_only_the_callers_students_are_deleted(self, db, two_institutes):
        d = self._clear(db, 1)
        assert d["success"] is True

        left = [r[0] for r in db.execute(
            "SELECT student_id FROM students ORDER BY student_id").fetchall()]
        assert left == ["SP001"], (
            "the other institute's students were deleted too")

    def test_the_other_institutes_attendance_survives(self, db, two_institutes):
        sp = two_institutes["students"]["SP001"]
        ng = two_institutes["students"]["NG001"]
        for sid, cid in ((sp, 2), (ng, 1)):
            db.execute(
                "INSERT INTO attendance (student_id, date, time_in, status, course_id) "
                "VALUES (?, '2026-10-12', '09:00:00', 'present', ?)", (sid, cid))
        db.commit()

        self._clear(db, 1)

        rows = db.execute("SELECT student_id FROM attendance").fetchall()
        assert [r[0] for r in rows] == [sp]

    def test_single_institute_still_clears_everything(self, db):
        db.execute("INSERT INTO institutes (id, name, slug) VALUES (1, 'Only', 'only')")
        db.execute("INSERT INTO courses (id, name, start_date, end_date, institute_id) "
                   "VALUES (1, 'B', '2026-01-01', '2027-01-01', 1)")
        db.execute("INSERT INTO students (student_id, name, email, course_id, status) "
                   "VALUES ('S1', 'Solo', 's1@t.local', 1, 'active')")
        db.commit()

        self._clear(db, 1)
        assert db.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0
