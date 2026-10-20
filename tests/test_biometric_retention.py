"""The privacy notice promises deletion. This is the code that performs it.

/privacy tells every student "your face data is deleted within N days of your
course ending", and the Settings page prints the same number. Nothing deleted
anything: BIOMETRIC_RETENTION_DAYS was read twice, both times only to display
it. The sole deletion in the system was the manual one when a student
withdraws consent.

So these tests are not really about a script. They are about whether the
sentence on /privacy is true, which is why they check both halves: that what
the notice says goes, goes -- and that what it says is kept, stays. Deleting
the attendance record along with the embedding would be its own breach of the
same notice, and far harder to undo.
"""

import os
import sys
from datetime import date, datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import purge_biometrics as purge                  # noqa: E402

TODAY = date.today()


def ago(days):
    return (TODAY - timedelta(days=days)).strftime("%Y-%m-%d")


def ahead(days):
    return (TODAY + timedelta(days=days)).strftime("%Y-%m-%d")


@pytest.fixture
def world(db):
    """Courses at every interesting position relative to a 90-day window.

        1  ended 200 days ago   past retention      -> purge
        2  ended  10 days ago   inside retention    -> keep
        3  ends in 30 days      still running       -> keep
        4  end_date is ''                          -> keep

    Institute 2 owns course 5, also long over, so the per-institute audit
    rows and the --institute filter are testable.
    """
    cur = db.cursor()
    for iid, name, slug in ((1, "Northgate", "northgate"),
                            (2, "Southpoint", "southpoint")):
        cur.execute("INSERT INTO institutes (id, name, slug, is_active, "
                    "created_at) VALUES (?, ?, ?, 1, ?)",
                    (iid, name, slug, datetime.now().isoformat()))
    courses = (
        (1, 1, "Long Over", ago(400), ago(200)),
        (2, 1, "Just Over", ago(120), ago(10)),
        (3, 1, "Running", ago(30), ahead(30)),
        # courses.end_date is NOT NULL, so the realistic bad-data case is
        # an empty string rather than NULL -- which sorts BELOW any cutoff in
        # a text comparison and would otherwise sweep the whole batch up.
        (4, 1, "Open Ended", ago(400), ""),
        (5, 2, "Other Institute", ago(400), ago(200)),
    )
    for cid, iid, nm, start, end in courses:
        cur.execute("INSERT INTO courses (id, name, start_date, end_date, "
                    "is_active, created_at, institute_id) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (cid, nm, start, end, datetime.now().isoformat(), iid))
    # One student per course, each holding an embedding and a photo count.
    for sid, cid in enumerate((1, 2, 3, 4, 5), start=1):
        cur.execute(
            "INSERT INTO students (id, student_id, name, email, status, "
            "course_id, registration_date, face_encoding, photo_count) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, ?, 3)",
            (sid, f"R{sid:03}", f"Student {sid}", f"s{sid}@x.test", cid,
             datetime.now().isoformat(), b"\x01" * 4096))
        cur.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, "
            "course_id, session_type) VALUES "
            "(?, ?, '09:00:00', 'present', ?, 'morning_1')",
            (sid, ago(300), cid))
        cur.execute(
            "INSERT INTO consent_records (student_id, purpose, policy_version, "
            "granted, granted_at) VALUES (?, 'biometric_attendance', '1.0', 1, ?)",
            (sid, datetime.now().isoformat()))
    db.commit()
    return db


def rolls(rows):
    return {r[1] for r in rows}


