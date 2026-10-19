"""Every data endpoint must require authentication.

This parses the route table out of main_with_face_recognition.py rather than
importing the app (the face-recognition model isn't available in every
environment). It guards against the regression where an endpoint is added
without a `Depends(...)` auth dependency and silently exposes data.

Background: an audit found 22 endpoints reachable with no login at all —
including ones that could mark attendance and register a face.
"""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_FILE = os.path.join(ROOT, "main_with_face_recognition.py")

# Endpoints that MUST stay reachable without a session: the login surface and
# public pages. Anything not on this list has to be guarded.
PUBLIC_BY_DESIGN = {
    "/", "/login", "/logout", "/about", "/contact", "/forgot-password",
    "/api/admin-login", "/api/user-login", "/api/face-login", "/api/logout",
    "/api/terminal-login", "/api/terminal/batches",
    "/api/forgot-password", "/api/reset-password-otp",
    "/api/system/status",
    # PWA plumbing. The browser fetches both outside any page session, and
    # neither exposes data: sw.js is static JS, the manifest is static JSON.
    "/sw.js", "/manifest.webmanifest",
    # The privacy notice must be readable BEFORE consenting (and before
    # logging in at all). Static text, no data.
    "/privacy",
}


def parse_routes():
    """Return [(method, path, has_auth_dependency), ...] from the app source."""
    lines = open(APP_FILE, encoding="utf-8").read().split("\n")
    routes, i = [], 0
    while i < len(lines):
        m = re.match(r'@app\.(get|post|put|delete)\("([^"]+)"', lines[i].strip())
        if m:
            method, path = m.group(1).upper(), m.group(2)
            j = i + 1
            while j < len(lines) and "def " not in lines[j]:
                j += 1
            # walk the signature until parentheses balance (handles multi-line defs)
            sig, depth, started, k = "", 0, False, j
            while k < len(lines):
                sig += lines[k]
                depth += lines[k].count("(") - lines[k].count(")")
                if "(" in lines[k]:
                    started = True
                if started and depth <= 0:
                    break
                k += 1
            routes.append((method, path, "Depends(" in sig))
            i = k
        i += 1
    return routes


ROUTES = parse_routes()


def test_route_table_was_parsed():
    """Sanity: if parsing breaks, the other tests would pass vacuously."""
    assert len(ROUTES) > 50, f"only parsed {len(ROUTES)} routes — parser likely broken"


@pytest.mark.parametrize(
    "method,path",
    [(m, p) for m, p, guarded in ROUTES if not guarded and p not in PUBLIC_BY_DESIGN],
)
def test_no_unguarded_data_endpoints(method, path):
    """Fails for any endpoint missing an auth dependency."""
    pytest.fail(
        f"{method} {path} has no Depends(...) auth guard. "
        f"Add the appropriate require_* dependency, or add it to "
        f"PUBLIC_BY_DESIGN in this test if it is genuinely public."
    )


def test_public_surface_has_not_grown():
    """The set of unauthenticated endpoints must not expand unnoticed."""
    unguarded = {p for _m, p, guarded in ROUTES if not guarded}
    unexpected = unguarded - PUBLIC_BY_DESIGN
    assert not unexpected, f"new unauthenticated endpoint(s): {sorted(unexpected)}"


def test_every_route_lives_in_the_app_file():
    """No other module may register routes, because this guard cannot see them.

    How 9 unauthenticated endpoints survived every run of this file:
    phase1_integration.add_phase1_api_endpoints(app, …) took the app object and
    declared `@app.get(...)` handlers inside a nested function in ANOTHER
    module. The parser above only ever reads main_with_face_recognition.py, so
    those routes were invisible here — among them an unauthenticated attendance
    write (POST /api/attendance/enhanced), an unauthenticated read of any
    student's history (/api/attendance/student/{id}/enhanced-v2) and an
    unauthenticated schema mutation (POST /api/system/initialize-phase1).

    Keeping every route in the app file is what makes the checks in this file
    meaningful. If routes ever need to be split across modules, teach
    parse_routes() about them in the same commit — do not delete this test.
    """
    offenders = {}
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".py") or name == os.path.basename(APP_FILE):
            continue
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            hits = [
                n for n, line in enumerate(f.read().split("\n"), 1)
                if re.search(r'@app\.(get|post|put|delete|patch)\s*\(', line)
            ]
        if hits:
            offenders[name] = hits

    assert not offenders, (
        "route decorators found outside the app file, where the auth checks in "
        f"this file cannot see them: {offenders}"
    )


