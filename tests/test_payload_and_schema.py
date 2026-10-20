"""Payload size, pagination, and the tables that should not exist.

Five cleanups, all of them things that were fine at one institute of sixty
and wrong at a few thousand across ten:

  * nothing was compressed, anywhere
  * the roster returned every student, always, with no ceiling
  * the batch roster ran one query per student
  * slot_attendance had no index, while being written on every mark
  * three tables were created on every startup and never read

The pagination tests are the ones worth reading. Paginating a list whose page
filters it in the browser is a trap: search would have quietly covered only
the page you were on, which looks like working software and silently fails to
find people. So the page keeps client-side filtering while the whole roster
fits in one response and hands search to the server once it does not, and the
tests check the handover rather than just the limit arithmetic.
"""

import os
import sqlite3
import sys
from datetime import datetime

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


def run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


@needs_app
class TestCompression:
    """Measured against the running app: /login 74% smaller, app.css 83%,
    and a 500-row roster page 96%. The deployment serves these over a campus
    LAN to terminals and phones."""

    def test_gzip_is_installed(self):
        from fastapi.middleware.gzip import GZipMiddleware
        assert any(m.cls is GZipMiddleware for m in _app.app.user_middleware), \
            "no compression middleware"

    def test_small_responses_are_left_alone(self):
        """The terminal polls /api/attendance/live-count every 10 seconds. On
        a response of a few hundred bytes the gzip header and the CPU cost
        exceed anything saved.

        Read off the source rather than the middleware object, whose kwargs
        Starlette keeps differently across versions.
        """
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        import re
        m = re.search(r"GZipMiddleware,\s*minimum_size=(\d+)", src)
        assert m, "GZipMiddleware is added without a minimum_size"
        assert int(m.group(1)) >= 500, f"minimum_size is {m.group(1)}"


@needs_app
class TestTheRosterIsBounded:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def big(self, db):
        """1,400 students across three batches at one institute."""
        cur = db.cursor()
        cur.execute("INSERT INTO institutes (id, name, slug, is_active, created_at) "
                    "VALUES (1, 'I', 'i', 1, ?)", (datetime.now().isoformat(),))
        for cid in (1, 2, 3):
            cur.execute("INSERT INTO courses (id, name, start_date, end_date, "
                        "is_active, created_at, institute_id) VALUES "
                        "(?, ?, '2026-01-01', '2027-12-31', 1, ?, 1)",
                        (cid, f"BATCH-{cid}", datetime.now().isoformat()))
        for i in range(1, 1401):
            cur.execute(
                "INSERT INTO students (id, student_id, name, email, status, "
                "course_id, registration_date) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (i, f"R{i:05}", f"Student {i:05}", f"s{i}@x.test",
                 1 + i % 3, datetime.now().isoformat()))
        db.commit()
        _app.attendance_system.conn = db
        return db

    def test_one_request_cannot_return_everything(self, big):
        d = run(_app.list_students(session=session_for("admin", 1)))
        assert d["success"] is True
        assert len(d["students"]) == 500
        assert d["total"] == 1400
        assert d["has_more"] is True

    def test_a_caller_asking_for_a_million_gets_a_thousand(self, big):
        """The ceiling is not negotiable from the query string."""
        d = run(_app.list_students(limit=1000000, session=session_for("admin", 1)))
        assert len(d["students"]) == 1000

    def test_a_nonsense_limit_does_not_crash(self, big):
        for bad in (0, -5):
            d = run(_app.list_students(limit=bad, session=session_for("admin", 1)))
            assert d["success"] is True
            assert len(d["students"]) >= 1

    def test_offset_walks_the_whole_roster_without_gaps(self, big):
        """The failure this guards against is a page boundary that drops or
        repeats someone, which nobody notices until a student is missing from
        a roster."""
        seen, offset = [], 0
        while True:
            d = run(_app.list_students(limit=500, offset=offset,
                                       session=session_for("admin", 1)))
            seen.extend(s["student_id"] for s in d["students"])
            if not d["has_more"]:
                break
            offset += len(d["students"])
        assert len(seen) == 1400
        assert len(set(seen)) == 1400, "a page boundary repeated someone"

    def test_pages_are_ordered_consistently(self, big):
        """Without a stable ORDER BY, offset pagination reshuffles between
        requests and the walk above would be luck."""
        first = run(_app.list_students(limit=10, session=session_for("admin", 1)))
        again = run(_app.list_students(limit=10, session=session_for("admin", 1)))
        assert [s["id"] for s in first["students"]] == [s["id"] for s in again["students"]]
        names = [s["name"] for s in first["students"]]
        assert names == sorted(names)

    def test_has_more_is_false_on_the_last_page(self, big):
        d = run(_app.list_students(limit=500, offset=1000,
                                   session=session_for("admin", 1)))
        assert len(d["students"]) == 400
        assert d["has_more"] is False

    def test_a_small_roster_still_arrives_in_one_response(self, db, seeded):
        """The common case must not regress into paging."""
        _app.attendance_system.conn = db
        d = run(_app.list_students(session=session_for("admin", 1)))
        assert d["has_more"] is False
        assert d["total"] == len(d["students"])


