"""/about must belong to whoever is looking at it.

/about and /contact were hardcoded marketing for one institute — its campus,
its programmes, and on the contact page a named individual with direct phone
numbers. Worse, both routes read their template straight off disk instead of
rendering it, so they *could* not have shown anything else. Every institute
onboarded onto the deployment would have been shown someone else's page.

These tests pin the two halves of the fix: the template carries no institute's
details of its own, and the route picks the right institute.
"""

import asyncio
import os
import re
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

ABOUT = os.path.join(ROOT, "templates", "about.html")


def _markup_only(path):
    """Strip <style>, <script> and comments: the prose, not my explanations.

    Asserting against a whole template has caught my own commentary more than
    once — a comment that says "this used to say CDAC" fails a test looking
    for "CDAC".
    """
    with open(path, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r"<style\b.*?</style>", "", src, flags=re.S)
    src = re.sub(r"<script\b.*?</script>", "", src, flags=re.S)
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    return src


class TestTemplateIsTenantNeutral:
    def test_no_institute_specific_strings(self):
        body = _markup_only(ABOUT).lower()
        for needle in ("cdac", "c-dac", "kharghar", "dbda", "dac ", "mumbai",
                       "maharashtra", "nishad"):
            assert needle not in body, (
                f"about.html still names one institute: {needle!r}")

    def test_no_phone_numbers_or_emails_hardcoded(self):
        body = _markup_only(ABOUT)
        # a mailto: with a literal address, as opposed to {{ ... }}
        assert not re.search(r"mailto:(?!\{\{)[^\"'\s]*@", body), \
            "a literal email address is baked into about.html"
        # 10+ consecutive digits, or the +91-xxxxx shapes
        assert not re.search(r"\+\d[\d\s\-]{8,}", body), \
            "a literal phone number is baked into about.html"

    def test_every_detail_comes_from_the_record(self):
        """Each field is rendered, and each is guarded so an institute that
        left it blank gets no empty row."""
        body = _markup_only(ABOUT)
        for field in ("name", "tagline", "about", "address", "phone",
                      "website", "contact_email"):
            assert "institute." + field in body, f"{field} is never rendered"
        for field in ("tagline", "about", "address", "phone", "website"):
            assert re.search(r"\{%\s*if\b[^%]*institute\." + field, body), \
                f"{field} renders unconditionally; blank leaves an empty row"


@needs_app
class TestRoutePicksTheRightInstitute:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def loop(self):
        return asyncio.new_event_loop()

    def _ctx(self, loop, session=None, slug=None, conn=None):
        """Call the handler and read the context it would have rendered."""
        captured = {}
        real = _app.templates.TemplateResponse

        def spy(name, ctx, *a, **kw):
            captured["name"] = name
            captured["ctx"] = ctx
            return real(name, ctx, *a, **kw)

        _app.templates.TemplateResponse = spy
        try:
            loop.run_until_complete(
                _app.about_page(_FakeRequest(), institute=slug, session=session))
        finally:
            _app.templates.TemplateResponse = real
        return captured["ctx"]

    def test_signed_in_admin_sees_their_own_institute(self, db, two_institutes, loop):
        _app.attendance_system.conn = db
        ctx = self._ctx(loop, session=session_for("admin", 2))
        assert ctx["institute"]["name"] == "Southpoint Academy"

    def test_the_other_institute_is_not_offered(self, db, two_institutes, loop):
        """Not a leak on its own — names are public at the login screen — but
        a signed-in admin has no business being shown a picker."""
        _app.attendance_system.conn = db
        ctx = self._ctx(loop, session=session_for("admin", 1))
        assert ctx["others"] == []

    def test_anonymous_visitor_gets_a_picker_when_several_exist(self, db, two_institutes, loop):
        _app.attendance_system.conn = db
        ctx = self._ctx(loop, session=None)
        assert ctx["institute"] is None
        assert {i["name"] for i in ctx["others"]} == {
            "Northgate Institute", "Southpoint Academy"}

    def test_anonymous_visitor_can_name_one_by_slug(self, db, two_institutes, loop):
        _app.attendance_system.conn = db
        ctx = self._ctx(loop, session=None, slug="southpoint")
        assert ctx["institute"]["name"] == "Southpoint Academy"

    def test_an_unknown_slug_does_not_fall_back_to_someone(self, db, two_institutes, loop):
        _app.attendance_system.conn = db
        ctx = self._ctx(loop, session=None, slug="nope")
        assert ctx["institute"] is None

    def test_a_deactivated_institute_is_not_shown(self, db, two_institutes, loop):
        _app.attendance_system.conn = db
        db.execute("UPDATE institutes SET is_active = 0 WHERE id = 2")
        db.commit()
        ctx = self._ctx(loop, session=None, slug="southpoint")
        assert ctx["institute"] is None

    def test_single_institute_needs_no_slug_and_no_login(self, db, seeded, loop):
        """The common case: one institute, an anonymous visitor, and the page
        still has to say whose it is."""
        _app.attendance_system.conn = db
        db.execute(
            "INSERT INTO institutes (id, name, slug, is_active, created_at, "
            "tagline) VALUES (1, 'Only One', 'only-one', 1, '2026-01-01', 'Hi')")
        db.execute("UPDATE courses SET institute_id = 1")
        db.commit()
        ctx = self._ctx(loop, session=None)
        assert ctx["institute"]["name"] == "Only One"
        assert ctx["institute"]["tagline"] == "Hi"

    def test_contact_redirects_rather_than_404s(self, loop):
        """It was linked from nowhere, but an old bookmark should still land."""
        r = loop.run_until_complete(_app.contact_page())
        assert r.status_code == 307
        assert r.headers["location"] == "/about"