class TestCriticalEndpointsGuarded:
    """Named checks for the endpoints whose exposure mattered most."""

    def _guarded(self, path):
        hits = [g for _m, p, g in ROUTES if p == path]
        assert hits, f"route {path} not found in the app"
        return all(hits)

    @pytest.mark.parametrize("path", [
        "/api/students/list",              # every student's name + email
        "/api/students/count",
        # /api/dashboard/stats used to be here. It is deleted, not merely
        # unguarded: it was unscoped by institute and counted attendance rows
        # rather than distinct students, so "present today" could exceed the
        # roster. /api/admin/overview replaced it.
        "/api/admin/overview",
        "/api/admin/consent-status",       # a roll-by-roll consent register
        "/api/attendance/today",
        "/api/attendance/bulk-export",
        "/api/analytics/at-risk",
    ])
    def test_data_reads_are_guarded(self, path):
        assert self._guarded(path), f"{path} must require authentication"

    @pytest.mark.parametrize("path", [
        "/api/attendance/manual/session",  # could mark attendance for anyone
        "/api/start_registration",         # could enrol an arbitrary face
        "/api/complete_registration",
        "/api/upload_face_photo",
        "/api/detect_attendance",
    ])
    def test_writes_are_guarded(self, path):
        assert self._guarded(path), f"{path} must require authentication"


class TestGuardsExist:
    """The guard helpers the routes depend on must still be defined."""

    @pytest.mark.parametrize("guard", [
        "require_admin_access",
        "require_teacher_or_admin",
        "require_student",
        "require_terminal",
        "require_staff_or_terminal",
        "require_any_authenticated",
        "require_user_or_admin_access",
    ])
    def test_guard_defined(self, guard):
        src = open(APP_FILE, encoding="utf-8").read()
        assert f"def {guard}(" in src, f"auth guard {guard}() is missing"

    @staticmethod
    def _handler_signature(route_path, method="get"):
        """Signature of the handler directly below a given @app decorator.

        Looked up via the decorator, not the function name: some handlers share
        a name with an AttendanceSystem method (e.g. get_today_attendance).
        """
        src = open(APP_FILE, encoding="utf-8").read()
        marker = f'@app.{method}("{route_path}")'
        idx = src.find(marker)
        assert idx != -1, f"decorator for {method.upper()} {route_path} not found"
        return src[idx:idx + 500]

    @pytest.mark.parametrize("path,method", [
        ("/api/attendance/live-count", "get"),
        ("/api/detect_attendance", "post"),
    ])
    def test_terminal_can_still_reach_live_attendance(self, path, method):
        """The kiosk reuses /attendance — these must accept a terminal session."""
        sig = self._handler_signature(path, method)
        assert "require_staff_or_terminal" in sig, (
            f"{path} must use require_staff_or_terminal or the batch terminal breaks")

    def test_today_attendance_is_staff_only(self):
        """/api/attendance/today returns every active student's name and email
        across every batch, with no batch scoping. It used to allow terminal
        sessions, which meant a kiosk in one batch's lab could read the whole
        institute's roll. Only admin.html consumes it, so the kiosk loses
        nothing by being locked out."""
        sig = self._handler_signature("/api/attendance/today", "get")
        assert "require_teacher_or_admin" in sig
        assert "require_staff_or_terminal" not in sig

    def test_students_can_still_upload_their_own_photos(self):
        """Self-registration posts to /api/upload_face_photo."""
        sig = self._handler_signature("/api/upload_face_photo", "post")
        assert "require_any_authenticated" in sig, (
            "upload_face_photo must allow students or self-registration breaks")
