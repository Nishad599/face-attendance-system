"""The admin dashboard and the aggregate it runs on.

The old dashboard called five endpoints and showed four counters, five
navigation cards and a monthly table. It could not answer "is anything
broken", "what is waiting on me" or "which batch is in trouble" — all of which
the app already had the data for, behind endpoints with no UI anywhere.

These tests pin the shape of /api/admin/overview and the parts of the page that
exist to answer those questions, plus the pieces of the old page that had to
survive the rewrite (slot timings are still only editable here).
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
jinja2 = pytest.importorskip("jinja2")


def render_dashboard():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
    return env.get_template("dashboard.html").render(
        request=None, face_recognition_available=True, opencv_available=True)


@pytest.fixture(scope="module")
def page():
    return render_dashboard()


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

class TestDashboardAnswersTheThreeQuestions:

    def test_it_asks_the_overview_endpoint(self, page):
        """One aggregate, not six round trips for things that must agree."""
        assert "/api/admin/overview" in page

    @pytest.mark.parametrize("surface,marker", [
        ("system health", 'id="ovHealth"'),
        ("approval queue", 'id="ovQueues"'),
        ("today, per batch", 'id="ovToday"'),
        ("at-risk students", 'id="ovRisk"'),
        ("attendance trend", 'id="ovTrend"'),
        ("consent compliance", 'id="ovConsent"'),
        ("audit trail", 'id="ovAudit"'),
    ])
    def test_surface_is_present(self, page, surface, marker):
        assert marker in page, f"the dashboard has no {surface} surface"

    @pytest.mark.parametrize("endpoint", [
        "/api/analytics/at-risk",
        "/api/attendance/analytics/class",
        "/api/admin/audit-log",
        "/api/admin/institute-report",
    ])
    def test_it_now_uses_data_that_had_no_ui(self, page, endpoint):
        """audit-log had no UI anywhere in the app; the analytics lived only on
        the reports page. The dashboard is where an admin actually looks."""
        assert endpoint in page

    def test_liveness_warning_is_surfaced(self, page):
        """While anti-spoofing is off a photo can mark attendance, and the only
        way to find out used to be reading .env on the server."""
        assert "ovHealthWarning" in page
        assert "ANTISPOOF_THRESHOLD" in page


class TestNothingWasLostInTheRewrite:
    """The old page's working features still have to be reachable."""

    def test_slot_timings_are_still_editable(self, page):
        for marker in ("slotBatchSelect", "sessionConfigContainer",
                       "updateSessionTiming", "testCurrentSlot"):
            assert marker in page, f"slot timing editing lost: {marker}"

    def test_batches_table_and_export_survive(self, page):
        assert "loadInstitute" in page
        assert "exportInstitute" in page
        assert "/api/admin/institute-export" in page

    def test_slot_names_are_readable(self, page):
        """The old page rendered `session_type.toUpperCase()`, so a slot showed
        as "MORNING_1 Session"."""
        assert "'Morning 1'" in page or '"Morning 1"' in page
        assert ".toUpperCase()}</h4>" not in page

    def test_logout_and_session_check_survive(self, page):
        assert "performLogout" in page
        assert "/api/session/status" in page


class TestPageSafety:
    def test_values_from_the_database_are_escaped(self, page):
        """Batch names, student names and audit actors all come from the
        database and land in innerHTML."""
        assert "function esc(" in page
        for binding in ("esc(b.batch)", "esc(s.name)", "esc(who)"):
            assert binding in page, f"unescaped interpolation: {binding}"