@needs_app
class TestSearchMovedToTheServerWithIt:
    """The half that makes pagination safe."""

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def big(self, db, request):
        cur = db.cursor()
        cur.execute("INSERT INTO institutes (id, name, slug, is_active, created_at) "
                    "VALUES (1, 'I', 'i', 1, ?)", (datetime.now().isoformat(),))
        for cid in (1, 2):
            cur.execute("INSERT INTO courses (id, name, start_date, end_date, "
                        "is_active, created_at, institute_id) VALUES "
                        "(?, ?, '2026-01-01', '2027-12-31', 1, ?, 1)",
                        (cid, f"BATCH-{cid}", datetime.now().isoformat()))
        for i in range(1, 1401):
            cur.execute(
                "INSERT INTO students (id, student_id, name, email, status, "
                "course_id, registration_date) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (i, f"R{i:05}", f"Student {i:05}", f"s{i}@x.test",
                 1 + i % 2, datetime.now().isoformat()))
        db.commit()
        _app.attendance_system.conn = db
        return db

    def test_it_finds_someone_past_the_first_page(self, big):
        """The exact bug naive pagination would have shipped: student 01399 is
        nowhere in the first 500 rows, so a browser-side search over what it
        had been given would report "no students match"."""
        d = run(_app.list_students(q="Student 01399",
                                   session=session_for("admin", 1)))
        assert d["total"] == 1
        assert d["students"][0]["student_id"] == "R01399"

    def test_it_matches_a_roll_number_too(self, big):
        d = run(_app.list_students(q="R01250", session=session_for("admin", 1)))
        assert [s["student_id"] for s in d["students"]] == ["R01250"]

    def test_it_is_case_insensitive(self, big):
        lower = run(_app.list_students(q="student 00007", session=session_for("admin", 1)))
        upper = run(_app.list_students(q="STUDENT 00007", session=session_for("admin", 1)))
        assert lower["total"] == upper["total"] == 1

    def test_total_reflects_the_filter_not_the_roster(self, big):
        """The page prints "n of total"; a total that ignored the filter would
        make it nonsense."""
        d = run(_app.list_students(q="Student 001", session=session_for("admin", 1)))
        expected = big.execute(
            "SELECT COUNT(*) FROM students WHERE LOWER(name) LIKE '%student 001%'"
        ).fetchone()[0]
        assert expected > 0, "the fixture matched nothing; the test proves nothing"
        assert d["total"] == expected
        assert d["total"] < 1400

    def test_a_wildcard_in_the_term_is_not_a_wildcard(self, big):
        """LIKE treats % and _ as patterns. A search for '%' must not return
        the whole roster as though the box were empty."""
        d = run(_app.list_students(q="%", session=session_for("admin", 1)))
        assert d["total"] < 1400, "a bare % matched everything"

    def test_the_batch_filter_is_scoped_not_just_filtered(self, big, db):
        """course_id comes from the query string, so it is an id a caller can
        put anything in."""
        d = run(_app.list_students(course_id=1, session=session_for("admin", 1)))
        assert d["total"] == 700
        assert all(s["course_id"] == 1 for s in d["students"])

    def test_another_institutes_batch_is_refused(self, db, two_institutes):
        _app.attendance_system.conn = db
        try:
            run(_app.list_students(course_id=2, session=session_for("admin", 1)))
        except _app.HTTPException as e:
            assert e.status_code == 403
        else:
            pytest.fail("served another institute's batch by id")

    def test_search_cannot_reach_across_institutes(self, db, two_institutes):
        """Search is a new way into the table, so it needs the same scope as
        the unfiltered list."""
        _app.attendance_system.conn = db
        d = run(_app.list_students(q="SP001", session=session_for("admin", 1)))
        assert d["total"] == 0, "search leaked another institute's student"


