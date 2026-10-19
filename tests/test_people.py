"""People: one place for students, teachers and the not-yet-enrolled.

Students were listed on four surfaces — /students, the teacher portal's
Students tab, /admin/batches' Pending tab and the reports page's roster — and
none of them was complete. Teachers lived somewhere else again. There was no
page for ONE student at all: their data sat in a sparkline modal, a slots
endpoint and the subject breakdown, with nothing joining them.
"""

import asyncio
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TEMPLATES = os.path.join(ROOT, "templates")
APP_FILE = os.path.join(ROOT, "main_with_face_recognition.py")
jinja2 = pytest.importorskip("jinja2")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def page():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
    return env.get_template("people.html").render(
        request=None, is_admin=True, face_recognition_available=True)


class TestNavigation:
    def test_nav_points_at_people(self):
        nav = read(os.path.join(TEMPLATES, "_navbar.html"))
        assert 'href="/people"' in nav
        assert 'href="/students"' not in nav

    def test_teachers_can_reach_it_too(self):
        nav = read(os.path.join(TEMPLATES, "_navbar.html"))
        import re
        m = re.search(r'href="/people"[^>]*data-roles="([^"]+)"', nav)
        assert m and "teacher" in m.group(1) and "admin" in m.group(1)

    def test_old_url_still_works(self):
        """Bookmarks and old links must not 404."""
        src = read(APP_FILE)
        idx = src.find('@app.get("/students", response_class=HTMLResponse)')
        assert idx != -1
        assert "RedirectResponse" in src[idx:idx + 700]
        assert '"/people"' in src[idx:idx + 700]


class TestPeoplePage:
    @pytest.mark.parametrize("surface", ["tab-students", "tab-teachers", "tab-pending"])
    def test_all_three_groups_are_here(self, page, surface):
        assert f'id="{surface}"' in page

    @pytest.mark.parametrize("endpoint", [
        "/api/students/list", "/api/teachers", "/api/students/pending",
    ])
    def test_merges_the_three_sources(self, page, endpoint):
        assert endpoint in page

    def test_has_search_and_filters(self, page):
        """A roll-number lookup was impossible without knowing which page to
        start from."""
        assert 'type="search"' in page
        assert 'id="batchFilter"' in page
        assert 'id="faceFilter"' in page

    def test_face_status_is_visible_in_the_list(self, page):
        """Without a face a student cannot be marked at all — the most
        actionable fact about them, and previously invisible."""
        assert "cannot be marked" in page
        assert "photo_count" in page

    def test_detail_is_a_panel_not_a_modal(self, page):
        """A modal throws away the list you were reading."""
        assert 'id="panel"' in page
        assert "/api/students/" in page and "/profile" in page

    def test_values_are_escaped(self, page):
        assert "function esc(" in page
        for b in ("esc(s.name)", "esc(s.student_id)", "esc(x.username)"):
            assert b in page, f"unescaped: {b}"


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


@needs_app
class TestStudentProfileEndpoint:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _profile(self, db, student_id, institute_id=1):
        _app.attendance_system.conn = db
        s = {"user_type": "admin",
             "user_info": {"id": 1, "role": "admin", "institute_id": institute_id}}
        return asyncio.new_event_loop().run_until_complete(
            _app.student_profile_detail(student_id, s))

    def test_returns_the_whole_picture(self, db, two_institutes):
        d = self._profile(db, two_institutes["students"]["NG001"])
        assert d["success"] is True
        for key in ("student", "attendance", "subjects", "requests", "consent"):
            assert key in d, f"profile is missing {key}"

    def test_face_status_is_reported(self, db, two_institutes):
        d = self._profile(db, two_institutes["students"]["NG001"])
        assert d["student"]["has_face"] is False

    def test_open_requests_are_counted(self, db, two_institutes):
        sid = two_institutes["students"]["NG001"]
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, '2026-11-01', '2026-11-02', 'x', 'pending')",
            (sid,))
        db.commit()
        assert self._profile(db, sid)["requests"]["leave"] == 1

    def test_unknown_student_is_404(self, db, two_institutes):
        with pytest.raises(_app.HTTPException) as e:
            self._profile(db, 99999)
        assert e.value.status_code == 404

    def test_cannot_read_another_institutes_student(self, db, two_institutes):
        """The tenancy rules reach this endpoint too."""
        sp = two_institutes["students"]["SP001"]
        with pytest.raises(_app.HTTPException) as e:
            self._profile(db, sp, institute_id=1)
        assert e.value.status_code == 403
