"""The attendance terminal renders as a kiosk, not as the staff page.

`/attendance` serves two very different audiences from one template: staff
running live attendance, and an unattended tablet that students walk up to.
It used to serve the staff page to both and hide the parts a kiosk cannot use
with CSS, which left a screen addressed to nobody standing at it — and, worse,
a Start button a student had to press and a Stop button anyone could press to
take the terminal out of service.

These tests render both branches and assert the properties that matter, so the
kiosk cannot quietly drift back into being the staff page with bits hidden.
"""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")

jinja2 = pytest.importorskip("jinja2")


def render(is_terminal, batch="PGCP BDA August 2026"):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
    return env.get_template("attendance.html").render(
        request=None,
        is_terminal=is_terminal,
        terminal_batch=batch if is_terminal else "",
        terminal_batch_count=1 if is_terminal else 0,
    )


def markup(html):
    """Just the rendered body markup.

    The <style> block and its comments are shared by both branches — they
    describe the staff layout and explain what the kiosk replaced — so a naive
    substring check on the whole document matches CSS and prose rather than
    anything a student would see.
    """
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.I)
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    return re.sub(r"<!--[\s\S]*?-->", "", html)


@pytest.fixture(scope="module")
def kiosk():
    return render(True)


@pytest.fixture(scope="module")
def staff():
    return render(False)


@pytest.fixture(scope="module")
def kiosk_markup(kiosk):
    return markup(kiosk)


@pytest.fixture(scope="module")
def staff_markup(staff):
    return markup(staff)


class TestBothBranchesRender:
    def test_each_renders(self, kiosk, staff):
        assert len(kiosk) > 2000 and len(staff) > 2000

    @pytest.mark.parametrize("element", ['id="video"', 'id="canvas"'])
    def test_exactly_one_camera_element_each(self, kiosk, staff, element):
        """Two <video> tags would mean the capture loop grabs the wrong one."""
        assert kiosk.count(element) == 1, f"{element} appears {kiosk.count(element)}x in kiosk"
        assert staff.count(element) == 1, f"{element} appears {staff.count(element)}x in staff"

    def test_branches_are_exclusive(self, kiosk_markup, staff_markup):
        assert 'class="kiosk"' in kiosk_markup
        assert 'class="kiosk"' not in staff_markup
        assert 'class="att-layout"' in staff_markup
        assert 'class="att-layout"' not in kiosk_markup

    def test_is_terminal_flag_matches_the_branch(self, kiosk, staff):
        assert re.search(r"const IS_TERMINAL = true", kiosk)
        assert re.search(r"const IS_TERMINAL = false", staff)


class TestKioskHasNoWayToStopItself:
    """The single most important property: a kiosk runs unattended."""

    @pytest.mark.parametrize("control", ["startBtn", "stopBtn", "captureBtn"])
    def test_no_camera_controls(self, kiosk, control):
        assert f'id="{control}"' not in kiosk, (
            f"{control} is reachable at the kiosk — a student should never have "
            "to press Start, and nobody should be able to press Stop")

    def test_camera_starts_by_itself(self, kiosk):
        """startCamera() must be called on load, not bound to a button."""
        assert re.search(r"DOMContentLoaded[\s\S]{0,900}startCamera\(\)", kiosk), (
            "the kiosk does not start its own camera")

    def test_camera_failure_retries(self, kiosk):
        """Nobody is there to reload the page."""
        assert re.search(r"setTimeout\(startCamera,", kiosk)

    def test_staff_keeps_its_controls(self, staff):
        for control in ("startBtn", "stopBtn", "captureBtn"):
            assert f'id="{control}"' in staff, f"{control} disappeared from the staff page"


