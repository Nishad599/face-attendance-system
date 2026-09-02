"""Slot timings are configured per batch and must be read per batch.

The bug: session_configs has a course_id, but the loader read every row and
keyed the result by session_type. Each batch has its own 'morning_1',
'afternoon_2' and so on, so the batches collapsed into one set of slots and the
last row read silently overwrote all the others.

Symptom: editing a batch's slot timing appeared to do nothing, and its students
were told they were outside their slot while their own batch was mid-session.
"""

from datetime import datetime

import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


@pytest.fixture
def batches_with_different_slots(db):
    """Two batches whose afternoon session ends at different times."""
    cur = db.cursor()
    for cid, name in ((1, "PGCP-AI"), (2, "PGCP-BDA")):
        cur.execute(
            "INSERT INTO courses (id, name, start_date, end_date, is_active, created_at) "
            "VALUES (?, ?, '2026-08-24', '2027-12-31', 1, '2026-08-24T00:00:00')",
            (cid, name),
        )
    rows = [
        # batch 1 runs late
        (1, "morning_1", "08:30:00", "09:30:00"),
        (1, "afternoon_2", "16:15:00", "17:50:00"),
        # batch 2 finishes earlier
        (2, "morning_1", "08:30:00", "09:30:00"),
        (2, "afternoon_2", "16:15:00", "16:45:00"),
    ]
    for cid, st, start, end in rows:
        cur.execute(
            "INSERT INTO session_configs (course_id, session_type, start_time, end_time, is_active) "
            "VALUES (?, ?, ?, ?, 1)",
            (cid, st, start, end),
        )
    cur.execute(
        "INSERT INTO students (student_id, name, email, course_id, status) "
        "VALUES ('A1', 'Alpha', 'a1@test.local', 1, 'active')")
    late_batch_student = cur.lastrowid
    cur.execute(
        "INSERT INTO students (student_id, name, email, course_id, status) "
        "VALUES ('B1', 'Bravo', 'b1@test.local', 2, 'active')")
    early_batch_student = cur.lastrowid
    db.commit()
    return {"late": late_batch_student, "early": early_batch_student}


def _manager(db_path):
    import attendance_manager
    attendance_manager.invalidate_slot_cache()
    return attendance_manager.AttendanceSlotManager(db_path)


def test_each_batch_keeps_its_own_timings(db_path, batches_with_different_slots):
    m = _manager(db_path)
    assert str(m.slots_for_course(1)["afternoon_2"]["end_time"]) == "17:50:00"
    assert str(m.slots_for_course(2)["afternoon_2"]["end_time"]) == "16:45:00"


def test_slot_is_active_only_for_the_batch_that_runs_late(db_path,
                                                          batches_with_different_slots):
    """17:10 is inside batch 1's session and outside batch 2's."""
    m = _manager(db_path)
    probe = IST.localize(datetime(2026, 9, 2, 17, 10))
    assert m.get_current_slot(probe, course_id=1) is not None, \
        "batch 1 runs to 17:50 - its students must be able to mark at 17:10"
    assert m.get_current_slot(probe, course_id=2) is None, \
        "batch 2 ends at 16:45 - 17:10 is outside its slot"


def test_batch_without_its_own_config_falls_back(db_path,
                                                 batches_with_different_slots):
    """A batch with no rows of its own still resolves to something usable."""
    m = _manager(db_path)
    assert m.slots_for_course(999), "unconfigured batch must not end up with zero slots"


def test_editing_a_batch_takes_effect_immediately(db_path,
                                                  batches_with_different_slots):
    """Timings are cached, so the save path has to invalidate. Without that an
    admin's edit appears to do nothing until the cache expires or the app
    restarts - which is how this was reported."""
    m = _manager(db_path)
    probe = IST.localize(datetime(2026, 9, 2, 18, 30))

    assert m.get_current_slot(probe, course_id=1) is None
    m.slots_for_course(1)          # force it into the cache

    ok, _ = m.update_session_timing("afternoon_2", "16:15", "19:00", course_id=1)
    assert ok

    assert str(m.slots_for_course(1)["afternoon_2"]["end_time"]) == "19:00:00"
    assert m.get_current_slot(probe, course_id=1) is not None, \
        "the edit must apply without a restart"
    # the other batch must be untouched
    assert str(m.slots_for_course(2)["afternoon_2"]["end_time"]) == "16:45:00"


def test_editing_one_batch_does_not_move_another(db_path,
                                                 batches_with_different_slots):
    m = _manager(db_path)
    m.update_session_timing("morning_1", "07:00", "07:30", course_id=1)
    assert str(m.slots_for_course(1)["morning_1"]["start_time"]) == "07:00:00"
    assert str(m.slots_for_course(2)["morning_1"]["start_time"]) == "08:30:00"


def test_marking_uses_the_students_own_batch(db_path, batches_with_different_slots):
    """The whole point: a student is judged against their batch's timings."""
    import attendance_manager
    src = open(attendance_manager.__file__, encoding="utf-8").read()
    start = src.index("def mark_attendance_with_slot(")
    body = src[start:src.index("\n    def ", start + 10)]

    assert "SELECT course_id FROM students WHERE id = ?" in body, \
        "marking must look up the student's batch"
    lookup_at = body.index("SELECT course_id FROM students")
    slot_at = body.index("self.get_current_slot(")
    assert lookup_at < slot_at, "the batch must be resolved before the slot check"
    assert "student_course_id" in body