class TestWhoIsDue:
    def test_only_courses_past_the_window(self, world):
        _cutoff, rows = purge.due_for_purge(world, 90)
        assert rolls(rows) == {"R001", "R005"}, (
            "expected only the two long-finished courses")

    def test_a_course_inside_the_window_is_not_touched(self, world):
        _c, rows = purge.due_for_purge(world, 90)
        assert "R002" not in rolls(rows), "10 days past the end is not 90"

    def test_a_running_course_is_never_touched(self, world):
        _c, rows = purge.due_for_purge(world, 90)
        assert "R003" not in rolls(rows)

    def test_a_course_with_a_blank_end_date_is_never_touched(self, world):
        """However old the enrolment. The notice ties deletion to the course
        ending, so a course with no usable end date has no clock running.

        This is the dangerous one: '' < '2026-07-22' is true in SQLite's text
        comparison, so a cutoff test written without the `end_date != ''`
        guard quietly purges every such batch on the first run.
        """
        _c, rows = purge.due_for_purge(world, 90)
        assert "R004" not in rolls(rows)

    def test_the_window_is_configurable(self, world):
        _c, rows = purge.due_for_purge(world, 5)
        assert "R002" in rolls(rows), "at 5 days, the 10-day-old course is due"

    def test_one_institute_can_be_purged_alone(self, world):
        _c, rows = purge.due_for_purge(world, 90, institute_id=2)
        assert rolls(rows) == {"R005"}

    def test_a_student_with_nothing_left_is_not_listed(self, world):
        """Idempotence at the query level, so a second run is a no-op rather
        than a pile of pointless UPDATEs."""
        world.execute("UPDATE students SET face_encoding = NULL, photo_count = 0 "
                      "WHERE student_id = 'R001'")
        world.commit()
        _c, rows = purge.due_for_purge(world, 90)
        assert "R001" not in rolls(rows)

    def test_photos_alone_still_count_as_biometric_data(self, world):
        """A consent withdrawal clears the encoding but historically left the
        JPEGs on disk. Those are face data too, so the row must still come up
        for purge."""
        world.execute("UPDATE students SET face_encoding = NULL "
                      "WHERE student_id = 'R001'")
        world.commit()
        _c, rows = purge.due_for_purge(world, 90)
        assert "R001" in rolls(rows)


class TestWhatThePurgeDoes:
    def _run(self, world, **kw):
        _c, rows = purge.due_for_purge(world, kw.pop("days", 90), kw.pop("institute_id", None))
        return purge.purge(world, rows, **kw)

    def test_the_embedding_goes(self, world):
        self._run(world)
        row = world.execute(
            "SELECT face_encoding, photo_count FROM students "
            "WHERE student_id = 'R001'").fetchone()
        assert row[0] is None
        assert row[1] == 0

    def test_the_attendance_record_stays(self, world):
        """The notice is explicit that it does: "part of your academic
        record". Deleting it would be its own breach, and unrecoverable."""
        self._run(world)
        assert world.execute(
            "SELECT COUNT(*) FROM attendance WHERE student_id = 1").fetchone()[0] == 1

    def test_the_consent_record_stays(self, world):
        """It is the proof of what was agreed and when -- the first thing
        anyone asks for, and the last thing to delete."""
        self._run(world)
        assert world.execute(
            "SELECT COUNT(*) FROM consent_records WHERE student_id = 1").fetchone()[0] == 1

    def test_the_student_stays(self, world):
        self._run(world)
        assert world.execute(
            "SELECT name FROM students WHERE student_id = 'R001'").fetchone()[0]

    def test_students_inside_the_window_are_untouched(self, world):
        self._run(world)
        for roll in ("R002", "R003", "R004"):
            enc = world.execute(
                "SELECT face_encoding FROM students WHERE student_id = ?",
                (roll,)).fetchone()[0]
            assert enc is not None, f"{roll} was purged and should not have been"

    def test_dry_run_changes_nothing(self, world):
        self._run(world, dry_run=True)
        assert world.execute(
            "SELECT face_encoding FROM students "
            "WHERE student_id = 'R001'").fetchone()[0] is not None

    def test_running_twice_is_a_no_op(self, world):
        first = self._run(world)
        second = self._run(world)
        assert first[0] == 2
        assert second[0] == 0, "a cron that fires twice must not do work twice"

    def test_each_institute_gets_its_own_audit_row(self, world):
        """The audit log is read scoped, so one row with a NULL institute_id
        is a platform entry only the superadmin sees -- and the institute
        whose students were purged would have no record of it at all."""
        self._run(world)
        rows = world.execute(
            "SELECT institute_id, target FROM audit_log "
            "WHERE action = 'purge_biometrics' ORDER BY institute_id").fetchall()
        assert [r[0] for r in rows] == [1, 2]
        assert all("student" in r[1] for r in rows)


