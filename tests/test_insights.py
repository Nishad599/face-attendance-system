"""Insights: one home for analytics.

Trends, heatmaps, weekday patterns and at-risk lists were rendered three
separate ways — the reports page's Overview tab, the teacher portal's Analytics
tab and the dashboard — each with its own chart code over overlapping data.

The reports page's copy (the largest) MOVED to /insights rather than being
rewritten, so the figures people were already reading cannot have changed.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TEMPLATES = os.path.join(ROOT, "templates")
STATIC = os.path.join(ROOT, "static", "js")
APP_FILE = os.path.join(ROOT, "main_with_face_recognition.py")
jinja2 = pytest.importorskip("jinja2")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def page():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
    return env.get_template("insights.html").render(request=None)


@pytest.fixture(scope="module")
def insights_js():
    return read(os.path.join(STATIC, "insights.js"))


@pytest.fixture(scope="module")
def records_js():
    return read(os.path.join(STATIC, "attendance_management.js"))


class TestInsightsExists:
    def test_route_allows_teachers_too(self):
        src = read(APP_FILE)
        idx = src.find('@app.get("/insights"')
        assert idx != -1
        assert "require_teacher_or_admin" in src[idx:idx + 400]

    def test_in_the_nav_for_both_roles(self):
        import re
        nav = read(os.path.join(TEMPLATES, "_navbar.html"))
        m = re.search(r'href="/insights"[^>]*data-roles="([^"]+)"', nav)
        assert m and "admin" in m.group(1) and "teacher" in m.group(1)

    @pytest.mark.parametrize("chart", [
        "attendanceTrendChart", "attendanceHeatmap", "dayOfWeekChart",
        "slotComparisonChart", "atRiskList",
    ])
    def test_every_chart_came_across(self, page, chart):
        assert f'id="{chart}"' in page

    def test_it_loads_its_own_script(self, page):
        assert "/static/js/insights.js" in page
        assert "/static/js/attendance_management.js" not in page

    def test_chart_library_is_pinned(self, page):
        """The reports page loads chart.js from a CDN with no version at all,
        so an upstream release can change the charts without a deploy."""
        assert "chart.js@" in page


class TestItMovedRatherThanForked:
    @pytest.mark.parametrize("fn", [
        "loadClassAnalytics", "renderTrendChart", "renderHeatmap",
        "renderDayOfWeekChart", "renderSlotChart", "renderAtRiskList",
        "updateOverviewKPIs",
    ])
    def test_function_lives_in_insights_only(self, insights_js, records_js, fn):
        assert f"function {fn}" in insights_js, f"{fn} did not move"
        assert f"function {fn}" not in records_js, f"{fn} was copied, not moved"

    def test_records_page_no_longer_has_an_overview_tab(self):
        page = read(os.path.join(TEMPLATES, "attendance_management.html"))
        assert 'id="overview-tab"' not in page
        assert "switchTab('overview')" not in page

    def test_records_tab_bar_links_to_insights(self):
        page = read(os.path.join(TEMPLATES, "attendance_management.html"))
        assert 'href="/insights"' in page

    def test_no_dangling_calls_left_behind(self, records_js):
        """A call to a function that moved throws on click."""
        page = read(os.path.join(TEMPLATES, "attendance_management.html"))
        for fn in ("showStudentSparkline", "closeSparklineModal",
                   "renderSparklineChart"):
            assert f"{fn}(" not in page, f"{page!r:.0} dangling {fn} in markup"
            # the switchTab comment may name it; calls must be gone
            assert f"{fn}(" not in records_js.replace(f"// {fn}", "")

    def test_orphaned_sparkline_modal_is_gone(self):
        page = read(os.path.join(TEMPLATES, "attendance_management.html"))
        assert "sparklineModal" not in page


class TestStudentDeepLink:
    def test_records_row_opens_the_student(self, records_js):
        """It used to open a sparkline modal whose renderer has moved. The
        student's own panel is the better destination anyway."""
        assert "/people?student=" in records_js

    def test_people_honours_the_deep_link(self):
        page = read(os.path.join(TEMPLATES, "people.html"))
        assert "URLSearchParams" in page
        assert "'student'" in page or '"student"' in page