class TestThePageHandsOverCleanly:
    """Asserted on the page because the handover lives there. Verified in a
    browser at both scales: 40 students stayed client-side with zero extra
    requests, 1,400 switched to the server, found student 01399 (not on the
    loaded page), debounced five keystrokes into one request, and loaded all
    1,400 with no duplicates."""

    @staticmethod
    def _page():
        with open(os.path.join(ROOT, "templates", "people.html"),
                  encoding="utf-8") as f:
            return f.read()

    def test_it_knows_when_the_server_owns_filtering(self):
        page = self._page()
        assert "SERVER_SIDE" in page
        assert "if (d.has_more) SERVER_SIDE = true;" in page

    def test_typing_is_debounced_only_when_it_costs_a_request(self):
        page = self._page()
        assert "function onSearchInput()" in page
        assert "if (!SERVER_SIDE) { renderStudents(); return; }" in page
        assert "setTimeout" in page

    def test_the_batch_list_does_not_come_from_the_students(self):
        """Deriving it from the roster meant a paginated page offered only the
        batches that happened to appear on it, and a batch with no students
        yet never appeared at all."""
        page = self._page()
        assert "loadBatchFilter" in page
        assert "'/api/courses'" in page

    def test_there_is_a_way_to_reach_the_later_pages(self):
        page = self._page()
        assert 'id="moreBtn"' in page
        assert "function loadMore()" in page
        assert "{append: true}" in page

    def test_load_more_appends_rather_than_replacing(self):
        page = self._page()
        assert "STUDENTS.concat(" in page


@needs_app
class TestTheRosterIsNotOneQueryPerStudent:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def test_todays_marks_come_in_one_query(self, db, seeded):
        """It ran a SELECT against `attendance` per row, so a 60-student
        roster cost 61 round trips -- paid on every visit to the teacher
        portal's main page."""
        from collections import Counter
        _app.attendance_system.conn = db
        seen = Counter()
        db.set_trace_callback(lambda sql: seen.update([" ".join(sql.split())]))
        try:
            run(_app.teacher_batch_students(1, session=session_for("admin", 1)))
        finally:
            db.set_trace_callback(None)
        per_student = sum(n for sql, n in seen.items()
                          if "FROM attendance" in sql and "student_id = ?" in sql)
        assert per_student == 0, f"{per_student} per-student attendance queries"

    def test_it_still_reports_the_right_people_as_present(self, db, seeded):
        """The rewrite is only worth anything if the answer is unchanged."""
        _app.attendance_system.conn = db
        d = run(_app.teacher_batch_students(1, session=session_for("admin", 1)))
        assert d["success"] is True
        assert d["students"], "no students returned"
        for s in d["students"]:
            marks = db.execute(
                "SELECT COUNT(*) FROM attendance WHERE student_id = ? AND date = ?",
                (s["id"], d.get("date"))).fetchone()[0]
            assert s["present"] == (marks > 0), f"{s['student_id']} disagrees"

    def test_a_mark_with_no_session_type_still_counts_as_present(self, db, seeded):
        """A whole-day manual mark carries no slot. Reading presence from the
        emptiness of the slot list would turn those students absent."""
        _app.attendance_system.conn = db
        # A date the `seeded` fixture cannot have marked, or the
        # dedupe index rejects the insert.
        the_date = "2027-06-14"
        db.execute("INSERT INTO attendance (student_id, date, time_in, status, "
                   "course_id, session_type) VALUES (1, ?, '09:00:00', 'present', 1, NULL)",
                   (the_date,))
        db.commit()
        d = run(_app.teacher_batch_students(1, date=the_date,
                                        session=session_for("admin", 1)))
        row = next(s for s in d["students"] if s["id"] == 1)
        assert row["present"] is True
        assert row["sessions"] == []

    def test_a_mark_with_a_stale_course_id_still_counts(self, db, seeded):
        """Attendance rows can carry a NULL or stale course_id -- written
        before that column was populated, or left behind by a batch move. A
        first draft of this narrowed the lookup by course_id and would have
        turned those students absent."""
        _app.attendance_system.conn = db
        the_date = "2027-06-15"
        db.execute("INSERT INTO attendance (student_id, date, time_in, status, "
                   "course_id, session_type) VALUES (1, ?, '09:00:00', 'present', NULL, 'morning_1')",
                   (the_date,))
        db.commit()
        d = run(_app.teacher_batch_students(1, date=the_date,
                                        session=session_for("admin", 1)))
        row = next(s for s in d["students"] if s["id"] == 1)
        assert row["present"] is True, "a NULL course_id turned the student absent"


