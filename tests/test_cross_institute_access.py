"""Can an admin of one institute reach another institute's data by id?

Scoping a LIST query is the easy half. The harder half is every endpoint that
takes an id straight from the URL: the role check passes, because the caller
genuinely is an admin — just not of that record's institute.

These tests attempt the access rather than inspecting the source. A static
sweep passed update_student and delete_student because they mention
teacher_allowed_course_ids() — which returns None for an admin, meaning "all
batches", so the entire scope block was skipped and nothing was checked. Only
running it found that.
"""

import asyncio
import os
import sys

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


@needs_app
class TestNoAccessAcrossInstitutes:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def world(self, db, two_institutes):
        """Institute 1 = Northgate (NG001), institute 2 = Southpoint (SP001)."""
        _app.attendance_system.conn = db
        return {
            "ng_student": two_institutes["students"]["NG001"],
            "sp_student": two_institutes["students"]["SP001"],
            "sp_teacher": db.execute(
                "SELECT id FROM users WHERE username = 'sp_admin'").fetchone()[0],
            "loop": asyncio.new_event_loop(),
        }

    def _blocked(self, world, fn):
        try:
            world["loop"].run_until_complete(fn())
        except _app.HTTPException as e:
            return e.status_code
        return None

    # ---- reads -----------------------------------------------------------

    @pytest.mark.parametrize("label,call", [
        ("student details", lambda a, w: a.get_student_details(w["sp_student"], w["admin"])),
        ("slot history", lambda a, w: a.get_student_slot_attendance(w["sp_student"], w["admin"])),
        ("session history", lambda a, w: a.get_student_session_attendance(w["sp_student"], w["admin"])),
        ("sparkline", lambda a, w: a.get_student_sparkline(w["sp_student"], 14, w["admin"])),
        ("CSV export", lambda a, w: a.export_student_attendance(w["sp_student"], w["admin"])),
    ])
    def test_cannot_read_another_institutes_student(self, world, label, call):
        world["admin"] = session_for("admin", 1)
        assert self._blocked(world, lambda: call(_app, world)) == 403, (
            f"{label} leaked another institute's student")

    # ---- writes ----------------------------------------------------------

    def test_cannot_rename_another_institutes_student(self, world, db):
        world["admin"] = session_for("admin", 1)
        assert self._blocked(
            world, lambda: _app.update_student(
                world["sp_student"], {"name": "hijack"}, world["admin"])) == 403
        assert db.execute("SELECT name FROM students WHERE id = ?",
                          (world["sp_student"],)).fetchone()[0] != "hijack"

    def test_cannot_delete_another_institutes_student(self, world, db):
        world["admin"] = session_for("admin", 1)
        assert self._blocked(
            world, lambda: _app.delete_student(
                world["sp_student"], world["admin"])) == 403
        assert db.execute("SELECT COUNT(*) FROM students WHERE id = ?",
                          (world["sp_student"],)).fetchone()[0] == 1

    def test_cannot_move_a_student_across_institutes(self, world, db):
        """In either direction: the destination batch is a write too."""
        world["admin"] = session_for("admin", 1)
        assert self._blocked(
            world, lambda: _app.update_student(
                world["sp_student"], {"course_id": 1}, world["admin"])) == 403
        assert self._blocked(
            world, lambda: _app.update_student(
                world["ng_student"], {"course_id": 2}, world["admin"])) == 403
        assert db.execute("SELECT course_id FROM students WHERE id = ?",
                          (world["ng_student"],)).fetchone()[0] == 1

    def test_cannot_edit_or_delete_another_institutes_batch(self, world, db):
        world["admin"] = session_for("admin", 1)
        assert self._blocked(
            world, lambda: _app.update_course(
                2, _app.CourseUpdate(name="hijack"), world["admin"])) == 403
        assert self._blocked(
            world, lambda: _app.delete_course(2, world["admin"])) == 403
        assert db.execute("SELECT name FROM courses WHERE id = 2").fetchone()[0] == "SP-BATCH"

    def test_cannot_touch_another_institutes_staff(self, world):
        world["admin"] = session_for("admin", 1)
        assert self._blocked(
            world, lambda: _app.reset_teacher_password(
                world["sp_teacher"], {}, world["admin"])) == 403

    def test_cannot_retime_another_institutes_slots(self, world):
        """Slot timings decide whether a student can mark at all. The batch
        arrives in the body, not the URL, which is why an id sweep misses it."""
        world["admin"] = session_for("admin", 1)
        assert self._blocked(
            world, lambda: _app.update_session_configuration(
                "morning_1",
                {"start_time": "00:00", "end_time": "23:59", "course_id": 2},
                world["admin"])) == 403

    # ---- the control: legitimate access still works ----------------------

    def test_own_institute_is_unaffected(self, world, db):
        world["admin"] = session_for("admin", 1)
        d = world["loop"].run_until_complete(
            _app.update_student(world["ng_student"], {"name": "Legit"}, world["admin"]))
        assert d["success"] is True
        assert db.execute("SELECT name FROM students WHERE id = ?",
                          (world["ng_student"],)).fetchone()[0] == "Legit"


class TestForbiddenIsNotReportedAsSuccess:
    """A bare `except Exception` catches HTTPException too, so a 403 came back
    as 200 with success:false. The caller could not tell 'forbidden' from
    'something broke', and the status code was simply wrong."""

    def test_handlers_that_raise_also_reraise(self):
        import re
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        offenders = []
        for m in re.finditer(r'@app\.(get|put|post|delete)\("([^"]+)"\)', src):
            start = m.start()
            end = src.find("\n@app.", start + 10)
            body = src[start:end if end != -1 else len(src)]
            raises = any(k in body for k in (
                "HTTPException(", "assert_course_allowed", "assert_student_allowed",
                "assert_staff_allowed"))
            if raises and "except Exception" in body and "except HTTPException" not in body:
                offenders.append(m.group(2))
        assert not offenders, (
            "these turn a 403/404 into 200 success:false: " + ", ".join(offenders))
