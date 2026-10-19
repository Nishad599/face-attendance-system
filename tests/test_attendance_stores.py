"""Which table attendance actually lives in, and what the slots are called.

Two problems this locks down.

1. There were three overlapping attendance stores: `attendance` (what every
   report, analytic and export reads), `slot_attendance` (one row per slot, the
   table whose UNIQUE constraint was doing all the real dedup work) and
   `session_attendance`. Nothing ever wrote the third one — its only INSERT
   lived in a phase1_integration method that was never attached to
   AttendanceSystem — yet `get_today_attendance()` joined it and therefore
   reported NULL times for every student, forever.

2. Slot display names were built as `f"{session_type.title()} Session"`, which
   renders the raw database key as "Morning_1 Session", underscore included.
   That string was what the attendance terminal, the staff slot banner and
   every "outside your slot" message showed.
"""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_FILE = os.path.join(ROOT, "main_with_face_recognition.py")


def _source(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestSlotDisplayNames:
    """Readable names, shared with the student timetable."""

    def test_known_slots_are_titled_without_underscores(self):
        from attendance_manager import SLOT_DISPLAY_NAMES

        assert SLOT_DISPLAY_NAMES["morning_1"] == "Morning 1"
        assert SLOT_DISPLAY_NAMES["afternoon_2"] == "Afternoon 2"
        for key, label in SLOT_DISPLAY_NAMES.items():
            assert "_" not in label, f"{key} renders as {label!r}"
            assert "Session" not in label, (
                f"{key} renders as {label!r} — the word 'Session' padded every "
                "label without adding meaning")

    def test_labels_match_the_student_timetable(self):
        """One source of truth: the terminal and the timetable must agree."""
        from attendance_manager import SLOT_DISPLAY_NAMES
        from timetable import SLOT_LABELS

        assert SLOT_DISPLAY_NAMES == SLOT_LABELS

    @pytest.mark.parametrize("slot_id,expected", [
        ("evening_1", "Evening 1"),
        ("late_night_2", "Late Night 2"),
        ("morning", "Morning"),
    ])
    def test_unknown_slots_degrade_readably(self, slot_id, expected):
        """An admin can create any session_type; it must not leak the raw key."""
        from attendance_manager import _humanise_slot

        assert _humanise_slot(slot_id) == expected

    def test_loader_uses_the_label_map(self, db):
        """End to end through load_session_configs()."""
        from attendance_manager import AttendanceSlotManager

        cur = db.cursor()
        cur.execute(
            "INSERT INTO courses (id, name, start_date, end_date, is_active, created_at) "
            "VALUES (1, 'TEST-BATCH', '2026-08-24', '2027-12-31', 1, '2026-08-24T00:00:00')")
        for slot, start, end in (("morning_1", "08:30:00", "09:30:00"),
                                 ("afternoon_2", "16:15:00", "16:45:00")):
            cur.execute(
                "INSERT INTO session_configs (course_id, session_type, start_time, "
                "end_time, is_active) VALUES (1, ?, ?, ?, 1)", (slot, start, end))
        db.commit()

        mgr = AttendanceSlotManager.__new__(AttendanceSlotManager)
        mgr.conn = db
        slots = mgr.load_session_configs(course_id=1)

        assert slots["morning_1"]["name"] == "Morning 1"
        assert slots["afternoon_2"]["name"] == "Afternoon 2"


# Any SQL statement that would touch a table, whatever quoting surrounds it.
# Matching the keyword rather than the bare table name is what lets the code
# keep comments explaining why the table was retired.
def _sql_references(source, table):
    pattern = re.compile(
        r"\b(FROM|JOIN|INTO|UPDATE|TABLE|EXISTS)\s+" + re.escape(table) + r"\b",
        re.IGNORECASE,
    )
    return [
        (n, line.strip())
        for n, line in enumerate(source.split("\n"), 1)
        if pattern.search(line)
    ]


class TestSessionAttendanceIsRetired:
    """The third store is gone from the live code paths."""

    def test_app_runs_no_sql_against_session_attendance(self):
        hits = _sql_references(_source(APP_FILE), "session_attendance")
        assert not hits, f"SQL still touches session_attendance: {hits}"

    def test_nothing_in_the_project_writes_session_attendance(self):
        offenders = {}
        for name in sorted(os.listdir(ROOT)):
            if not name.endswith(".py"):
                continue
            hits = _sql_references(_source(os.path.join(ROOT, name)),
                                   "session_attendance")
            writes = [h for h in hits if re.search(r"\b(INTO|UPDATE)\b", h[1], re.I)]
            if writes:
                offenders[name] = writes
        assert not offenders, (
            "a writer for session_attendance came back; either wire it into "
            f"reports or drop the table: {offenders}")

    def test_phase1_integration_is_gone(self):
        """It registered 9 unauthenticated endpoints and shadowed the newer
        attendance_manager + timetable path."""
        assert not os.path.exists(os.path.join(ROOT, "phase1_integration.py"))
        src = _source(APP_FILE)
        assert "from phase1_integration import" not in src
        assert "import phase1_integration" not in src
        assert "add_phase1_api_endpoints(" not in src
        assert "enhance_existing_attendance_system(" not in src


class TestTodayAttendanceReadsThePrimaryTable:
    """`get_today_attendance()` must read `attendance`."""

    def _method_source(self):
        src = _source(APP_FILE)
        start = src.find("def get_today_attendance(self)")
        assert start != -1, "get_today_attendance() not found"
        end = src.find("\n    def ", start + 10)
        return src[start:end if end != -1 else len(src)]

    def test_selects_from_attendance(self):
        body = self._method_source()
        assert re.search(r"FROM\s+students\s+s", body)
        assert re.search(r"JOIN\s+attendance\s+a", body)
        assert not _sql_references(body, "session_attendance")

    def test_exposes_a_first_time_column(self):
        """Presence has to be judged on the earliest mark of the day: a
        whole-day manual or bulk mark carries no session_type, so neither slot
        column would show it."""
        body = self._method_source()
        assert "first_time" in body

    def test_admin_page_counts_presence_on_first_time(self):
        """admin.html filtered on record[2] — the email column, never null —
        so "Present Today" always equalled the total student count."""
        page = _source(os.path.join(ROOT, "templates", "admin.html"))
        idx = page.find("const presentToday")
        assert idx != -1, "presentToday calculation not found in admin.html"
        snippet = page[idx:idx + 260]
        assert "record[5]" in snippet, (
            "presence must be read from first_time (index 5)")
        assert "record[2]" not in snippet, (
            "index 2 is email and is never null — it counts everyone present")
