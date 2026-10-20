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


class TestTheRoleCanActuallyLogIn:
    """The gap this closes: migrate_phase10 added the role and
    require_superadmin guarded the console, but the staff login accepted only
    ["admin", "teacher"] — so the role existed and nobody could ever become
    one. The console was unreachable by construction."""

    def test_login_accepts_the_role(self):
        src = read(APP_FILE)
        idx = src.find("async def simple_admin_login")
        assert idx != -1
        body = src[idx:idx + 1400]
        assert '"superadmin"' in body, "staff login still refuses superadmins"

    def test_login_lands_on_the_console(self):
        src = read(APP_FILE)
        idx = src.find("async def simple_admin_login")
        assert '"/superadmin"' in src[idx:idx + 1400]

    def test_superadmin_passes_the_admin_guards(self):
        """Otherwise every admin page 403s them, including the console's own
        'Leave platform view' link."""
        src = read(APP_FILE)
        for guard in ("def require_admin_access", "def require_teacher_or_admin"):
            idx = src.find(guard)
            assert idx != -1
            assert "superadmin" in src[idx:idx + 900], f"{guard} excludes superadmin"

    def test_an_admin_is_still_not_a_superadmin(self):
        """The converse must never hold."""
        src = read(APP_FILE)
        idx = src.find("def require_superadmin(")
        body = src[idx:idx + 600]
        assert '!= "superadmin"' in body or "!= 'superadmin'" in body

    def test_nav_shows_the_console_only_to_owners(self):
        nav = read(os.path.join(TEMPLATES, "_navbar.html"))
        m = re.search(r'href="/superadmin"[^>]*data-roles="([^"]+)"', nav)
        assert m, "no platform link in the nav"
        assert m.group(1).strip() == "superadmin"

    def test_there_is_a_way_to_create_one(self):
        """The migration deliberately creates no account: a privileged login
        appearing by itself, with a password nobody can account for, is worse
        than having to run one command."""
        script = os.path.join(ROOT, "create_superadmin.py")
        assert os.path.exists(script)
        src = read(script)
        assert "getpass" in src, "a password passed as an argument lands in shell history"
        assert "hash_password" in src


