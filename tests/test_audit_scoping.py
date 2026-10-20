"""The audit log is scoped; platform actions are the superadmin's alone.

`audit_log` recorded `course_id`, which is NULL for anything not about a
batch — including every platform-level action a superadmin takes — and the
dashboard read the table with no filter at all. An institute admin therefore
saw, on their own dashboard:

    Platform Owner · create_institute · cdac ycp
    Platform Owner · create_institute_admin · ycpadmin

which tells one customer that another customer exists and names their
administrator.
"""

import asyncio
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from conftest import session_for                  # noqa: E402

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
class TestAuditScoping:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def seeded(self, db, two_institutes):
        """One entry per institute, plus a platform-level one."""
        _app.attendance_system.conn = db
        _app.audit(session_for("admin", 1), "bulk_mark", target="NG batch",
                   institute_id=1)
        _app.audit(session_for("admin", 2), "bulk_mark", target="SP batch",
                   institute_id=2)
        _app.audit(session_for("superadmin", None), "create_institute",
                   target="Somewhere Else", institute_id=None)
        return db

    def _read(self, db, session, limit=50):
        _app.attendance_system.conn = db
        d = asyncio.new_event_loop().run_until_complete(
            _app.get_audit_log(limit, None, session))
        return [e["target"] for e in d["entries"]]

    def test_an_admin_sees_only_their_own_institute(self, seeded):
        assert self._read(seeded, session_for("admin", 1)) == ["NG batch"]
        assert self._read(seeded, session_for("admin", 2)) == ["SP batch"]

    def test_platform_actions_are_hidden_from_admins(self, seeded):
        """The reported bug."""
        for institute in (1, 2):
            assert "Somewhere Else" not in self._read(seeded, session_for("admin", institute))

    def test_the_superadmin_sees_everything(self, seeded):
        seen = self._read(seeded, session_for("superadmin", None))
        assert set(seen) == {"NG batch", "SP batch", "Somewhere Else"}

    def test_a_stale_session_sees_nothing(self, seeded):
        """Multi-tenant deployment, session predating tenancy. Showing
        everything would be the leak; showing nothing is a re-login."""
        stale = {"user_type": "admin", "user_info": {"id": 1, "role": "admin"}}
        assert self._read(seeded, stale) == []


@needs_app
class TestAuditAttribution:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _institute_of(self, db, target):
        return db.execute(
            "SELECT institute_id FROM audit_log WHERE target = ?", (target,)
        ).fetchone()[0]

    def test_a_batch_action_is_attributed_to_its_institute(self, db, two_institutes):
        """Inferred from the batch, not from who happened to be signed in."""
        _app.attendance_system.conn = db
        _app.audit(session_for("superadmin", None), "bulk_mark",
                   target="on NG batch", course_id=1)
        assert self._institute_of(db, "on NG batch") == 1

    def test_an_actors_institute_is_used_when_there_is_no_batch(self, db, two_institutes):
        _app.attendance_system.conn = db
        _app.audit(session_for("admin", 2), "update_institute", target="renamed")
        assert self._institute_of(db, "renamed") == 2

    def test_a_superadmin_with_no_batch_records_platform_level(self, db, two_institutes):
        _app.attendance_system.conn = db
        _app.audit(session_for("superadmin", None), "create_institute", target="New Co")
        assert self._institute_of(db, "New Co") is None

    def test_explicit_none_beats_inference(self, db, two_institutes):
        """None is a meaningful value — "platform level" — so it must not be
        mistaken for "caller said nothing, infer it"."""
        _app.attendance_system.conn = db
        _app.audit(session_for("admin", 1), "something", target="forced",
                   institute_id=None)
        assert self._institute_of(db, "forced") is None

    def test_creating_an_institute_admin_is_visible_to_that_institute(self, db, two_institutes):
        """It reveals nothing about any other customer, and that institute's
        admins should be able to see how their account came to exist."""
        _app.attendance_system.conn = db
        body = _app.InstituteAdminCreate(username="sp.admin", password="Temp12345")
        asyncio.new_event_loop().run_until_complete(
            _app.platform_create_admin(2, body, session_for("superadmin", None)))
        assert self._institute_of(db, "sp.admin") == 2


class TestSchemaIsChecked:
    def test_check_db_requires_the_column(self):
        with open(os.path.join(ROOT, "check_db.py"), encoding="utf-8") as f:
            src = f.read()
        assert '"audit_log"' in src and "institute_id" in src

    def test_migration_runs_on_deploy(self):
        with open(os.path.join(ROOT, ".github/workflows/deploy.yml"), encoding="utf-8") as f:
            assert "migrate_phase11.py" in f.read()
