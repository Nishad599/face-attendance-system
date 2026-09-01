"""Academic calendar: module/event timeline and the CSV importer (Phase 7).

Modules and events are kept in separate tables on purpose — only a module can
have attendance attributed to it, so "BATCH PICNIC" must never become an
attendance module — but students read them as one schedule.
"""
import csv
import importlib.util
import os
from datetime import date, timedelta

import pytest

import timetable as tt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "import_calendar", os.path.join(ROOT, "import_calendar.py"))
importer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(importer)


class TestParseDate:

    @pytest.mark.parametrize("raw,expected", [
        ("2026-08-17", date(2026, 8, 17)),
        ("17 August 2026", date(2026, 8, 17)),
        ("17 Aug 2026", date(2026, 8, 17)),
    ])
    def test_accepted_formats(self, raw, expected):
        assert importer.parse_date(raw) == expected

    def test_us_style_exam_dates(self):
        """The source schedule writes exam dates M/D/YYYY — 9/7/2026 is the
        7th of September, verified against the module that ends 4 September."""
        assert importer.parse_date("9/7/2026") == date(2026, 9, 7)
        assert importer.parse_date("11/13/2026") == date(2026, 11, 13)

    def test_blank_and_rubbish(self):
        assert importer.parse_date("") is None
        assert importer.parse_date(None) is None
        assert importer.parse_date("not a date") is None


class TestClassify:

    @pytest.mark.parametrize("title,kind", [
        ("Centralised Course-End Exams (CCEE)", "exam"),
        ("Course Mid Centralised Exams (CMCE)", "exam"),
        ("Revision Days", "revision"),
        ("MOCK INTERVIEWS - I", "interview"),
        ("Common Campus Placements (CCPP) Begin", "placement"),
        ("COURSE INAUGURATION & INDUCTION PROGRAMME", "induction"),
        ("CONVOCATION & FAREWELL", "induction"),
        ("BATCH PICNIC", "activity"),
        ("SPORTS & TEAM BUILDING", "activity"),
        ("Something Else Entirely", "event"),
    ])
    def test_events_are_grouped_for_colour_coding(self, title, kind):
        assert importer.classify(title) == kind


@pytest.fixture
def calendar_csv(tmp_path):
    """A miniature calendar covering every row type."""
    path = tmp_path / "cal.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["type", "seq", "title", "start_date", "end_date", "hours",
                    "faculty", "days", "exam_date", "coordinator"])
        w.writerow(["module", "0", "Logic Building", "2026-08-17", "2026-08-21",
                    "30 hrs", "Sahil Karande", "4", "", "Vineeta"])
        w.writerow(["module", "1", "Python & R", "2026-08-24", "2026-09-04",
                    "120 hrs", "Dr. Amar Panchal", "12", "2026-09-07", "Sahil"])
        w.writerow(["event", "", "BATCH PICNIC", "2026-11-21", "", "", "", "1", "", "Sahil"])
        w.writerow(["holiday", "", "Deepawali", "2026-11-07", "2026-11-08",
                    "", "", "", "", ""])
        w.writerow(["nonsense", "", "Ignored", "2026-01-01", "", "", "", "", "", ""])
    return str(path)


@pytest.fixture
def importable(seeded, db, monkeypatch, db_path):
    """Point the importer's get_connection at the throwaway database."""
    import sqlite3
    monkeypatch.setattr(importer, "get_connection",
                        lambda *a, **k: sqlite3.connect(db_path))
    return db_path