class TestTheSchemaCleanup:
    @staticmethod
    def _sources():
        out = {}
        for name in ("main_with_face_recognition.py", "attendance_manager.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                out[name] = f.read()
        return out

    @pytest.mark.parametrize("table", [
        "session_attendance", "daily_attendance_summary", "course_settings"])
    def test_the_dead_tables_are_not_re_created_on_startup(self, table):
        """Dropping them in a migration achieves nothing while startup brings
        them straight back."""
        for name, src in self._sources().items():
            assert f"CREATE TABLE IF NOT EXISTS {table}" not in src, name

    @pytest.mark.parametrize("table", [
        "session_attendance", "daily_attendance_summary", "course_settings"])
    def test_nothing_queries_them(self, table):
        import re
        for name, src in self._sources().items():
            hit = re.search(r"(FROM|INTO|UPDATE|JOIN)\s+" + table + r"\b",
                            src, re.I)
            assert hit is None, f"{name} still queries {table}: {hit.group(0)}"

    def test_the_migration_refuses_a_table_with_rows_in_it(self):
        """Zero rows on a dev machine says nothing about the VM, and a DROP is
        not somewhere to be clever."""
        import migrate_phase13 as m
        src_path = os.path.join(ROOT, "migrate_phase13.py")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        assert "[STOP]" in src
        assert "NOT dropping" in src
        # and prove it, rather than trusting the string
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE course_settings (id INTEGER)")
        conn.execute("INSERT INTO course_settings VALUES (1)")
        conn.commit()
        assert m.row_count(conn, "course_settings") == 1

    def test_live_tables_were_not_mistaken_for_dead_ones(self):
        """face_encodings and registration_sessions look just as empty and are
        both live -- per-photo encodings and the in-progress enrolment flow.
        An earlier pass of this audit had them on the drop list on the
        strength of a row count."""
        import migrate_phase13 as m
        for live in ("face_encodings", "registration_sessions", "slot_attendance",
                     "attendance", "students", "institutes"):
            assert live not in m.DROP, f"{live} is live and must not be dropped"

    def test_clear_all_data_no_longer_deletes_a_dropped_table(self):
        """The DELETE would throw and take the whole wipe down with it."""
        src = self._sources()["main_with_face_recognition.py"]
        start = src.index('@app.post("/api/admin/clear_all_data")')
        end = src.find("\n@app.", start + 10)
        body = src[start:end if end != -1 else len(src)]
        assert "DELETE FROM daily_attendance_summary" not in body


class TestSlotAttendanceIsIndexed:
    """It had no index at all, while being written on every mark and read by
    every report and export."""

    def test_the_migration_covers_the_three_access_patterns(self):
        import migrate_phase13 as m
        cols = {cols for _n, table, cols in m.INDEXES
                if table == "slot_attendance"}
        assert "(student_id, date)" in cols, "the already-marked check"
        assert "(date, slot_id)" in cols, "the per-slot counts"
        assert "(course_id, date)" in cols, "the batch roster views"

    def test_check_db_treats_them_as_required(self):
        import check_db
        required = check_db.REQUIRED_INDEXES
        assert "idx_slot_attendance_student_date" in required
        assert required["idx_slot_attendance_student_date"] == "slot_attendance"

    def test_the_migration_runs_on_deploy(self):
        with open(os.path.join(ROOT, ".github", "workflows", "deploy.yml"),
                  encoding="utf-8") as f:
            assert "migrate_phase13.py" in f.read(), \
                "the migration will never run on the VM"

    def test_it_skips_an_index_whose_column_is_missing(self):
        """A CREATE INDEX on a column that does not exist fails the whole
        migration run. A first draft indexed email_log(student_id); the table
        has no such column, and this guard is what caught it."""
        import migrate_phase13 as m
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE email_log (id INTEGER, status TEXT)")
        assert m.columns_of(conn, "email_log") == {"id", "status"}
