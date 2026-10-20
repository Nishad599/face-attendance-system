"""Status colours are tokens, not hexes retyped on every page.

"At risk", "marked" and "needs attention" appear on nearly every page and had
no tokens at all, so each page hardcoded its own hex. The audit found two
different reds (#BF2600 and #B3372E) and two different greens (#006644 and
#0E6B4A) all meaning the same thing — and, worse, pale surfaces that stayed
near-white in dark mode. Measured, a dark-mode "ok" chip came out at 1.37:1.

These tests stop the drift coming back. They do not check that the design is
good; they check that nobody re-typed a colour the token scale already names.
"""

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CSS = os.path.join(ROOT, "static", "css", "app.css")
TEMPLATES = os.path.join(ROOT, "templates")
STATIC_JS = os.path.join(ROOT, "static", "js")

HEX = re.compile(r"#[0-9A-Fa-f]{6}")

# Colours the scale already names. Re-typing one of these is the drift.
BANNED = {
    "#BF2600", "#B3372E", "#006644", "#0E6B4A", "#36B37E",
    "#E3FCEF", "#FFEBE6", "#FFFAE6", "#DEEBFF", "#F2FAF6",
    "#8A4B00", "#FF8B00", "#0747A6", "#6B778C",
}


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def ui_files():
    out = []
    for d in (TEMPLATES, STATIC_JS):
        for f in sorted(os.listdir(d)):
            if f.endswith((".html", ".js")):
                out.append(os.path.join(d, f))
    return out


def body_of(path):
    """Source minus the kiosk palette.

    attendance.html defines --k-* literals on purpose: the terminal must stay
    dark whatever the light/dark toggle says, so it cannot inherit the themed
    tokens.
    """
    src = read(path)
    if os.path.basename(path) == "attendance.html":
        cut = src.find(".terminal-mode {")
        if cut != -1:
            return src[:cut]
    return src


class TestScaleExists:
    @pytest.mark.parametrize("role", ["ok", "warn", "bad", "info"])
    @pytest.mark.parametrize("slot", ["surface", "border", "text", "solid"])
    def test_token_is_defined(self, role, slot):
        assert f"--status-{role}-{slot}:" in read(CSS)

    @pytest.mark.parametrize("role", ["ok", "warn", "bad", "info"])
    def test_token_has_a_dark_value(self, role):
        """A light surface left undefined for dark mode is the bug that
        measured 1.37:1."""
        css = read(CSS)
        dark = css[css.index('[data-theme="dark"]'):]
        dark = dark[:dark.index("}")]
        assert f"--status-{role}-surface:" in dark
        assert f"--status-{role}-text:" in dark

    def test_links_have_their_own_token(self):
        """--color-primary doubles as a button fill with white text, so it
        cannot be lightened for dark mode; links measured 3.98:1."""
        css = read(CSS)
        assert "--color-link:" in css
        assert css.count("--color-link:") >= 2, "no dark-mode value for links"


class TestNoRetypedColours:
    @pytest.mark.parametrize("path", ui_files(), ids=lambda p: os.path.basename(p))
    def test_file_uses_tokens_not_status_hexes(self, path):
        found = {h.upper() for h in HEX.findall(body_of(path))} & BANNED
        assert not found, (
            f"{os.path.basename(path)} re-types colours the scale already names: "
            f"{sorted(found)} — use var(--status-*) instead")

    def test_app_css_rules_use_the_tokens(self):
        """The definitions are literal by necessity; the rules below them are
        not allowed to be."""
        css = read(CSS)
        cut = css.index("}", css.index('[data-theme="dark"]')) + 1
        found = {h.upper() for h in HEX.findall(css[cut:])} & BANNED
        assert not found, f"app.css rules still hardcode {sorted(found)}"

    def test_badges_are_tokenised(self):
        css = read(CSS)
        for role, tok in (("success", "ok"), ("danger", "bad"),
                          ("warning", "warn"), ("info", "info")):
            block = css[css.index(f".app-badge-{role} {{"):]
            block = block[:block.index("}")]
            assert f"var(--status-{tok}-" in block, (
                f".app-badge-{role} is not tokenised, so it keeps its light "
                "surface in dark mode")


class TestSvgAttributesStayValid:
    @pytest.mark.parametrize("path", ui_files(), ids=lambda p: os.path.basename(p))
    def test_no_var_in_presentation_attributes(self, path):
        """var() is not valid in an SVG stroke="" or fill="" attribute; it has
        to go through style instead. A blind find-and-replace puts it there."""
        offenders = re.findall(r'(?:stroke|fill)="var\(', read(path))
        assert not offenders, (
            f"{os.path.basename(path)} has var() in an SVG presentation "
            "attribute, where it silently does not apply")
