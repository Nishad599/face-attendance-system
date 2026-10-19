"""The platform console: every institute at once, and only for its owner.

Phase C. The guard is the point of the whole feature — an institute admin with
platform powers is exactly what tenancy exists to prevent — so most of this
file is about who is refused rather than what is displayed.
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

from conftest import session_for                 # noqa: E402


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def page():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
    return env.get_template("superadmin.html").render(request=None, user_name="Owner")


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


class TestGuardIsSeparate:
    def test_it_is_not_folded_into_admin(self):
        """If require_admin_access let a superadmin through, every institute
        admin would inherit platform powers the first time someone reused the
        dependency."""
        src = read(APP_FILE)
        idx = src.find("def require_superadmin(")
        assert idx != -1, "no superadmin guard"
        body = src[idx:idx + 700]
        assert "superadmin" in body

    @pytest.mark.parametrize("route", [
        '@app.get("/api/platform/institutes")',
        '@app.post("/api/platform/institutes")',
        '@app.get("/superadmin"',
    ])
    def test_platform_routes_use_it(self, route):
        src = read(APP_FILE)
        idx = src.find(route)
        assert idx != -1, f"missing {route}"
        assert "require_superadmin" in src[idx:idx + 400]


@needs_app
class TestWhoGetsIn:
    @pytest.mark.parametrize("role", ["admin", "teacher", "student", "terminal"])
    def test_everyone_else_is_refused(self, role):
        with pytest.raises(_app.HTTPException) as e:
            _app.require_superadmin(session_for(role, 1))
        assert e.value.status_code == 403

    def test_no_session_is_401(self):
        with pytest.raises(_app.HTTPException) as e:
            _app.require_superadmin(None)
        assert e.value.status_code == 401

    def test_the_owner_gets_in(self):
        s = session_for("superadmin", None)
        assert _app.require_superadmin(s) is s


@needs_app
class TestPlatformListing:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _list(self, db):
        _app.attendance_system.conn = db
        return asyncio.new_event_loop().run_until_complete(
            _app.platform_institutes(session_for("superadmin", None)))

    def test_sees_every_institute(self, db, two_institutes):
        d = self._list(db)
        names = sorted(i["name"] for i in d["institutes"])
        assert names == ["Northgate Institute", "Southpoint Academy"]
        assert d["totals"]["institutes"] == 2

    def test_counts_are_per_institute(self, db, two_institutes):
        """The numbers must not be the whole deployment repeated."""
        for i in self._list(db)["institutes"]:
            assert i["students"] == 1, f"{i['name']} counted other tenants"
            assert i["batches"] == 1

    def test_flags_an_institute_nobody_can_use(self, db, two_institutes):
        """Students but no enrolled face means the camera can mark nobody."""
        warnings = {i["name"]: i["warnings"] for i in self._list(db)["institutes"]}
        assert any("face" in w for w in warnings["Northgate Institute"])

    def test_pending_approvals_are_attributed_correctly(self, db, two_institutes):
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, '2026-11-01', '2026-11-02', 'x', 'pending')",
            (two_institutes["students"]["NG001"],))
        db.commit()
        by_name = {i["name"]: i for i in self._list(db)["institutes"]}
        assert by_name["Northgate Institute"]["pending_approvals"] == 1
        assert by_name["Southpoint Academy"]["pending_approvals"] == 0


@needs_app
class TestProvisioning:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _create(self, db, name, email=None):
        _app.attendance_system.conn = db
        body = _app.InstituteCreate(name=name, contact_email=email)
        return asyncio.new_event_loop().run_until_complete(
            _app.platform_create_institute(body, session_for("superadmin", None)))

    def test_creates_with_a_slug(self, db, two_institutes):
        d = self._create(db, "Pune Academy")
        assert d["success"] is True
        assert d["slug"] == "pune-academy"

    def test_duplicate_identifier_is_refused(self, db, two_institutes):
        assert self._create(db, "Northgate Institute")["success"] is False

    def test_name_is_required(self, db, two_institutes):
        assert self._create(db, "   ")["success"] is False

    def test_it_does_not_create_an_admin_account(self, db, two_institutes):
        """Provisioning a tenant and creating a login are separate acts: an
        institute with no way in is recoverable; one with a password nobody
        can account for is not."""
        before = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self._create(db, "Pune Academy")
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before


@needs_app
class TestSuspension:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _set(self, db, iid, active):
        _app.attendance_system.conn = db
        return asyncio.new_event_loop().run_until_complete(
            _app.platform_set_active(iid, {"is_active": active},
                                     session_for("superadmin", None)))

    def test_suspend_then_restore(self, db, two_institutes):
        assert self._set(db, 1, False)["success"] is True
        assert db.execute("SELECT is_active FROM institutes WHERE id = 1").fetchone()[0] == 0
        assert self._set(db, 1, True)["success"] is True
        assert db.execute("SELECT is_active FROM institutes WHERE id = 1").fetchone()[0] == 1

    def test_suspension_deletes_nothing(self, db, two_institutes):
        before = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        self._set(db, 1, False)
        assert db.execute("SELECT COUNT(*) FROM students").fetchone()[0] == before

    def test_unknown_institute_is_404(self, db, two_institutes):
        with pytest.raises(_app.HTTPException) as e:
            self._set(db, 9999, False)
        assert e.value.status_code == 404


class TestConsoleLooksDifferent:
    def test_it_is_visibly_not_a_tenant_page(self, page):
        """When you can see every customer at once, the screen should never be
        mistakable for one customer's own."""
        assert "PLATFORM" in page
        assert "sa-bar" in page

    def test_it_does_not_include_the_tenant_navbar(self, page):
        assert "app-navbar" not in page

    def test_values_are_escaped(self, page):
        assert "function esc(" in page
        assert "esc(i.name)" in page
