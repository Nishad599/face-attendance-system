"""Smoke test: the application module actually imports.

Compiling and unit tests both passed while `sweep_stale_registration_files()`
called `time.time()` — which fails at runtime because line 17 of the app does
`from datetime import time`, shadowing the stdlib module with the `time`
CLASS. The deploy went red and the app would not start.

Importing the module executes everything at module scope: the connection
setup, the pragmas, table creation, the AttendanceSystem constructor and the
startup sweep. That is the cheapest way to catch wiring errors that only
appear when the process really starts.

Skipped automatically where the InsightFace buffalo_l model is absent (CI and
sandboxes), so it runs on the developer machine and the VM where it matters.
"""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_app_module = None
_import_error = None


def _load_app_once():
    """Import the app a single time, recording why if it is not possible.

    Enumerating individual dependencies here is a losing game — the first
    attempt checked insightface/cv2/fastapi and still tripped over
    email_validator. Instead: try it, and treat a MISSING DEPENDENCY as
    "skip" while any other failure is a real bug worth failing on.
    """
    global _app_module, _import_error
    if _app_module is not None or _import_error is not None:
        return

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    cwd = os.getcwd()
    os.chdir(ROOT)          # the app resolves templates/, models/, the db relatively
    try:
        _app_module = importlib.import_module("main_with_face_recognition")
    except ImportError as e:
        # A dependency this interpreter lacks — not a defect in the app.
        _import_error = e
    finally:
        os.chdir(cwd)


_load_app_once()

needs_model = pytest.mark.skipif(
    _app_module is None,
    reason=f"cannot import the app in this interpreter ({_import_error}); "
           "run the suite from venv_win to exercise this",
)


@needs_model
def test_app_module_imports_and_registers_routes():
    """The whole point: does `python main_with_face_recognition.py` get past
    module scope?"""
    assert hasattr(_app_module, "app"), "FastAPI app object missing"
    assert len(_app_module.app.routes) > 100, (
        f"only {len(_app_module.app.routes)} routes registered — something "
        "failed silently during import"
    )


@needs_model
def test_startup_sweep_is_callable():
    """It runs at module scope, so a NameError or AttributeError inside it
    stops the app booting at all — which is exactly what happened."""
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        assert isinstance(_app_module.sweep_stale_registration_files(), int)
    finally:
        os.chdir(cwd)


def test_stdlib_time_is_not_used_in_the_app_module():
    """A static guard that works without the face model.

    `from datetime import time` shadows the stdlib module, so any bare
    `time.time()` / `time.sleep()` in this file is a runtime error waiting to
    happen. Other modules are free to import `time` normally.
    """
    path = os.path.join(ROOT, "main_with_face_recognition.py")
    with open(path, encoding="utf-8") as f:
        source = f.read()

    assert "from datetime import time" in source, (
        "this guard assumes datetime.time is imported; if that changed, "
        "the guard can be removed"
    )

    offenders = [
        (n, line.strip())
        for n, line in enumerate(source.split("\n"), 1)
        if "time.time(" in line or "time.sleep(" in line
    ]
    assert not offenders, (
        "bare stdlib `time.` calls in main_with_face_recognition.py, where "
        f"`time` is datetime.time and these raise AttributeError: {offenders}"
    )