class TestImporter:

    def _run(self, csv_path, argv_extra=()):
        import sys
        argv = ["import_calendar.py", "--batch", "1", "--file", csv_path, *argv_extra]
        old = sys.argv
        sys.argv = argv
        try:
            importer.main()
        finally:
            sys.argv = old

    def test_imports_modules_events_and_holidays(self, calendar_csv, importable, db, capsys):
        self._run(calendar_csv)
        assert db.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM academic_events").fetchone()[0] == 1
        # Deepawali spans two dates -> two holiday rows
        assert db.execute("SELECT COUNT(*) FROM holidays").fetchone()[0] == 2

    def test_events_never_become_subjects(self, calendar_csv, importable, db):
        """The whole reason events live in their own table."""
        self._run(calendar_csv)
        names = [r[0] for r in db.execute("SELECT name FROM subjects").fetchall()]
        assert "BATCH PICNIC" not in names

    def test_module_detail_is_stored(self, calendar_csv, importable, db):
        self._run(calendar_csv)
        row = db.execute(
            "SELECT sequence, faculty, hours, teaching_days, exam_date, coordinator "
            "FROM subjects WHERE name = 'Python & R'").fetchone()
        assert row[0] == 1
        assert row[1] == "Dr. Amar Panchal"
        assert row[2] == "120 hrs"
        assert row[3] == 12
        assert str(row[4])[:10] == "2026-09-07"
        assert row[5] == "Sahil"

    def test_unknown_row_type_is_skipped_not_fatal(self, calendar_csv, importable, db, capsys):
        self._run(calendar_csv)
        assert "unknown type" in capsys.readouterr().out

    def test_reimport_updates_rather_than_duplicates(self, calendar_csv, importable, db):
        """A corrected calendar must be re-importable."""
        self._run(calendar_csv)
        self._run(calendar_csv)
        assert db.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM academic_events").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM holidays").fetchone()[0] == 2

    def test_dry_run_writes_nothing(self, calendar_csv, importable, db):
        self._run(calendar_csv, ("--dry-run",))
        assert db.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM academic_events").fetchone()[0] == 0

    def test_batch_window_widens_to_cover_the_calendar(self, calendar_csv, importable, db):
        """Figures are computed inside the course window, so a calendar
        extending past the old end date must push it out."""
        db.execute("UPDATE courses SET start_date='2026-08-17', end_date='2026-08-31' WHERE id=1")
        db.commit()
        self._run(calendar_csv)
        end = db.execute("SELECT end_date FROM courses WHERE id=1").fetchone()[0]
        assert str(end)[:10] >= "2026-11-21"

    def test_clash_detection_flags_an_exam_on_a_holiday(self, importable, db, tmp_path, capsys):
        """The real schedule puts the Java exam on Dussehra. Nobody notices
        that in a spreadsheet."""
        path = tmp_path / "clash.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["type", "seq", "title", "start_date", "end_date", "hours",
                        "faculty", "days", "exam_date", "coordinator"])
            w.writerow(["module", "4", "Java", "2026-10-07", "2026-10-19", "", "", "",
                        "2026-10-20", ""])
            w.writerow(["holiday", "", "Dussehra", "2026-10-20", "", "", "", "", "", ""])
        self._run(str(path))
        out = capsys.readouterr().out
        assert "falls on a holiday" in out
        assert "Dussehra" in out

    def test_clash_detection_flags_an_exam_on_a_sunday(self, importable, db, tmp_path, capsys):
        path = tmp_path / "sun.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["type", "seq", "title", "start_date", "end_date", "hours",
                        "faculty", "days", "exam_date", "coordinator"])
            # 2027-01-03 is a Sunday
            w.writerow(["module", "7", "ML", "2026-12-08", "2026-12-31", "", "", "",
                        "2027-01-03", ""])
        self._run(str(path))
        assert "falls on a Sunday" in capsys.readouterr().out


class TestCourseCalendar:

    def _seed(self, db, today):
        db.execute(
            "INSERT INTO subjects (course_id, name, sequence, start_date, end_date, is_active) "
            "VALUES (1, 'Past Module', 0, ?, ?, 1)",
            ((today - timedelta(days=40)).isoformat(), (today - timedelta(days=30)).isoformat()))
        db.execute(
            "INSERT INTO subjects (course_id, name, sequence, start_date, end_date, is_active) "
            "VALUES (1, 'Current Module', 1, ?, ?, 1)",
            ((today - timedelta(days=5)).isoformat(), (today + timedelta(days=5)).isoformat()))
        db.execute(
            "INSERT INTO subjects (course_id, name, sequence, start_date, end_date, is_active) "
            "VALUES (1, 'Future Module', 2, ?, ?, 1)",
            ((today + timedelta(days=20)).isoformat(), (today + timedelta(days=30)).isoformat()))
        db.execute(
            "INSERT INTO academic_events (course_id, title, kind, start_date, end_date) "
            "VALUES (1, 'Picnic', 'activity', ?, ?)",
            ((today + timedelta(days=10)).isoformat(), (today + timedelta(days=10)).isoformat()))
        db.commit()

    def test_merges_modules_and_events_in_date_order(self, seeded, db):
        today = date.today()
        self._seed(db, today)
        items = tt.course_calendar(db, 1, today)
        titles = [i["title"] for i in items]
        assert titles == ["Past Module", "Current Module", "Picnic", "Future Module"]

    def test_status_is_derived_from_today(self, seeded, db):
        today = date.today()
        self._seed(db, today)
        status = {i["title"]: i["status"] for i in tt.course_calendar(db, 1, today)}
        assert status["Past Module"] == "done"
        assert status["Current Module"] == "current"
        assert status["Future Module"] == "upcoming"
        assert status["Picnic"] == "upcoming"

    def test_type_distinguishes_modules_from_events(self, seeded, db):
        today = date.today()
        self._seed(db, today)
        by_title = {i["title"]: i for i in tt.course_calendar(db, 1, today)}
        assert by_title["Current Module"]["type"] == "module"
        assert by_title["Picnic"]["type"] == "event"
        assert by_title["Picnic"]["kind"] == "activity"

    def test_inactive_modules_are_hidden(self, seeded, db):
        today = date.today()
        self._seed(db, today)
        db.execute("UPDATE subjects SET is_active = 0 WHERE name = 'Current Module'")
        db.commit()
        titles = [i["title"] for i in tt.course_calendar(db, 1, today)]
        assert "Current Module" not in titles

    def test_empty_batch_returns_empty(self, seeded, db):
        assert tt.course_calendar(db, 1) == []

    def test_single_day_event_is_current_on_the_day(self, seeded, db):
        today = date.today()
        db.execute(
            "INSERT INTO academic_events (course_id, title, kind, start_date, end_date) "
            "VALUES (1, 'Convocation', 'induction', ?, ?)",
            (today.isoformat(), today.isoformat()))
        db.commit()
        item = tt.course_calendar(db, 1, today)[0]
        assert item["status"] == "current"