# ---------------------------------------------------------------------------
# The endpoint
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
class TestOverviewEndpoint:

    def _overview(self, db, institute_id=1, monkeypatch=None):
        _app.attendance_system.conn = db
        session = {"user_type": "admin",
                   "user_info": {"id": 1, "role": "admin", "institute_id": institute_id}}
        return asyncio.new_event_loop().run_until_complete(
            _app.admin_overview(session))

    @pytest.fixture(autouse=True)
    def _restore_conn(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def test_payload_shape(self, db, two_institutes):
        d = self._overview(db)
        assert d["success"] is True
        for key in ("institute", "today", "health", "queues", "consent"):
            assert key in d, f"overview is missing {key}"
        for key in ("service", "database", "face_model", "liveness", "email",
                    "backup", "attention"):
            assert key in d["health"], f"health is missing {key}"

    def test_today_is_scoped_to_the_callers_institute(self, db, two_institutes):
        """The whole point of Phase A reaching the dashboard."""
        batches1 = [b["batch"] for b in self._overview(db, 1)["today"]["batches"]]
        batches2 = [b["batch"] for b in self._overview(db, 2)["today"]["batches"]]
        assert batches1 == ["NG-BATCH"]
        assert batches2 == ["SP-BATCH"]

    def test_queues_are_scoped(self, db, two_institutes):
        """A pending request in one institute must not appear in the other's
        'waiting on you' count."""
        db.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, "
            "end_date, reason, status) VALUES (?, 1, '2026-10-20', '2026-10-21', "
            "'x', 'pending')", (two_institutes["students"]["NG001"],))
        db.commit()
        assert self._overview(db, 1)["queues"]["leave"] == 1
        assert self._overview(db, 2)["queues"]["leave"] == 0

    def test_students_without_a_face_are_counted(self, db, two_institutes):
        """Not a request queue, but the same kind of blockage: a student with
        no face enrolled cannot be marked by the camera at all, and nothing
        else in the app says so."""
        assert self._overview(db, 1)["queues"]["no_face"] == 1

    def test_batch_without_a_terminal_pin_is_flagged(self, db, two_institutes):
        db.execute("UPDATE courses SET terminal_pin_hash = NULL WHERE id = 1")
        db.commit()
        batch = self._overview(db, 1)["today"]["batches"][0]
        assert batch["has_terminal"] is False

    def test_health_flags_what_needs_attention(self, db, two_institutes):
        d = self._overview(db)
        attention = d["health"]["attention"]
        assert isinstance(attention, list)
        # Every flagged key must actually be a health card reporting ok False.
        for key in attention:
            assert d["health"][key]["ok"] is False

    def test_liveness_card_carries_a_warning_when_off(self, db, two_institutes, monkeypatch):
        monkeypatch.setattr(_app.anti_spoof_checker, "disabled", True, raising=False)
        liveness = self._overview(db)["health"]["liveness"]
        assert liveness["ok"] is False
        assert liveness["warning"] and "photo" in liveness["warning"].lower()
        assert "liveness" in self._overview(db)["health"]["attention"]

    def test_consent_counts_do_not_exceed_the_roll(self, db, two_institutes):
        c = self._overview(db, 1)["consent"]
        assert c["total"] == 1
        assert 0 <= c["consented"] <= c["total"]
        assert c["pending"] == c["total"] - c["consented"]


class TestOneBadCardDoesNotBlankThePage:
    """The four renderers were four bare calls inside one try/catch, so an
    unexpected key in the health payload threw before renderConsent ran and
    the whole overview — today's numbers included — sat on "Loading…" forever.

    Reproduced by deleting health.service from the payload: before, all four
    cards stayed blank; now the health card reads "Unavailable." and the other
    three render.
    """

    @staticmethod
    def _page():
        with open(os.path.join(ROOT, "templates", "dashboard.html"),
                  encoding="utf-8") as f:
            return f.read()

    @pytest.mark.parametrize("renderer", [
        "renderHealth", "renderQueues", "renderToday", "renderConsent"])
    def test_each_card_renders_inside_its_own_guard(self, renderer):
        page = self._page()
        calls = [ln.strip() for ln in page.splitlines()
                 if renderer + "(d." in ln]
        assert calls, f"{renderer} is no longer called from loadOverview"
        for call in calls:
            assert call.startswith("section("), (
                f"{renderer} is called unguarded: {call}")

    def test_the_guard_replaces_the_loading_text(self):
        """Leaving "Loading…" up reads as "still working" and never resolves,
        which is worse than saying it failed."""
        page = self._page()
        start = page.index("function section(")
        body = page[start:start + 600]
        assert "innerHTML" in body
        assert "Unavailable" in body

    def test_the_consent_card_reports_the_real_policy_version(self):
        """renderConsent does `c.policy_version || '1.0'`, and the payload
        never carried the key — so bumping CONSENT_POLICY_VERSION, which
        re-asks every student, left the card still claiming v1.0."""
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        assert 'consent["policy_version"] = CONSENT_POLICY_VERSION' in src
