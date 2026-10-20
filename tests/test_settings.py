"""Settings replaces /admin, and lets a new customer rename themselves.

/admin was named after a role rather than a task, mixed a bulk-upload form in
with a database wipe, and carried a "Check System" button calling an endpoint
that existed nowhere. It also had no way to change the one thing a deployment
sold to another institute must change first: its own name, which was only ever
hardcoded in templates.
"""

import asyncio
import os
import re
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
    return env.get_template("settings.html").render(request=None)


class TestItReplacedAdmin:
    def test_nav_points_at_settings(self):
        nav = read(os.path.join(TEMPLATES, "_navbar.html"))
        assert 'href="/settings"' in nav
        assert 'href="/admin"' not in nav

    def test_old_url_redirects(self):
        src = read(APP_FILE)
        idx = src.find("async def admin_page")
        assert idx != -1
        assert "RedirectResponse" in src[idx:idx + 600]
        assert '"/settings"' in src[idx:idx + 600]

    def test_bulk_upload_moved_to_people(self):
        """It was next to a database wipe. It belongs with the people it
        creates, and the /admin redirect would otherwise orphan it."""
        people = read(os.path.join(TEMPLATES, "people.html"))
        assert "/api/students/bulk-upload" in people
        assert "uploadBulk" in people

    def test_settings_does_not_carry_bulk_upload(self, page):
        assert "/api/students/bulk-upload" not in page


class TestInstituteProfile:
    def test_name_is_editable(self, page):
        assert 'id="instName"' in page
        assert "saveInstitute" in page
        assert "/api/admin/institute" in page

    def test_policy_is_shown_read_only(self, page):
        """These come from .env and apply at startup. A field that silently
        needs a restart is worse than one that is clearly fixed."""
        assert "policyRows" in page
        for field in ("Late after", "Longest leave request", "Face data retention"):
            assert field in page

    def test_insecure_cookie_setting_is_called_out(self, page):
        assert "plain HTTP" in page

    def test_liveness_state_is_shown(self, page):
        assert "Liveness" in page
        assert "liveness_warning" in page or "sysWarn" in page


class TestDangerZone:
    def test_destructive_action_is_last_and_separated(self, page):
        # The first "st-danger" is its CSS rule; compare where it is USED.
        body = page[page.index("</style>"):]
        assert 'class="st-card st-danger"' in body
        assert body.index("st-danger") > body.index('id="instName"'), (
            "the wipe must sit below the ordinary settings, not above them")

    def test_it_requires_typing_to_confirm(self, page):
        """A single confirm() is too easy to click through for a wipe of every
        student, their attendance and their face encodings."""
        assert "DELETE" in page
        assert "prompt(" in page

    def test_it_says_what_survives(self, page):
        assert "Batches and staff accounts are kept" in page


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
class TestInstituteEndpoint:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _session(self, institute_id=1):
        return {"user_type": "admin",
                "user_info": {"id": 1, "role": "admin", "institute_id": institute_id}}

    def _get(self, db, institute_id=1):
        _app.attendance_system.conn = db
        return asyncio.new_event_loop().run_until_complete(
            _app.get_institute(self._session(institute_id)))

    def _put(self, db, body, institute_id=1):
        _app.attendance_system.conn = db
        return asyncio.new_event_loop().run_until_complete(
            _app.update_institute(body, self._session(institute_id)))

    def test_reports_the_right_institute(self, db, two_institutes):
        assert self._get(db, 1)["institute"]["name"] == "Northgate Institute"
        assert self._get(db, 2)["institute"]["name"] == "Southpoint Academy"

    def test_rename_only_touches_your_own(self, db, two_institutes):
        assert self._put(db, {"name": "Northgate College"}, 1)["success"] is True
        assert self._get(db, 1)["institute"]["name"] == "Northgate College"
        assert self._get(db, 2)["institute"]["name"] == "Southpoint Academy"

    def test_name_is_required(self, db, two_institutes):
        assert self._put(db, {"name": "   "})["success"] is False

    def test_bad_email_is_refused(self, db, two_institutes):
        assert self._put(db, {"name": "X", "contact_email": "nope"})["success"] is False

    def test_policy_values_are_reported(self, db, two_institutes):
        p = self._get(db)["policy"]
        for key in ("late_grace_minutes", "leave_max_days", "consent_policy_version",
                    "biometric_retention_days", "cookie_secure", "database"):
            assert key in p

    def test_multi_tenant_flag(self, db, two_institutes):
        assert self._get(db)["is_multi_tenant"] is True
