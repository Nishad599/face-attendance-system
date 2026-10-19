"""Manual marking writes the table reports actually read.

There were two endpoints writing attendance by different routes:

    POST /api/attendance/manual/session   -> slot_attendance ONLY
    POST /api/attendance/bulk-mark        -> attendance

`attendance` is what every report, analytic and export reads, so a teacher who
marked a student present through the first one left them absent in every figure
that mattered — and the write skipped the batch-ownership and working-day
checks the second one makes.

manual/session is now an adapter onto bulk-mark. These tests pin that, so the
two cannot drift apart again.
"""

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

APP_FILE = os.path.join(ROOT, "main_with_face_recognition.py")


def source():
    with open(APP_FILE, encoding="utf-8") as f:
        return f.read()


def handler(path, method="post"):
    src = source()
    idx = src.find(f'@app.{method}("{path}")')
    assert idx != -1, f"no {method.upper()} {path}"
    end = src.find("\n@app.", idx + 10)
    return src[idx:end if end != -1 else len(src)]


class TestOneMarkingPath:

    def test_manual_delegates_to_bulk_mark(self):
        body = handler("/api/attendance/manual/session")
        assert "bulk_mark_attendance_api" in body, (
            "manual/session must delegate, not write attendance itself")

    def test_manual_writes_nothing_itself(self):
        """Beyond resolving the student's batch it must run no SQL of its own —
        any INSERT here is a second implementation starting again."""
        body = handler("/api/attendance/manual/session")
        # Drop the docstring: it explains what this used to write, by name.
        parts = body.split('"""')
        code = parts[0] + ("".join(parts[2:]) if len(parts) > 2 else "")
        assert "INSERT INTO" not in code
        assert "slot_attendance" not in code

    def test_the_slot_only_writer_is_gone(self):
        """AttendanceSystem.mark_manual_session_attendance() wrote only
        slot_attendance. Keeping it invites the same bug back."""
        src = source()
        assert "def mark_manual_session_attendance(self" not in src
        assert "attendance_system.mark_manual_session_attendance(" not in src

    def test_whole_day_maps_to_no_session_type(self):
        """The old API used 'full_day' and expanded it into four slot rows. One
        row with no session_type is what reports.day_credit() already counts as
        a full day."""
        body = handler("/api/attendance/manual/session")
        assert "full_day" in body
        assert "slot = None" in body or "slot=None" in body

    def test_batch_is_resolved_from_the_student(self):
        """bulk-mark needs a course_id; the old API never took one."""
        body = handler("/api/attendance/manual/session")
        assert re.search(r"SELECT course_id FROM students", body)

    def test_permission_errors_are_not_swallowed(self):
        """bulk-mark raises HTTPException for a batch the caller does not own.
        A bare `except Exception` would turn a 403 into {'success': False}."""
        body = handler("/api/attendance/manual/session")
        assert "except HTTPException" in body
        assert body.index("except HTTPException") < body.index("except Exception")


class TestBulkMarkStillGuards:
    """The path everything now goes through keeps its checks."""

    def test_checks_batch_ownership(self):
        assert "assert_course_allowed" in handler("/api/attendance/bulk-mark")

    def test_refuses_non_working_days_when_marking_present(self):
        body = handler("/api/attendance/bulk-mark")
        assert "working_days.check" in body

    def test_writes_the_primary_attendance_table(self):
        body = handler("/api/attendance/bulk-mark")
        assert re.search(r"INTO attendance\b", body)