@needs_app
class TestEveryInstituteGetsItsOwnAdmin:
    """Provisioning creates the tenant, not its way in.

    The first institute's admin is confined to it by tenancy — correctly — so
    a newly created institute had nobody who could run it. That made the
    provisioning feature useless on its own.
    """

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _boss(self):
        return session_for("superadmin", None)

    def _create_admin(self, db, institute_id, username, password="Temp12345"):
        _app.attendance_system.conn = db
        body = _app.InstituteAdminCreate(username=username, password=password)
        return asyncio.new_event_loop().run_until_complete(
            _app.platform_create_admin(institute_id, body, self._boss()))

    def test_admin_is_created_for_that_institute(self, db, two_institutes):
        assert self._create_admin(db, 2, "sp.admin")["success"] is True
        row = db.execute(
            "SELECT role, institute_id, must_change_password FROM users "
            "WHERE username = 'sp.admin'").fetchone()
        assert row[0] == "admin"
        assert row[1] == 2
        assert row[2] == 1, "the new admin should replace the temporary password"

    def test_the_new_admin_is_confined_to_it(self, db, two_institutes):
        """The whole point. An admin created for institute 2 must not be able
        to act on institute 1's batches."""
        import tenancy
        self._create_admin(db, 2, "sp.admin")
        row = db.execute(
            "SELECT id, institute_id FROM users WHERE username = 'sp.admin'").fetchone()
        sess = {"user_type": "admin",
                "user_info": {"id": row[0], "role": "admin", "institute_id": row[1]}}
        assert tenancy.session_may_touch_course(db, sess, 2) is True
        assert tenancy.session_may_touch_course(db, sess, 1) is False

    def test_username_collisions_are_refused(self, db, two_institutes):
        """Usernames are deployment-wide: the sign-in form has no institute
        field, so two tenants cannot both own 'admin'."""
        self._create_admin(db, 1, "shared.name")
        d = self._create_admin(db, 2, "shared.name")
        assert d["success"] is False
        assert "already taken" in d["message"]

    def test_short_password_is_refused(self, db, two_institutes):
        assert self._create_admin(db, 2, "x.admin", "short")["success"] is False

    def test_unknown_institute_is_404(self, db, two_institutes):
        with pytest.raises(_app.HTTPException) as e:
            self._create_admin(db, 9999, "nobody")
        assert e.value.status_code == 404

    def test_console_flags_an_institute_with_no_admin(self, db, two_institutes):
        """A freshly provisioned institute: the fixture's two already have
        admins, so this makes a third the way the console does."""
        _app.attendance_system.conn = db
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_app.platform_create_institute(
            _app.InstituteCreate(name="Pune Academy"), self._boss()))

        by_name = {i["name"]: i for i in
                   loop.run_until_complete(_app.platform_institutes(self._boss()))["institutes"]}
        assert by_name["Pune Academy"]["admins"] == 0
        assert any("administrator" in w for w in by_name["Pune Academy"]["warnings"])

        # And the warning clears once it has one.
        new_id = db.execute(
            "SELECT id FROM institutes WHERE slug = 'pune-academy'").fetchone()[0]
        self._create_admin(db, new_id, "pune.admin")
        by_name = {i["name"]: i for i in
                   loop.run_until_complete(_app.platform_institutes(self._boss()))["institutes"]}
        assert by_name["Pune Academy"]["admins"] == 1
        assert not any("administrator" in w for w in by_name["Pune Academy"]["warnings"])

    def test_listing_admins_is_per_institute(self, db, two_institutes):
        self._create_admin(db, 1, "ng.admin")
        self._create_admin(db, 2, "sp.admin")
        _app.attendance_system.conn = db
        loop = asyncio.new_event_loop()
        # The fixture already seeded ng_admin / sp_admin, so assert the new
        # one lands in the right institute and the other's does not appear.
        for iid, mine, theirs in ((1, "ng.admin", "sp.admin"),
                                  (2, "sp.admin", "ng.admin")):
            names = [a["username"] for a in loop.run_until_complete(
                _app.platform_list_admins(iid, self._boss()))["admins"]]
            assert mine in names
            assert theirs not in names


@needs_app
class TestTeacherAccountsAreScopedToo:
    """Found alongside: create_teacher never set institute_id, so every
    teacher was created unconfined, and list_teachers showed every tenant's
    staff — names and email addresses — to any admin."""

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def test_new_teacher_inherits_the_creating_admins_institute(self, db, two_institutes):
        _app.attendance_system.conn = db
        body = _app.TeacherCreate(username="ng.teach", password="Temp12345")
        d = asyncio.new_event_loop().run_until_complete(
            _app.create_teacher(body, session_for("admin", 1)))
        assert d["success"] is True
        assert db.execute(
            "SELECT institute_id FROM users WHERE username = 'ng.teach'"
        ).fetchone()[0] == 1

    def test_cannot_assign_another_institutes_batch(self, db, two_institutes):
        _app.attendance_system.conn = db
        body = _app.TeacherCreate(username="sneaky", password="Temp12345", batch_ids=[2])
        d = asyncio.new_event_loop().run_until_complete(
            _app.create_teacher(body, session_for("admin", 1)))
        assert d["success"] is False, "a cross-institute batch assignment was allowed"

    def test_listing_is_scoped(self, db, two_institutes):
        for uname, inst in (("ng.teach", 1), ("sp.teach", 2)):
            db.execute(
                "INSERT INTO users (username, name, password_hash, role, is_active, "
                "institute_id) VALUES (?, ?, 'x', 'teacher', 1, ?)", (uname, uname, inst))
        db.commit()
        _app.attendance_system.conn = db
        loop = asyncio.new_event_loop()
        for inst, expected in ((1, "ng.teach"), (2, "sp.teach")):
            d = loop.run_until_complete(_app.list_teachers(session_for("admin", inst)))
            assert [t["username"] for t in d["teachers"]] == [expected]