class TestThePhotoDirectory:
    def test_it_is_removed(self, world, tmp_path, monkeypatch):
        monkeypatch.setattr(purge, "PHOTO_ROOT", str(tmp_path / "student_photos"))
        d = purge.photo_dir_for("R001", "Student 1")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "photo_1.jpg"), "wb") as f:
            f.write(b"jpeg")
        _c, rows = purge.due_for_purge(world, 90)
        cleared, removed = purge.purge(world, rows)
        assert removed == 1
        assert not os.path.isdir(d)

    def test_dry_run_does_not_create_it(self, world, tmp_path, monkeypatch):
        """photo_utils.create_student_photo_directory() makes the directory as
        a side effect, so calling it from a deletion script would leave new
        empty folders behind on a --dry-run. The path is rebuilt instead."""
        root = tmp_path / "student_photos"
        monkeypatch.setattr(purge, "PHOTO_ROOT", str(root))
        purge.photo_dir_for("R001", "Student 1")
        assert not root.exists()

    def test_the_path_matches_what_enrolment_wrote(self, tmp_path, monkeypatch):
        """If these two ever disagree, the purge silently leaves the JPEGs
        behind while the database says the data is gone -- the worst outcome,
        because nothing looks wrong."""
        import photo_utils
        monkeypatch.chdir(tmp_path)
        written = photo_utils.create_student_photo_directory("R001", "Priya O'Brien-Rao")
        monkeypatch.setattr(purge, "PHOTO_ROOT", "student_photos")
        assert purge.photo_dir_for("R001", "Priya O'Brien-Rao") == written


class TestTheCliRefusesSomething:
    def test_zero_days_is_refused(self, world, monkeypatch):
        """--days 0 would purge every finished course the moment it ends.
        A typo should not be able to do that."""
        monkeypatch.setattr(purge, "get_connection", lambda *a, **k: world)
        assert purge.main(["--days", "0"]) == 2

    def test_a_negative_window_is_refused(self, world, monkeypatch):
        monkeypatch.setattr(purge, "get_connection", lambda *a, **k: world)
        assert purge.main(["--days", "-30"]) == 2

    def test_a_normal_dry_run_succeeds_and_changes_nothing(self, world, monkeypatch):
        monkeypatch.setattr(purge, "get_connection", lambda *a, **k: world)
        assert purge.main(["--dry-run"]) == 0
        assert world.execute(
            "SELECT face_encoding FROM students "
            "WHERE student_id = 'R001'").fetchone()[0] is not None


class TestThePromiseIsStillTheOneWeKeep:
    def test_the_notice_and_the_script_read_the_same_variable(self):
        """If someone changes the window, both the sentence students read and
        the job that enforces it have to move together."""
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            app_src = f.read()
        with open(os.path.join(ROOT, "purge_biometrics.py"), encoding="utf-8") as f:
            purge_src = f.read()
        assert 'BIOMETRIC_RETENTION_DAYS' in app_src
        assert 'getenv("BIOMETRIC_RETENTION_DAYS"' in purge_src

    def test_the_cron_is_documented(self):
        """An unscheduled purge script keeps no promise at all."""
        with open(os.path.join(ROOT, "deploy", "DEPLOYMENT.md"),
                  encoding="utf-8") as f:
            doc = f.read()
        assert "purge_biometrics.py" in doc, (
            "the purge is not in the cron list, so it will never run")
