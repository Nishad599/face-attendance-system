"""Every page renders, and the JavaScript inside it parses.

There is no build step here: the JS lives inline in the Jinja templates and
ships exactly as written. A stray bracket is not caught by anything until a
real person opens the page and finds a blank panel, because the parse fails
before the first line runs, so even code far from the typo never executes.

That has happened: four templates carried a stray `</div>` that only turned up
when the layout was measured in a browser, and a card once failed silently
because `app-badge-warn` was a class that did not exist. The markup half of
that is caught here too, by balance rather than by eye.

The JS check needs node, which the deploy runner has and a bare checkout may
not, so it skips rather than fails when node is missing.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TEMPLATES = os.path.join(ROOT, "templates")

# Includes (_navbar.html and friends) are fragments; they are checked through
# the pages that include them.
PAGES = sorted(n for n in os.listdir(TEMPLATES)
               if n.endswith(".html") and not n.startswith("_"))

# Enough context for any page to render. A template that needs more than this
# should say so by failing here.
CONTEXT = dict(request=None, session=None, institute=None, others=[],
               is_terminal=False, policy_version="1.0", retention_days=90)

node = shutil.which("node")
needs_node = pytest.mark.skipif(node is None, reason="node not installed")


def _env():
    from jinja2 import Environment, FileSystemLoader
    return Environment(loader=FileSystemLoader(TEMPLATES))


def _render(name, **extra):
    ctx = dict(CONTEXT)
    ctx.update(extra)
    return _env().get_template(name).render(**ctx)


# Inline only: a src= script is a CDN file, not ours.
_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def _inline_scripts(html):
    return [m.group(1) for m in _SCRIPT.finditer(html) if m.group(1).strip()]


def _node_check(js):
    """(ok, message). node --check parses without executing."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run([node, "--check", path],
                           capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    if not r.returncode:
        return True, ""
    lines = [ln for ln in r.stderr.splitlines() if "Error" in ln]
    return False, (lines[0] if lines else r.stderr[:200])


@pytest.mark.parametrize("name", PAGES)
def test_page_renders(name):
    assert _render(name).strip(), f"{name} rendered empty"


@pytest.mark.parametrize("name", PAGES)
def test_divs_are_balanced(name):
    """A stray </div> closes a container early, and the rest of the page
    moves. Four templates had one."""
    html = _render(name)
    opens = len(re.findall(r"<div\b", html))
    closes = len(re.findall(r"</div>", html))
    assert opens == closes, f"{name}: {opens} <div> vs {closes} </div>"


@needs_node
@pytest.mark.parametrize("name", PAGES)
def test_inline_javascript_parses(name):
    for i, js in enumerate(_inline_scripts(_render(name))):
        ok, msg = _node_check(js)
        assert ok, f"{name} inline script #{i}: {msg}"


@needs_node
def test_the_kiosk_branch_parses_too():
    """attendance.html has two entirely separate bodies behind
    {% if is_terminal %}, so rendering it once only ever checks one of them."""
    for js in _inline_scripts(_render("attendance.html", is_terminal=True)):
        ok, msg = _node_check(js)
        assert ok, f"attendance.html (kiosk): {msg}"


@pytest.mark.parametrize("name", PAGES)
def test_only_classes_that_exist_are_used(name):
    """`app-badge-warn` for `app-badge-warning` renders an unstyled span: no
    error, no warning, just a badge that is not a badge.

    Limited to the app- prefix, which is ours and defined in one file.
    """
    with open(os.path.join(ROOT, "static", "css", "app.css"),
              encoding="utf-8") as f:
        css = f.read()
    defined = set(re.findall(r"\.(app-[A-Za-z0-9_-]+)", css))
    html = _render(name)
    used = set()
    for attr in re.findall(r'class="([^"]*)"', html):
        used.update(c for c in attr.split() if c.startswith("app-"))
    # Classes built by string concatenation in JS are found the same way.
    for attr in re.findall(r"class=\\?'([^'\\]*)", html):
        used.update(c for c in attr.split() if c.startswith("app-"))
    missing = sorted(used - defined)
    assert not missing, f"{name} uses undefined classes: {missing}"


class TestNoOrphanedTemplates:
    """A page the app never renders is dead weight that still reads as live.

    admin.html and students.html sat here after /admin became /settings and
    /students became /people. Both were 400 lines of plausible-looking markup
    calling endpoints that had moved, and two tests were still pinning fixes
    inside them — guarding a page nobody could open.
    """

    @staticmethod
    def _rendered():
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        return set(re.findall(r'TemplateResponse\(\s*"([^"]+\.html)"', src))

    def test_every_page_on_disk_is_reachable(self):
        orphans = sorted(set(PAGES) - self._rendered())
        assert not orphans, (
            "no route renders these; delete them or route them: "
            + ", ".join(orphans))

    def test_every_rendered_page_exists(self):
        """The other direction: a typo'd template name is a 500 on a page that
        was presumably tested by hand once and never again."""
        on_disk = set(os.listdir(TEMPLATES))
        missing = sorted(n for n in self._rendered() if n not in on_disk)
        assert not missing, "rendered but absent: " + ", ".join(missing)
