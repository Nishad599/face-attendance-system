"""One frame, one transaction.

A camera frame can recognise a whole row of students at once -- that is the
point of group marking, and the reason a batch moves through a terminal
quickly. The writes, though, were per student: each recognised face got its
own commit, its own repeated lookups, and its own full recomputation of a
daily summary table that nothing reads.

On the VM that matters more than the raw numbers suggest. SQLite allows one
writer at a time, so every commit is both an fsync and a turn holding the
lock, taken in the exact minute when several terminals are all marking.

What this pins down:

  * one BEGIN and one COMMIT for the whole frame
  * nothing half-written if the frame fails part-way
  * batch-level lookups (holidays, slot, subject, late rule) computed once
    per batch rather than once per face
  * update_daily_summary stays deleted

Measured on a file-backed WAL database, writing N marks:

      students   commit each   one commit
             6       2.9 ms       0.5 ms
            30      15.2 ms       0.5 ms
            60      29.0 ms       0.6 ms

The rows written are identical either way, which is the part that actually
needs guarding, so most of what follows checks results rather than timings.
"""

import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from attendance_manager import AttendanceSlotManager      # noqa: E402


def handler_body(src, route):
    """The source of one route's handler.

    Slices to the next @app. decorator, or to end of file --
    /api/detect_attendance_slots happens to be the last route defined, which
    an earlier version of this helper did not survive.
    """
    start = src.index(route)
    end = src.find("\n@app.", start + 10)
    return src[start:end if end != -1 else len(src)]


class Trace:
    """Counts the SQL a connection actually issues.

    Query counts are the honest measure here: a wall-clock assertion would be
    flaky on a loaded machine, while "how many times did this run" is exactly
    the thing that regressed.
    """

    def __init__(self, conn):
        self.conn = conn
        self.seen = Counter()

    def __enter__(self):
        self.conn.set_trace_callback(
            lambda sql: self.seen.update([" ".join(sql.split())]))
        return self

    def __exit__(self, *exc):
        self.conn.set_trace_callback(None)

    @property
    def commits(self):
        return self.seen.get("COMMIT", 0)

    def matching(self, fragment):
        return sum(n for sql, n in self.seen.items() if fragment in sql)


@pytest.fixture
def manager(db_path, db):
    """A manager on the same file the fixture's connection points at."""
    cur = db.cursor()
    cur.execute("INSERT INTO courses (id, name, start_date, end_date, is_active, "
                "created_at) VALUES (1, 'B', '2026-01-01', '2027-12-31', 1, ?)",
                (datetime.now().isoformat(),))
    for i in range(1, 11):
        cur.execute("INSERT INTO students (id, student_id, name, email, status, "
                    "course_id, registration_date) VALUES "
                    "(?, ?, ?, ?, 'active', 1, ?)",
                    (i, f"R{i:03}", f"Student {i}", f"s{i}@x.test",
                     datetime.now().isoformat()))
    db.commit()
    m = AttendanceSlotManager(db_path)
    return m


def mark_a_frame(manager, ids, slot="morning_1", defer=True, cache=True):
    """What a detection handler does for one frame: one deferred transaction,
    and one per-frame cache shared by every face in it."""
    out = []
    frame_cache = {} if cache else None
    for sid in ids:
        out.append(manager.mark_attendance_with_slot(
            student_id=sid, detection_confidence=0.9, force_slot=slot,
            defer_commit=defer, frame_cache=frame_cache))
    if defer:
        manager.conn.commit()
    return out


class TestOneCommitPerFrame:
    def test_six_students_cost_one_commit(self, manager):
        with Trace(manager.conn) as t:
            mark_a_frame(manager, range(1, 7))
        assert t.commits == 1, f"{t.commits} commits for one frame"

    def test_it_does_not_scale_with_the_group(self, manager):
        with Trace(manager.conn) as t:
            mark_a_frame(manager, range(1, 11))
        assert t.commits == 1

    def test_the_single_student_path_still_commits_itself(self, manager):
        """Manual marking and the utility helpers call this one student at a
        time and expect the write to be durable when it returns."""
        with Trace(manager.conn) as t:
            manager.mark_attendance_with_slot(
                student_id=1, force_slot="morning_1")
        assert t.commits == 1

    def test_deferring_really_defers(self, manager, db_path):
        """Not just a flag that is ignored. Read from a second connection,
        which cannot see an uncommitted transaction."""
        manager.mark_attendance_with_slot(
            student_id=1, force_slot="morning_1", defer_commit=True)
        other = sqlite3.connect(db_path)
        try:
            assert other.execute(
                "SELECT COUNT(*) FROM slot_attendance").fetchone()[0] == 0, \
                "the mark was visible before the caller committed"
        finally:
            other.close()
        manager.conn.commit()
        other = sqlite3.connect(db_path)
        try:
            assert other.execute(
                "SELECT COUNT(*) FROM slot_attendance").fetchone()[0] == 1
        finally:
            other.close()


