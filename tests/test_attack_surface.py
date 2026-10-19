"""Endpoints that no page calls are still reachable by anyone who asks.

Four of them had accumulated. Three are gone and one was wired up, and these
tests are what keeps them from drifting back:

  POST /api/face-login             minted a student session from an image
  GET  /api/navigation/home        only knew "admin" and the legacy "user"
  GET  /api/dashboard/stats        unscoped, and counted rows not students
  GET  /api/admin/consent-status   kept, scoped, and now has a UI

The deletion tests assert on the route decorators rather than on prose, since
the file explains at length why face login is gone and a naive search for the
string finds the explanation.
"""

import ast
import asyncio
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from conftest import session_for                  # noqa: E402

MAIN = os.path.join(ROOT, "main_with_face_recognition.py")

with open(MAIN, encoding="utf-8") as _f:
    SRC = _f.read()

ROUTES = set(re.findall(r'@app\.(?:get|post|put|delete)\("([^"]+)"\)', SRC))

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


class TestRemovedRoutesStayRemoved:
    @pytest.mark.parametrize("route,why", [
        ("/api/face-login",
         "an image is not a credential: nothing attests it came from a camera"),
        ("/api/navigation/home",
         "sent teachers, students and superadmins to /login as 'Invalid session'"),
        ("/api/dashboard/stats",
         "unscoped by institute, and counted attendance rows not students"),
    ])
    def test_route_is_gone(self, route, why):
        assert route not in ROUTES, "{} is back - {}".format(route, why)

    def test_no_handler_still_creates_a_session_from_an_image(self):
        """The shape of the hazard, not the name of the route.

        A handler that both decodes an image and calls create_session() is
        face login again under another name.
        """
        tree = ast.parse(SRC)
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(SRC, node) or ""
            if "b64decode" in body and "create_session" in body:
                offenders.append(node.name)
        assert not offenders, (
            "these mint a session from an uploaded image: " + ", ".join(offenders))

    def test_the_request_model_went_with_it(self):
        assert "class SimpleFaceLogin" not in SRC

    def test_the_readme_no_longer_advertises_it(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        # The explanation names the route; the endpoint table must not.
        rows = [ln for ln in readme.splitlines() if ln.startswith("| **Auth**")]
        assert rows, "the Auth row of the endpoint table vanished"
        assert "/api/face-login" not in rows[0]

    def test_every_login_path_is_throttled(self):
        """face-login was the one that was not, which is how a gallery of
        512-D embeddings becomes brute-forceable."""
        unthrottled = []
        for m in re.finditer(r'@app\.post\("(/api/[a-z-]*login[a-z-]*)"\)', SRC):
            start = m.start()
            end = SRC.find("\n@app.", start + 10)
            body = SRC[start:end if end != -1 else len(SRC)]
            if "login_blocked" not in body:
                unthrottled.append(m.group(1))
        assert not unthrottled, "no lockout on: " + ", ".join(unthrottled)


class TestOneThresholdNotFour:
    """`RECOGNITION_THRESHOLD = 0.60` was written out inside each handler that
    matches a face, plus once as a class attribute. Tuning one and not the
    others means a student recognised at the terminal and not at the staff
    camera."""

    def test_no_local_copies_remain(self):
        # An assignment, not the bare name: the comment explaining why the
        # locals are gone mentions them, and an earlier version of this test
        # failed on my own prose.
        assert not re.search(r"^\s*RECOGNITION_THRESHOLD\s*=", SRC, re.M)

    def test_the_literal_is_not_inlined_at_a_comparison(self):
        assert not re.search(r"best_sim(?:ilarity)?\s*>=?\s*0\.\d", SRC), \
            "a bare number is being compared against a face match"

    def test_it_is_tunable_without_a_code_change(self):
        assert 'getenv("FACE_MATCH_THRESHOLD"' in SRC

    def test_the_active_value_is_visible_to_an_operator(self):
        """Tuning it blind is worse than not tuning it."""
        assert '"face_match_threshold"' in SRC
        with open(os.path.join(ROOT, "templates", "settings.html"),
                  encoding="utf-8") as f:
            assert "face_match_threshold" in f.read()

    @needs_app
    def test_it_is_one_value_everywhere(self):
        assert (_app.AttendanceSystem.DUPLICATE_FACE_THRESHOLD
                == _app.FACE_MATCH_THRESHOLD)


@needs_app
class TestConsentRegisterIsScopedAndReachable:
    """Kept, not deleted: the consent register is the record you need when a
    regulator or a parent asks. But it filtered on teacher_allowed_course_ids()
    alone, which returns None for an admin meaning "every batch" - so one
    customer's admin got a roll-by-roll register for every other customer."""

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def world(self, db, two_institutes):
        _app.attendance_system.conn = db
        return {"loop": asyncio.new_event_loop()}

    def test_admin_sees_only_their_own_institutes_students(self, world):
        d = world["loop"].run_until_complete(
            _app.consent_status_report(session=session_for("admin", 1)))
        assert d["success"] is True
        rolls = set(s["roll_no"] for s in d["students"])
        assert rolls == {"NG001"}, "leaked: {}".format(rolls - {"NG001"})

    def test_the_totals_are_scoped_too(self, world):
        """A count of 2 when you can see 1 is its own disclosure."""
        d = world["loop"].run_until_complete(
            _app.consent_status_report(session=session_for("admin", 2)))
        assert d["total"] == 1
        assert d["consented"] + d["pending"] == 1

    def test_cannot_ask_for_another_institutes_batch_by_id(self, world):
        try:
            world["loop"].run_until_complete(
                _app.consent_status_report(
                    course_id=2, session=session_for("admin", 1)))
        except _app.HTTPException as e:
            assert e.status_code == 403
        else:
            pytest.fail("course_id=2 was served to institute 1's admin")

    def test_forbidden_is_not_returned_as_success_false(self, world):
        """A bare `except Exception` swallows HTTPException, so the caller
        cannot tell 'not yours' from 'something broke'."""
        start = SRC.index('@app.get("/api/admin/consent-status")')
        end = SRC.find("\n@app.", start + 10)
        assert "except HTTPException" in SRC[start:end]

    def test_the_dashboard_actually_calls_it(self, world):
        """It was a complete endpoint that nothing invoked, which is the same
        as not having it."""
        with open(os.path.join(ROOT, "templates", "dashboard.html"),
                  encoding="utf-8") as f:
            assert "/api/admin/consent-status" in f.read()

    def test_the_drill_down_links_by_db_id_not_roll_number(self, world):
        """/people?student= is parseInt'd, so a roll number silently opens the
        wrong person or nobody."""
        with open(os.path.join(ROOT, "templates", "dashboard.html"),
                  encoding="utf-8") as f:
            page = f.read()
        link = [ln for ln in page.splitlines() if "/people?student=" in ln]
        assert link, "the register does not link anywhere"
        assert "student_db_id" in link[0], link[0]
