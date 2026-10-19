"""What a signed-out browser gets, and what it is allowed to keep.

Two problems, both in the middleware layer and both invisible from inside a
logged-in session:

  1. Every protected page answered a signed-out browser with the raw JSON
     body {"detail": "Authentication required"}. An admin whose session had
     expired clicked a bookmark and got that, with no way forward but to type
     the login URL by hand. It is also the first thing a prospective customer
     sees.

  2. The no-store header was applied from a hardcoded list of four paths,
     which drifted twice: /admin and /students became redirects, and /people,
     /settings, /insights, /approvals and /superadmin arrived without it. The
     pages carrying the most student data were the cacheable ones, so a Back
     button on a shared machine could show the last person's roster.

The suite has no HTTP client, so the middleware and the exception handler are
called directly. That is also the honest unit: both are plain async functions
of a request.
"""

import asyncio
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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

HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


class _Req:
    """Only what these two functions touch: a path, a query and headers."""

    def __init__(self, path, query="", accept=HTML):
        self.url = type("U", (), {"path": path, "query": query})()
        self.headers = {"accept": accept} if accept else {}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@needs_app
class TestAnExpiredSessionLandsOnTheLoginPage:
    def _handle(self, path, query="", accept=HTML, status=401):
        return _run(_app._auth_redirect(
            _Req(path, query, accept),
            _app.HTTPException(status_code=status,
                               detail="Authentication required")))

    @pytest.mark.parametrize("path", [
        "/dashboard", "/people", "/settings", "/insights", "/approvals",
        "/superadmin", "/attendance", "/teacher", "/student",
    ])
    def test_page_navigation_redirects(self, path):
        r = self._handle(path)
        assert r.status_code == 303, f"{path} answered {r.status_code}"
        assert r.headers["location"].startswith("/login?next=")

    def test_it_remembers_where_you_were_going(self):
        """Including the query string: a link to one student is the whole
        point of the link."""
        r = self._handle("/people", "student=7")
        assert r.headers["location"] == "/login?next=%2Fpeople%3Fstudent%3D7"

    def test_403_lands_there_too(self):
        """A session for the wrong role is as useless to the browser as none
        at all, and 'Not your institute' as a JSON body helps nobody."""
        r = self._handle("/superadmin", status=403)
        assert r.status_code == 303

    def test_an_api_call_keeps_its_json_401(self):
        """The pages' own fetch handlers branch on the status. Hand them an
        HTML login page and they try to parse it as JSON."""
        r = self._handle("/api/students/list")
        assert r.status_code == 401
        assert b"Authentication required" in r.body

    def test_a_non_browser_client_keeps_its_json_401(self):
        """curl, a health check, or anything that did not ask for HTML."""
        r = self._handle("/dashboard", accept="*/*")
        assert r.status_code == 401

    def test_other_errors_are_not_turned_into_redirects(self):
        """A 404 or a 500 must not look like a login prompt."""
        for code in (404, 409, 500):
            r = self._handle("/people", status=code)
            assert r.status_code == code

    def test_the_login_page_only_honours_a_local_next(self):
        """An open redirect on a login page sends a freshly signed-in user to
        an attacker's copy of it. //evil.test is a protocol-relative URL, so
        checking for a leading / alone is not enough."""
        with open(os.path.join(ROOT, "templates", "simple_login.html"),
                  encoding="utf-8") as f:
            page = f.read()
        assert "startsWith('/')" in page
        assert "startsWith('//')" in page


@needs_app
class TestNothingPrivateIsCacheable:
    def _headers(self, path):
        async def call_next(_req):
            return _app.Response(content="x")
        r = _run(_app.add_security_headers(_Req(path), call_next))
        return r.headers

    @pytest.mark.parametrize("path", [
        "/dashboard", "/people", "/settings", "/insights", "/approvals",
        "/superadmin", "/attendance", "/teacher", "/student",
        "/attendance-management", "/admin/batches", "/register",
        "/change-password",
        # These redirect based on who you are, so a cached response sends the
        # next person to the wrong place.
        "/", "/logout",
        # An API response is as disclosive as the page that renders it.
        "/api/students/list", "/api/admin/consent-status",
    ])
    def test_private_paths_are_no_store(self, path):
        cc = self._headers(path).get("Cache-Control", "")
        assert "no-store" in cc, f"{path} is cacheable: {cc!r}"

    @pytest.mark.parametrize("path", [
        "/login", "/about", "/contact", "/privacy", "/forgot-password",
        "/static/css/app.css", "/static/js/app.js",
    ])
    def test_public_paths_stay_cacheable(self, path):
        assert "Cache-Control" not in self._headers(path), (
            f"{path} is the same for everyone; no-store just costs bandwidth")

    def test_the_list_is_of_public_paths_not_private_ones(self):
        """The old list enumerated what to protect, so anything forgotten was
        exposed. Enumerating what is public means anything forgotten is merely
        uncacheable, which is the safe direction to drift in."""
        assert not _app._is_public_path("/a-page-added-next-year")

    def test_every_public_path_is_a_real_route(self):
        """A stale entry is a page protected by accident or, worse, a typo
        next to a path that does matter."""
        routes = set()
        for r in _app.app.routes:
            routes.add(getattr(r, "path", None))
        missing = sorted(p for p in _app._PUBLIC_PATHS
                         if p not in routes and not p.startswith("/static"))
        # favicon.ico is served out of /static by the browser's own request.
        missing = [p for p in missing if p != "/favicon.ico"]
        assert not missing, "not routes: " + ", ".join(missing)

    @pytest.mark.parametrize("header,value", [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
    ])
    def test_the_other_headers_still_go_out(self, header, value):
        assert self._headers("/dashboard").get(header) == value
