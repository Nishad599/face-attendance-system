"""A group standing together must all get marked.

Detection already finds every face in the frame and the server loops over them,
but two things threw a group away:

  1. The `outside_slot` and `non_working_day` branches `return`ed from INSIDE
     that loop. Everyone already marked in the frame was discarded and the
     remaining faces were never examined. Slot timings are per batch, so on a
     combined terminal one batch being closed denied the other batch's
     students their mark.

  2. The kiosk short-circuited on the first spoof or wrong-batch face, so a
     group containing one of those showed nobody as marked — even though the
     server had marked them.
"""

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

APP_FILE = os.path.join(ROOT, "main_with_face_recognition.py")
TEMPLATES = os.path.join(ROOT, "templates")
jinja2 = pytest.importorskip("jinja2")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def slots_handler():
    src = read(APP_FILE)
    idx = src.find('@app.post("/api/detect_attendance_slots")')
    assert idx != -1
    end = src.find("\nif __name__", idx)
    return src[idx:end if end != -1 else len(src)]


@pytest.fixture(scope="module")
def kiosk():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
    return env.get_template("attendance.html").render(
        request=None, is_terminal=True, terminal_batch="PGCP AI August 2026",
        terminal_batch_count=1)


class TestServerKeepsGoingThroughTheGroup:

    def test_blocked_students_are_recorded_not_returned(self):
        body = slots_handler()
        # The per-student branch appends a row with the blocking status.
        assert '"status": blocked' in body
        assert 'blocked = ("non_working_day"' in body

    def test_no_early_return_inside_the_face_loop(self):
        """A `return` inside the loop drops the students already collected.

        The loop body is found by indentation, which is what "inside the loop"
        means. It used to be sliced from `for face_data` to the `marked_now`
        line, and that over-reached: the frame's single commit now sits
        between the two, and its failure path legitimately returns -- at that
        point the loop is finished and nothing was persisted, so reporting
        marks would be a lie.
        """
        body = slots_handler()
        lines = body.splitlines()
        start = next(i for i, ln in enumerate(lines)
                     if "for face_data in detected_faces:" in ln)
        indent = len(lines[start]) - len(lines[start].lstrip())

        loop = []
        for ln in lines[start + 1:]:
            if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
                break           # dedented back out: the loop has ended
            loop.append(ln)
        assert loop, "could not find the loop body"
        offenders = [ln.strip() for ln in loop if ln.strip().startswith("return")]
        assert not offenders, (
            "a return inside the loop abandons the group: " + "; ".join(offenders))

    def test_success_is_judged_on_who_was_marked(self):
        body = slots_handler()
        assert "marked_now = [s for s in recognized_students" in body
        assert "success = len(marked_now) > 0" in body

    def test_whole_screen_flags_need_everyone_blocked(self):
        """Four marked and one out of slot must not show the terminal as
        closed."""
        body = slots_handler()
        assert "not marked_now" in body
        assert "len(blocked_rows) == len(recognized_students)" in body

    def test_every_recognised_face_still_gets_a_row(self):
        body = slots_handler()
        # marked, already_marked, wrong_batch, spoof and the blocked pair.
        for status in ('"marked"', '"already_marked"', '"wrong_batch"',
                       '"spoof_detected"'):
            assert status in body, f"status {status} no longer reported"


class TestKioskShowsTheWholeGroup:

    def test_spoof_does_not_speak_for_the_frame(self, kiosk):
        assert "if (spoof && !marked.length && !already.length)" in kiosk

    def test_wrong_batch_does_not_either(self, kiosk):
        assert "if (wrongBatch && !marked.length && !already.length)" in kiosk

    def test_already_marked_is_a_list(self, kiosk):
        """A group can hold several people who are already in."""
        assert "students.filter(s => s.status === 'already_marked')" in kiosk
        assert "students.find(s => s.status === 'already_marked')" not in kiosk

    def test_group_names_everyone_marked(self, kiosk):
        """A count alone leaves each person unsure whether they were the one
        who got missed."""
        assert "marked.map(s =>" in kiosk
        assert "students marked" in kiosk

    def test_group_reports_who_was_not_marked(self, kiosk):
        assert "not marked — step up one at a time" in kiosk

    def test_single_person_still_gets_the_big_card(self, kiosk):
        """The common case at a kiosk is one person, and their name should
        still fill the card."""
        assert "if (marked.length === 1)" in kiosk
        assert "MARKED PRESENT" in kiosk


class TestStaffPageIsSeparate:
    """The screenshot that looked like 'the old terminal' is the STAFF page,
    which is intentionally unchanged: Start/Stop/Capture and the stat boxes are
    for an operator, not for a student at a kiosk."""

    def test_staff_keeps_its_controls(self):
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
        staff = env.get_template("attendance.html").render(
            request=None, is_terminal=False, terminal_batch="", terminal_batch_count=0)
        for control in ("startBtn", "stopBtn", "captureBtn"):
            assert f'id="{control}"' in staff
        assert 'class="kiosk"' not in staff

    def test_kiosk_has_no_controls(self, kiosk):
        for control in ("startBtn", "stopBtn", "captureBtn"):
            assert f'id="{control}"' not in kiosk