class TestTheRowsAreTheSame:
    """Batching is only worth anything if it writes exactly what it used to."""

    def test_every_student_in_the_frame_is_marked(self, manager, db):
        results = mark_a_frame(manager, range(1, 7))
        assert all(r["success"] for r in results), [
            r["message"] for r in results if not r["success"]]
        assert db.execute(
            "SELECT COUNT(*) FROM slot_attendance").fetchone()[0] == 6
        assert db.execute(
            "SELECT COUNT(*) FROM attendance").fetchone()[0] == 6

    def test_both_tables_are_written(self, manager, db):
        """Manual marking once wrote only slot_attendance, which left students
        absent in every report. The camera path has to write both."""
        mark_a_frame(manager, [3])
        assert db.execute(
            "SELECT COUNT(*) FROM slot_attendance WHERE student_id = 3"
        ).fetchone()[0] == 1
        row = db.execute(
            "SELECT course_id, session_type FROM attendance WHERE student_id = 3"
        ).fetchone()
        assert row == (1, "morning_1"), row

    def test_the_same_frame_twice_marks_nobody_twice(self, manager, db):
        mark_a_frame(manager, range(1, 7))
        again = mark_a_frame(manager, range(1, 7))
        assert not any(r["success"] for r in again)
        assert all(r.get("already_marked") for r in again)
        assert db.execute(
            "SELECT COUNT(*) FROM slot_attendance").fetchone()[0] == 6

    def test_a_student_already_marked_does_not_block_the_rest(self, manager, db):
        """The realistic frame: someone who already marked is standing next to
        someone who has not."""
        mark_a_frame(manager, [2])
        results = mark_a_frame(manager, [1, 2, 3])
        assert [r["success"] for r in results] == [True, False, True]
        assert db.execute(
            "SELECT COUNT(*) FROM slot_attendance").fetchone()[0] == 3


class TestNothingIsHalfWritten:
    def test_a_rollback_discards_the_whole_frame(self, manager, db_path):
        """What the handler does when something throws mid-frame. The group was
        either seen together or it was not."""
        mark_a_frame(manager, [1, 2, 3], defer=False)
        manager.mark_attendance_with_slot(
            student_id=4, force_slot="morning_1", defer_commit=True)
        manager.mark_attendance_with_slot(
            student_id=5, force_slot="morning_1", defer_commit=True)
        manager.conn.rollback()
        other = sqlite3.connect(db_path)
        try:
            rolls = {r[0] for r in other.execute(
                "SELECT student_id FROM slot_attendance").fetchall()}
        finally:
            other.close()
        assert rolls == {1, 2, 3}, "the rolled-back frame left rows behind"

    def test_the_handlers_roll_back_on_an_error(self):
        """Both detection handlers defer their commits, so both need this or a
        failed frame leaves an open transaction for the next request to commit
        by accident."""
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        for route in ('@app.post("/api/detect_attendance")',
                      '@app.post("/api/detect_attendance_slots")'):
            assert "rollback()" in handler_body(src, route), \
                f"{route} never rolls back"

    def test_both_handlers_commit_the_frame(self):
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        for route in ('@app.post("/api/detect_attendance")',
                      '@app.post("/api/detect_attendance_slots")'):
            assert "commit()" in handler_body(src, route), \
                f"{route} never commits"


class TestWorkDoneOncePerBatchNotOncePerFace:
    def test_the_course_is_looked_up_once_per_student_not_three_times(self, manager):
        """mark_attendance_with_slot ran `SELECT course_id FROM students WHERE
        id = ?` three times for the same student in one call: once for the slot
        timings, once inside the working-day check, once before the insert."""
        with Trace(manager.conn) as t:
            manager.mark_attendance_with_slot(
                student_id=1, force_slot="morning_1", defer_commit=True)
        n = t.matching("SELECT course_id FROM students WHERE id")
        assert n <= 1, f"{n} identical course lookups for one student"

    def test_holidays_are_read_once_per_frame_by_the_handler(self):
        """The handler memoises the batch-level lookups, so the holiday table
        is read once per batch rather than once per recognised face. Asserted
        on the structure, since the measurement lives in the handler."""
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        body = handler_body(src, '@app.post("/api/detect_attendance")')
        assert "frame_cache" in body
        assert "if s_course_id not in frame_cache:" in body

    def test_the_gallery_is_resolved_once_per_frame(self):
        """match_gallery() calls tenancy.is_single_tenant(), a COUNT(*) on
        institutes. Resolving it per face meant eight of those for a six-face
        frame."""
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        assert src.count("frame_gallery = attendance_system.match_gallery(") == 2
        assert "best_match(\n                    face_encoding, match_institute_id, frame_gallery)" in src


class TestTheDeadSummaryIsGone:
    """update_daily_summary() ran on every mark -- four COUNT queries and a
    write -- to maintain daily_attendance_summary, which nothing reads. It is
    created, written there, and emptied by clear_all_data; that is its whole
    life. Its counts were also unscoped by institute, so a multi-tenant
    deployment would have had them wrong had anything read them."""

    @staticmethod
    def _sources():
        out = {}
        for name in ("attendance_manager.py", "main_with_face_recognition.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                out[name] = f.read()
        return out

    def test_the_method_is_deleted(self):
        src = self._sources()["attendance_manager.py"]
        assert "def update_daily_summary" not in src

    def test_nothing_calls_it(self):
        for name, src in self._sources().items():
            assert "self.update_daily_summary(" not in src, name
            assert ".update_daily_summary(" not in src, name

    def test_marking_no_longer_touches_the_table(self, manager):
        with Trace(manager.conn) as t:
            mark_a_frame(manager, range(1, 7))
        assert t.matching("daily_attendance_summary") == 0

    def test_the_whole_frame_stays_within_a_sane_statement_count(self, manager):
        """A regression guard with room to breathe, not a golden number.

        Before this change a six-student frame cost five summary queries and
        two duplicate lookups per student on top of the real work, so it ran
        well over sixty statements.
        """
        with Trace(manager.conn) as t:
            mark_a_frame(manager, range(1, 7))
        total = sum(t.seen.values())
        assert total < 45, f"{total} statements for a six-student frame"
