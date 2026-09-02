"""A terminal sees exactly the batches it unlocked - no more, no fewer.

Two things are being protected here:

  * a single-batch terminal must not see another batch's students (the bug
    where every kiosk showed the site-wide headcount); and
  * a combined terminal, which covers several batches from one camera, must
    require the PIN of *every* batch it covers. Otherwise holding one batch's
    PIN would be a way to read another batch's roll.
"""

import re
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_FILE = os.path.join(ROOT, "main_with_face_recognition.py")


# --------------------------------------------------------------------------
# scope helper
# --------------------------------------------------------------------------

def _terminal_course_ids(session):
    """Mirror of the app helper, exercised without importing the whole app
    (which needs the face-recognition model present)."""
    src = open(APP_FILE, encoding="utf-8").read()
    start = src.index("def terminal_course_ids(")
    end = src.index("def teacher_allowed_course_ids(")
    ns = {"Dict": dict, "Any": object, "List": list}
    exec(src[start:end], ns)
    return ns["terminal_course_ids"](session)


def test_combined_terminal_reports_every_batch():
    s = {"user_info": {"course_id": 2, "course_ids": [2, 3]}}
    assert _terminal_course_ids(s) == [2, 3]


def test_single_batch_terminal_reports_one():
    s = {"user_info": {"course_id": 2, "course_ids": [2]}}
    assert _terminal_course_ids(s) == [2]


def test_session_predating_combined_terminals_still_works():
    """A kiosk logged in before this feature shipped has no course_ids key.
    It must keep working on its original batch, not fall through to an empty
    scope (which every caller reads as 'no batches')."""
    s = {"user_info": {"course_id": 7}}
    assert _terminal_course_ids(s) == [7]


def test_terminal_with_no_batch_gets_empty_scope():
    assert _terminal_course_ids({"user_info": {}}) == []


# --------------------------------------------------------------------------
# live count across several batches
# --------------------------------------------------------------------------

@pytest.fixture
def three_batches(db):
    """Batch 1: 3 students (2 present). Batch 2: 2 students (1 present).
    Batch 3: 4 students (none present)."""
    from datetime import date
    cur = db.cursor()
    today = date.today().strftime("%Y-%m-%d")
    for cid, name in ((1, "PGCP-AI"), (2, "PGCP-BDA"), (3, "PGCP-OTHER")):
        cur.execute(
            "INSERT INTO courses (id, name, start_date, end_date, is_active, created_at) "
            "VALUES (?, ?, '2026-08-24', '2027-12-31', 1, '2026-08-24T00:00:00')",
            (cid, name),
        )
    ids = {}
    roster = [("A1", 1), ("A2", 1), ("A3", 1), ("B1", 2), ("B2", 2),
              ("C1", 3), ("C2", 3), ("C3", 3), ("C4", 3)]
    for roll, cid in roster:
        cur.execute(
            "INSERT INTO students (student_id, name, email, course_id, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            (roll, roll, f"{roll.lower()}@test.local", cid),
        )
        ids[roll] = cur.lastrowid
    for roll, cid in (("A1", 1), ("A2", 1), ("B1", 2)):
        cur.execute(
            "INSERT INTO attendance (student_id, date, time_in, status, session_type, course_id) "
            "VALUES (?, ?, '09:30:00', 'present', 'morning_1', ?)",
            (ids[roll], today, cid),
        )
    db.commit()
    return ids


def _manager(db_path):
    from attendance_manager import AttendanceSlotManager
    return AttendanceSlotManager(db_path)


def test_combined_terminal_totals_add_up(db_path, three_batches):
    m = _manager(db_path)
    combined = m.get_live_student_count(course_id=[1, 2])
    assert combined["total_students"] == 5, "3 from batch 1 + 2 from batch 2"
    assert combined["total_present"] == 3
    assert combined["attendance_percentage"] == pytest.approx(60.0)

    # and it must exclude the batch that was not unlocked
    assert combined["total_students"] < m.get_live_student_count()["total_students"]


def test_single_id_and_one_element_list_agree(db_path, three_batches):
    m = _manager(db_path)
    assert (m.get_live_student_count(course_id=1)
            == m.get_live_student_count(course_id=[1]))


def test_empty_scope_means_no_batches_not_all_batches(db_path, three_batches):
    """The dangerous failure mode: a terminal whose scope resolves to nothing
    must show zero, never the whole institute."""
    m = _manager(db_path)
    assert m.get_live_student_count(course_id=[])["total_students"] == 0
    assert m.get_live_student_count()["total_students"] == 9


# --------------------------------------------------------------------------
# every batch needs its own PIN
# --------------------------------------------------------------------------

def test_login_verifies_a_pin_for_every_batch():
    """Source-level guard: the handler must check each pair in turn and only
    add a course to the session after its PIN verified."""
    src = open(APP_FILE, encoding="utf-8").read()
    start = src.index('@app.post("/api/terminal-login")')
    body = src[start:src.index("@app.", start + 10)]

    assert "for cid, pin in pairs:" in body, "must loop over every requested batch"
    # the append must come after the verify, inside the same loop
    verify_at = body.index("verify_password")
    append_at = body.index("course_ids.append")
    assert verify_at < append_at, "a batch is added to the session before its PIN is checked"
    assert "return {\"success\": False, \"message\": f\"Incorrect PIN{label}\"}" in body