class TestKioskChrome:
    def test_no_navbar(self, kiosk_markup, staff_markup):
        """The kiosk has its own top bar; the app navbar links nowhere it can go."""
        assert "app-navbar" not in kiosk_markup
        assert "app-navbar" in staff_markup

    def test_no_breadcrumb_to_the_dashboard(self, kiosk_markup):
        assert "app-breadcrumb" not in kiosk_markup
        assert "/dashboard" not in kiosk_markup

    def test_navbar_padding_is_cancelled(self, kiosk):
        """app.css reserves --nav-height at the top of every page for the fixed
        navbar. The kiosk has no navbar, so without this override the footer is
        pushed below the fold on a 1280x800 tablet."""
        body_rule = kiosk[kiosk.find(".terminal-mode {"):]
        body_rule = body_rule[:body_rule.find("}")]
        assert "padding-top: 0" in body_rule

    def test_batch_name_is_shown(self, kiosk):
        """A combined terminal must say which batches it covers."""
        assert "PGCP BDA August 2026" in kiosk

    def test_admin_stat_boxes_are_gone(self, kiosk_markup, staff_markup):
        """A student does not need the class attendance percentage."""
        for label in ("Students Present", "Total Students", "Attendance %"):
            assert label not in kiosk_markup, f"admin stat {label!r} still on the kiosk"
            assert label in staff_markup

    def test_staff_wording_is_gone(self, kiosk_markup):
        for phrase in ("Live Attendance Detection", "Detection Results",
                       "Automatic face recognition attendance system"):
            assert phrase not in kiosk_markup, f"staff wording {phrase!r} still on the kiosk"

    def test_student_facing_wording_is_present(self, kiosk_markup):
        assert "Step up to mark yourself present" in kiosk_markup
        assert "Stand on the mark and look at the camera" in kiosk_markup


class TestKioskFallback:
    def test_fallback_is_always_available(self, kiosk):
        """It used to be revealed by a /api/session/status round trip; the whole
        branch only renders for terminals, so it is simply there."""
        assert 'id="mfRoll"' in kiosk
        assert "submitManualRequest" in kiosk

    def test_has_an_on_screen_keypad(self, kiosk):
        """A tablet kiosk has no keyboard, and the roll number is all digits."""
        for digit in "0123456789":
            assert f"keyPress('{digit}')" in kiosk, f"keypad is missing {digit}"
        assert "keyBack()" in kiosk and "keyClear()" in kiosk

    def test_says_it_does_not_mark_attendance(self, kiosk):
        """The request goes to a teacher for approval — a student must not walk
        away believing they are marked present."""
        assert "not</strong> mark you" in kiosk or "not mark you" in kiosk

    def test_staff_do_not_get_the_fallback(self, staff):
        """Staff have Mark Attendance in the teacher portal."""
        assert 'id="mfRoll"' not in staff


class TestKioskMessages:
    """Wording a student can act on, for every state the API can return."""

    def test_spoof_is_not_called_spoof(self, kiosk):
        assert "SPOOF DETECTED" not in kiosk
        assert "That looked like a photo" in kiosk

    @pytest.mark.parametrize("state,phrase", [
        ("marked", "MARKED PRESENT"),
        ("already marked", "ALREADY IN"),
        ("not recognised", "We didn't catch that"),
        ("outside slot", "No session open right now"),
        ("non-working day", "No marking today"),
        ("wrong batch", "Not this terminal"),
        ("camera down", "The camera is not available"),
    ])
    def test_state_has_wording(self, kiosk, state, phrase):
        assert phrase in kiosk, f"the {state} state has no student-facing wording"

    def test_every_status_tone_pairs_with_an_icon(self, kiosk):
        """Colour alone must not carry the message."""
        for call in re.findall(r"kioskStatus\(\{[\s\S]{0,120}?tone: '(\w+)'", kiosk):
            assert call in ("idle", "ok", "warn", "stop", "info")
        # every non-idle state passes an explicit icon
        blocks = re.findall(r"kioskStatus\(\{([\s\S]{0,200}?)\}\)", kiosk)
        for b in blocks:
            if "tone: 'idle'" in b:
                continue
            assert "icon:" in b, f"a status state has no icon: {b[:80]}"