@needs_app
class TestAdminCanFillTheDetailsIn:
    """A page that renders from the record is useless if nothing can write it.

    The fields have to survive the whole round trip — PUT /api/admin/institute
    then GET it back — or an admin fills the form in, sees it save, and /about
    stays blank.
    """

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def admin(self, db, two_institutes):
        _app.attendance_system.conn = db
        return session_for("admin", 1)

    # Newlines on purpose: /about renders these with white-space: pre-line,
    # so a multi-line address has to make it through intact.
    FIELDS = {"tagline": "A tagline",
              "about": "Para one.\nPara two.",
              "address": "1 Road\nCity",
              "phone": "+00 000 0000",
              "website": "https://northgate.test"}

    def _put(self, **overrides):
        """The name is the one field the endpoint requires on every write,
        because an institute without one shows a blank heading on every
        page and every report."""
        body = dict(self.FIELDS, name="Northgate Institute")
        body.update(overrides)
        return body

    def test_round_trip(self, admin, db):
        loop = asyncio.new_event_loop()
        r = loop.run_until_complete(_app.update_institute(self._put(), admin))
        assert r["success"] is True, r
        back = loop.run_until_complete(_app.get_institute(admin))
        for field, value in self.FIELDS.items():
            assert back["institute"][field] == value, f"{field} did not survive"

    def test_blanking_a_field_clears_it(self, admin, db):
        """Not the same as omitting it: the form always sends all five, so an
        emptied box has to actually remove the row from /about."""
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_app.update_institute(self._put(), admin))
        loop.run_until_complete(_app.update_institute(
            {"name": "Northgate Institute", "tagline": ""}, admin))
        back = loop.run_until_complete(_app.get_institute(admin))
        assert not back["institute"]["tagline"]
        assert back["institute"]["about"] == self.FIELDS["about"], \
            "omitted fields must be left alone"

    @pytest.mark.parametrize("field,limit", [
        ("tagline", 160), ("about", 4000), ("address", 400),
        ("phone", 60), ("website", 200)])
    def test_overlong_values_are_refused(self, admin, db, field, limit):
        """These render on a public page; there is no reason to accept a
        megabyte of it."""
        loop = asyncio.new_event_loop()
        r = loop.run_until_complete(
            _app.update_institute(self._put(**{field: "x" * (limit + 1)}), admin))
        assert r["success"] is False
        assert loop.run_until_complete(
            _app.get_institute(admin))["institute"][field] in (None, "")

    def test_omitting_a_field_does_not_wipe_it(self, admin, db):
        """contact_email used to be written unconditionally alongside the name,
        so a caller sending {name, tagline} silently cleared the institute's
        email address — the one field students use to reach anybody."""
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_app.update_institute(
            {"name": "Northgate Institute", "contact_email": "office@ng.test"},
            admin))
        loop.run_until_complete(_app.update_institute(
            {"name": "Northgate Institute", "tagline": "later"}, admin))
        back = loop.run_until_complete(_app.get_institute(admin))
        assert back["institute"]["contact_email"] == "office@ng.test"
        assert back["institute"]["tagline"] == "later"

    def test_a_bad_email_is_still_refused(self, admin, db):
        loop = asyncio.new_event_loop()
        r = loop.run_until_complete(
            _app.update_institute(self._put(contact_email="nope"), admin))
        assert r["success"] is False

    def test_the_audit_entry_names_fields_not_values(self, admin, db):
        """`about` is 4000 characters and the log renders as a list."""
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_app.update_institute(self._put(), admin))
        row = db.execute("SELECT details FROM audit_log WHERE action = "
                         "'update_institute' ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None, "the rebrand was not logged"
        assert "tagline" in row[0]
        assert self.FIELDS["about"] not in row[0]

    def test_an_admin_cannot_rebrand_another_institute(self, admin, db):
        """There is no id in this endpoint — it writes the caller's own
        institute — so the test is that institute 2 is untouched."""
        loop = asyncio.new_event_loop()
        loop.run_until_complete(
            _app.update_institute(self._put(tagline="mine"), admin))
        assert db.execute(
            "SELECT tagline FROM institutes WHERE id = 2").fetchone()[0] is None


class _FakeRequest:
    """TemplateResponse only needs `request` to be present in the context."""
    scope = {"type": "http"}
    headers = {}
    cookies = {}

    def __init__(self):
        self.url = None
