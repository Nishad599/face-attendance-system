"""The approvals inbox, and the navigation hole it closes.

Leave requests, attendance disputes, face registrations and detail changes
were four tabs inside /teacher. Two problems:

  1. navbar.js renders the portal link with data-roles="teacher", so an ADMIN
     was never shown a route to any of them. /teacher allows admins, so the
     work was reachable only by typing the URL.
  2. Four queues behind tabs means three of them are invisible at any moment,
     which is the wrong shape for "what is waiting on me".

They are now one page at /approvals, reachable by both roles, and the four
tabs are gone from the portal so this is a move rather than a fifth copy.
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


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def render(name, **ctx):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
    base = dict(request=None, user_name="X", is_admin=True)
    base.update(ctx)
    return env.get_template(name).render(**base)


@pytest.fixture(scope="module")
def approvals():
    return render("approvals.html")


@pytest.fixture(scope="module")
def portal():
    return render("teacher.html")


@pytest.fixture(scope="module")
def navbar():
    return read(os.path.join(TEMPLATES, "_navbar.html"))


# ---------------------------------------------------------------------------
# The navigation hole
# ---------------------------------------------------------------------------

class TestAdminsCanReachApprovals:

    def test_nav_has_an_approvals_link(self, navbar):
        assert 'href="/approvals"' in navbar

    def test_the_link_is_for_admins_and_teachers(self, navbar):
        """The whole bug: the portal link is teacher-only, so an admin saw no
        route to the four queues at all."""
        m = re.search(r'href="/approvals"[^>]*data-roles="([^"]+)"', navbar)
        assert m, "the approvals link has no data-roles attribute"
        roles = set(m.group(1).split(","))
        assert "admin" in roles, "admins still cannot see the approvals link"
        assert "teacher" in roles

    def test_route_allows_both_roles(self):
        src = read(APP_FILE)
        idx = src.find('@app.get("/approvals"')
        assert idx != -1, "no /approvals route"
        assert "require_teacher_or_admin" in src[idx:idx + 400]

    def test_a_count_is_shown_without_opening_the_page(self, navbar):
        """Staff should not have to open a page to learn four students are
        waiting on a decision."""
        assert "navApprovalCount" in navbar
        assert "loadApprovalBadge" in read(os.path.join(ROOT, "static/js/navbar.js"))


# ---------------------------------------------------------------------------
# It is a move, not a fifth copy
# ---------------------------------------------------------------------------

class TestQueuesLeftThePortal:

    @pytest.mark.parametrize("tab", ["facereq", "profilereq", "grievances", "leaves"])
    def test_tab_is_gone(self, portal, tab):
        assert f'data-tab="{tab}"' not in portal, f"{tab} is still a portal tab"
        assert f'id="tab-{tab}"' not in portal

    @pytest.mark.parametrize("fn", ["loadFaceRequests", "loadProfileReqs",
                                    "loadGrievances", "loadLeaves"])
    def test_loader_is_gone(self, portal, fn):
        """A leftover call to a deleted function throws on every tab switch."""
        assert fn not in portal

    def test_portal_points_at_the_new_page(self, portal):
        assert "/approvals" in portal

    def test_portal_keeps_its_own_work(self, portal):
        """Marking, analytics, modules, holidays and students stay — only the
        approval queues moved."""
        for tab in ("mark", "analytics", "modules", "holidays", "students"):
            assert f'data-tab="{tab}"' in portal, f"portal lost its {tab} tab"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

class TestApprovalsPage:

    @pytest.mark.parametrize("queue,endpoint", [
        ("leave", "/api/teacher/leaves"),
        ("grievances", "/api/teacher/grievances"),
        ("face", "/api/teacher/face-requests"),
        ("profile", "/api/teacher/profile-requests"),
    ])
    def test_queue_is_wired(self, approvals, queue, endpoint):
        assert endpoint in approvals
        assert f"{endpoint}/action" in approvals

    def test_all_four_are_visible_at_once(self, approvals):
        """Not tabs. Three queues hidden behind a tab are three queues you
        forget exist."""
        for anchor in ("id=\"q-leave\"", "id=\"q-grievances\"",
                       "id=\"q-face\"", "id=\"q-profile\""):
            assert anchor in approvals
        assert 'data-tab=' not in approvals

    def test_non_working_days_are_flagged(self, approvals):
        """Approving a dispute for a Sunday writes attendance on a day no
        report counts. The endpoint already computes this."""
        assert "is_working_day" in approvals
        assert "non_working_reason" in approvals

    def test_leave_starting_soon_is_flagged(self, approvals):
        assert "upcoming" in approvals
        assert "starting soon" in approvals or "starts " in approvals

    def test_bulk_and_single_actions_both_exist(self, approvals):
        assert "Approve selected" in approvals
        assert "function act(" in approvals

    def test_rejection_asks_for_a_reason(self, approvals):
        """It is shown to the student."""
        assert "Reason for rejecting" in approvals

    def test_slot_names_are_readable(self, approvals):
        """The grievance endpoint returns the raw key, so a dispute for the
        first morning slot rendered as "morning_1"."""
        assert "slotLabel" in approvals
        assert "'Morning 1'" in approvals
        assert "esc(r.session_type)" not in approvals

    def test_database_values_are_escaped(self, approvals):
        assert "function esc(" in approvals
        for binding in ("esc(r.student_name)", "esc(r.batch)", "esc(r.reason"):
            assert binding in approvals, f"unescaped: {binding}"


# ---------------------------------------------------------------------------
# Dead ends removed
# ---------------------------------------------------------------------------

class TestDeadEnds:

    def test_check_system_calls_an_endpoint_that_exists(self):
        """The old /admin page called /api/debug/check_system, which is defined
        nowhere in the app — the button had always errored. That page is gone
        (admin.html is deleted, /admin redirects to /settings); the assertion
        moves to the page that inherited the button."""
        page = read(os.path.join(TEMPLATES, "settings.html"))
        # The comment above the function still names the old endpoint, which is
        # the point of the comment — assert on what it actually calls.
        fetched = re.findall(r"fetch\('([^']+)'", page)
        assert "/api/debug/check_system" not in fetched
        assert "/api/system/status" in fetched

        src = read(APP_FILE)
        assert '@app.get("/api/system/status")' in src
        assert '"/api/debug/check_system"' not in src

    def test_face_registration_is_reachable(self):
        """/register was linked only from a zero-students empty state, so in
        any real deployment it could not be reached at all."""
        dash = read(os.path.join(TEMPLATES, "dashboard.html"))
        assert 'href="/register"' in dash


# ---------------------------------------------------------------------------
# The summary endpoint
# ---------------------------------------------------------------------------

_app = None
_err = None
try:
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        import importlib
        _app = importlib.import_module("main_with_face_recognition")
    finally:
        os.chdir(cwd)
except ImportError as e:
    _err = e

needs_app = pytest.mark.skipif(
    _app is None, reason=f"cannot import the app here ({_err}); run from venv_win")


@needs_app
class TestApprovalsSummary:

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def _summary(self, db, role="admin", institute_id=1):
        _app.attendance_system.conn = db
        session = {"user_type": role,
                   "user_info": {"id": 99, "role": role, "institute_id": institute_id}}
        return asyncio.new_event_loop().run_until_complete(
            _app.approvals_summary(session))

    def test_counts_every_queue(self, db, two_institutes):
        d = self._summary(db)
        assert d["success"] is True
        for key in ("leave", "grievances", "face_requests", "profile_requests", "total"):
            assert key in d["counts"]

    def test_total_is_the_sum(self, db, two_institutes):
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, '2026-11-01', '2026-11-02', 'x', 'pending')",
            (two_institutes["students"]["NG001"],))
        db.execute(
            "INSERT INTO grievances (student_id, course_id, date, reason, status) "
            "VALUES (?, 1, '2026-10-10', 'x', 'pending')",
            (two_institutes["students"]["NG001"],))
        db.commit()
        c = self._summary(db)["counts"]
        assert c["leave"] == 1 and c["grievances"] == 1
        assert c["total"] == 2

    def test_scoped_to_the_institute(self, db, two_institutes):
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, '2026-11-01', '2026-11-02', 'x', 'pending')",
            (two_institutes["students"]["NG001"],))
        db.commit()
        assert self._summary(db, institute_id=1)["counts"]["leave"] == 1
        assert self._summary(db, institute_id=2)["counts"]["leave"] == 0

    def test_only_pending_is_counted(self, db, two_institutes):
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, "
            "reason, status) VALUES (?, 1, '2026-11-01', '2026-11-02', 'x', 'approved')",
            (two_institutes["students"]["NG001"],))
        db.commit()
        assert self._summary(db)["counts"]["leave"] == 0
