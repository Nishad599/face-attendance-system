"""The fast match path has to give exactly the old answer.

Matching was a Python loop over the gallery that, per known face, did two
np.linalg.norm divisions and a 512-element dot product -- and re-normalised
the probe *inside* the loop, so once per student rather than once per face.
There were two copies of it, the staff camera and the terminal, plus a third
in a since-deleted face-login endpoint.

It is now one pre-normalised float32 matrix and one `M @ probe`. Measured on
the dev machine, per face:

    gallery      before     after
      100       0.58 ms    0.12 ms      5x
      500       2.98 ms    0.13 ms     22x
     2000      12.76 ms    0.12 ms    104x

Speed is not what these tests are about. The risk in this change is that a
matrix row stops lining up with the names and ids beside it, and an off-by-one
there marks the wrong student present -- a silent, plausible-looking error that
nobody would catch from the UI. So the tests compare against a brute-force
loop computed independently, and check the ordering invariant directly.
"""

import os
import sys

import numpy as np
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


def brute_force(probe, encodings, names, ids):
    """The old implementation, written out again on purpose.

    Not imported and not refactored -- if it shared code with what it checks,
    it would agree with a shared bug.
    """
    p = np.asarray(probe, dtype=np.float64)
    p = p / np.linalg.norm(p)
    best_i, best = -1, -2.0
    for i, k in enumerate(encodings):
        k = np.asarray(k, dtype=np.float64)
        sim = float(np.dot(p, k / np.linalg.norm(k)))
        if sim > best:
            best, best_i = sim, i
    return ids[best_i], names[best_i], best


@needs_app
class TestTheFastPathAgrees:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def gallery(self, db, request):
        """A gallery of random embeddings, one institute.

        Random vectors in 512 dimensions sit at near-identical angles to each
        other, so the top two similarities are close together -- which is the
        hard case for this test, not the easy one: any row/label mismatch or
        precision loss shows up as a different winner.
        """
        from datetime import datetime
        n = getattr(request, "param", 50)
        rng = np.random.default_rng(7)
        cur = db.cursor()
        cur.execute("INSERT INTO institutes (id, name, slug, is_active, created_at) "
                    "VALUES (1, 'I', 'i', 1, ?)", (datetime.now().isoformat(),))
        cur.execute("INSERT INTO courses (id, name, start_date, end_date, is_active, "
                    "created_at, institute_id) VALUES "
                    "(1, 'B', '2026-01-01', '2027-12-31', 1, ?, 1)",
                    (datetime.now().isoformat(),))
        encs = {}
        for i in range(1, n + 1):
            e = rng.standard_normal(512)
            encs[i] = e
            cur.execute(
                "INSERT INTO students (id, student_id, name, email, status, "
                "course_id, registration_date, face_encoding) "
                "VALUES (?, ?, ?, ?, 'active', 1, ?, ?)",
                (i, f"R{i:05}", f"Student {i}", f"s{i}@x.test",
                 datetime.now().isoformat(), e.astype(np.float64).tobytes()))
        db.commit()
        _app.attendance_system.conn = db
        _app.attendance_system.load_student_faces()
        return {"encs": encs, "rng": rng, "n": n}

    @pytest.mark.parametrize("gallery", [1, 2, 50, 300], indirect=True)
    def test_it_picks_the_same_student_as_the_loop(self, gallery):
        encodings, names, ids = _app.attendance_system.gallery_for(None)
        for _ in range(12):
            probe = gallery["rng"].standard_normal(512)
            got = _app.best_match(probe, None)
            want = brute_force(probe, encodings, names, ids)
            assert got[0] == want[0], "different student chosen"
            assert got[1] == want[1]
            assert got[2] == pytest.approx(want[2], abs=1e-4)

    def test_an_exact_enrolled_face_scores_one(self, gallery):
        """The end-to-end sanity check: a student's own embedding must come
        back as that student, at a similarity of 1."""
        for sid in (1, gallery["n"] // 2 or 1, gallery["n"]):
            db_id, name, sim = _app.best_match(gallery["encs"][sid], None)
            assert db_id == sid, f"student {sid} matched as {db_id}"
            assert name == f"Student {sid}"
            assert sim == pytest.approx(1.0, abs=1e-4)

    def test_float32_does_not_cost_a_correct_match(self, gallery):
        """The matrix is float32 while the blobs are float64. A cosine
        similarity does not need the precision, but that is a claim worth
        checking rather than asserting."""
        for sid, enc in list(gallery["encs"].items())[:10]:
            assert _app.best_match(enc, None)[0] == sid

    def test_row_order_matches_label_order(self, gallery):
        """The invariant the whole change rests on, checked directly: row i of
        the matrix must be the unit form of the encoding for ids[i]."""
        M, names, ids = _app.attendance_system.match_gallery(None)
        assert M is not None
        assert M.shape == (gallery["n"], 512)
        assert len(ids) == len(names) == gallery["n"]
        for i, sid in enumerate(ids):
            want = gallery["encs"][sid]
            want = want / np.linalg.norm(want)
            assert float(np.dot(M[i], want.astype(M.dtype))) == pytest.approx(1.0, abs=1e-4)

    def test_rows_are_unit_length(self, gallery):
        M, _n, _i = _app.attendance_system.match_gallery(None)
        norms = np.linalg.norm(M, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-4)


@needs_app
class TestEdgeCasesThatUsedToBeImpossible:
    """A matmul has failure modes a loop did not: a zero-length gallery has no
    matrix at all, and a probe of the wrong width raises instead of being
    skipped per row."""

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def test_an_empty_gallery_matches_nobody(self, db):
        _app.attendance_system.conn = db
        _app.attendance_system.load_student_faces()
        assert _app.best_match(np.ones(512), None) == (None, None, 0.0)

    def test_a_zero_vector_matches_nobody(self, db):
        """Rather than dividing by zero. InsightFace should never emit one,
        but it arrives from outside this process."""
        _app.attendance_system.conn = db
        _app.attendance_system.load_student_faces()
        assert _app.best_match(np.zeros(512), None)[0] is None

    def test_none_matches_nobody(self, db):
        _app.attendance_system.conn = db
        _app.attendance_system.load_student_faces()
        assert _app.best_match(None, None) == (None, None, 0.0)

    def test_a_wrong_width_probe_is_refused_not_raised(self, db, seeded):
        """A 128-D legacy embedding against a 512-D gallery. The loop skipped
        mismatched rows; a matmul would raise, and a traceback at the terminal
        reads as "the camera is broken"."""
        _app.attendance_system.conn = db
        _app.attendance_system.load_student_faces()
        assert _app.best_match(np.ones(128), None)[0] is None

    def test_a_mixed_dimension_gallery_falls_back_rather_than_breaking(self, db):
        """Both 512-D and 128-D embeddings on one deployment: np.stack would
        raise, so no matrix is built and the per-row loop handles it."""
        from datetime import datetime
        cur = db.cursor()
        cur.execute("INSERT INTO courses (id, name, start_date, end_date, is_active, "
                    "created_at) VALUES (1, 'B', '2026-01-01', '2027-12-31', 1, ?)",
                    (datetime.now().isoformat(),))
        rng = np.random.default_rng(3)
        for i, dim in ((1, 512), (2, 128)):
            cur.execute(
                "INSERT INTO students (id, student_id, name, email, status, "
                "course_id, registration_date, face_encoding) "
                "VALUES (?, ?, ?, ?, 'active', 1, ?, ?)",
                (i, f"R{i}", f"S{i}", f"s{i}@x.test", datetime.now().isoformat(),
                 rng.standard_normal(dim).astype(np.float64).tobytes()))
        db.commit()
        _app.attendance_system.conn = db
        _app.attendance_system.load_student_faces()
        assert _app.attendance_system.match_gallery(None)[0] is None, \
            "a matrix should not be built from mixed dimensions"
        # and matching still works, via the loop
        enc = np.frombuffer(
            db.execute("SELECT face_encoding FROM students WHERE id = 1")
              .fetchone()[0], dtype=np.float64)
        assert _app.best_match(enc, None)[0] == 1


@needs_app
class TestTheInstituteBoundarySurvived:
    """The matrix is built per institute, so the fast path is also where a
    cross-institute match would now come from."""

    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    @pytest.fixture
    def two(self, db, two_institutes):
        rng = np.random.default_rng(11)
        encs = {}
        for sid, roll in ((1, "NG001"), (2, "SP001")):
            e = rng.standard_normal(512)
            encs[roll] = e
            db.execute("UPDATE students SET face_encoding = ? WHERE student_id = ?",
                       (e.astype(np.float64).tobytes(), roll))
        db.commit()
        _app.attendance_system.conn = db
        _app.attendance_system.load_student_faces()
        return encs

    def test_one_institute_cannot_match_the_others_student(self, two, db):
        sp_id = db.execute(
            "SELECT id FROM students WHERE student_id = 'SP001'").fetchone()[0]
        db_id, _name, sim = _app.best_match(two["SP001"], institute_id=1)
        assert db_id != sp_id, "matched another institute's student"
        assert sim < 0.9, f"a near-exact cross-institute match at {sim}"

    def test_its_own_student_still_matches(self, two, db):
        ng_id = db.execute(
            "SELECT id FROM students WHERE student_id = 'NG001'").fetchone()[0]
        assert _app.best_match(two["NG001"], institute_id=1)[0] == ng_id

    def test_each_institute_gets_its_own_matrix(self, two):
        a, _n, ids_a = _app.attendance_system.match_gallery(1)
        b, _m, ids_b = _app.attendance_system.match_gallery(2)
        assert a is not None and b is not None
        assert a.shape[0] == len(ids_a) == 1
        assert b.shape[0] == len(ids_b) == 1
        assert set(ids_a).isdisjoint(ids_b)


@needs_app
class TestTheGalleryIsRebuiltWhenItChanges:
    @pytest.fixture(autouse=True)
    def _restore(self):
        original = _app.attendance_system.conn
        yield
        _app.attendance_system.conn = original

    def test_a_reload_picks_up_a_new_face(self, db, seeded):
        """A matrix cached past an enrolment would leave the new student
        unrecognisable until the next restart."""
        from datetime import datetime
        _app.attendance_system.conn = db
        _app.attendance_system.load_student_faces()
        before = _app.attendance_system.match_gallery(None)[0]
        n_before = 0 if before is None else before.shape[0]

        enc = np.random.default_rng(5).standard_normal(512)
        db.execute(
            "INSERT INTO students (student_id, name, email, status, course_id, "
            "registration_date, face_encoding) VALUES "
            "('NEW1', 'New Face', 'new@x.test', 'active', 1, ?, ?)",
            (datetime.now().isoformat(), enc.astype(np.float64).tobytes()))
        db.commit()
        _app.attendance_system.load_student_faces()

        after = _app.attendance_system.match_gallery(None)[0]
        assert after is not None and after.shape[0] == n_before + 1
        assert _app.best_match(enc, None)[1] == "New Face"


class TestThePrintStormIsGone:
    """compare_faces_optimized() printed one [DEBUG] line per gallery entry
    per face, then the whole similarity list. Nothing ever called it. On
    systemd each print is a synchronous journald write, so at 500 students
    that was 500-odd of them per face, for a function that did not run."""

    @staticmethod
    def _src():
        with open(os.path.join(ROOT, "asian_face_model.py"), encoding="utf-8") as f:
            return f.read()

    def test_the_dead_matcher_is_deleted(self):
        assert "def compare_faces_optimized" not in self._src()

    def test_nothing_prints_per_frame_or_per_face(self):
        """Startup messages may still print -- once per process. Anything
        inside the detection path must be a logger call."""
        src = self._src()
        start = src.index("def detect_faces_optimized")
        end = src.index("# There was a compare_faces_optimized")
        body = src[start:end]
        assert "print(" not in body, "a print() remains in the per-frame path"
        assert "logger." in body

    def test_no_traceback_is_printed_straight_to_stdout(self):
        assert "traceback.print_exc()" not in self._src()

    def test_a_logger_exists_rather_than_bare_prints(self):
        src = self._src()
        assert "logging.getLogger(__name__)" in src


@needs_app
class TestOneSlotManagerNotOnePerRequest:
    """Six handlers built a fresh AttendanceSlotManager per request, two of
    them the hottest paths in the app. Its constructor opens a connection and
    runs 5x CREATE TABLE IF NOT EXISTS plus two commits -- two write locks on a
    database that takes one writer at a time, about 80 a minute per terminal,
    to re-create tables that already existed."""

    def test_the_accessor_returns_the_same_instance(self):
        assert _app.get_slot_manager() is _app.get_slot_manager()

    def test_it_is_the_app_wide_one(self):
        assert _app.get_slot_manager() is _app.attendance_manager

    def test_no_handler_builds_its_own_any_more(self):
        """Asked of the request handlers specifically.

        A plain text search flags the lazy fallback inside get_slot_manager()
        itself, which is the one place allowed to construct one -- so the
        question has to be "does any *route* build one", not "does the string
        appear indented anywhere".
        """
        import ast
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        offenders = []
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            routes = [d.args[0].value for d in node.decorator_list
                      if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                      and d.func.attr in ("get", "post", "put", "delete")
                      and d.args and isinstance(d.args[0], ast.Constant)]
            if not routes:
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "create_slot_manager_instance"):
                    offenders.append(routes[0])
        assert not offenders, (
            "these build a slot manager per request: " + ", ".join(offenders))

    def test_a_config_change_still_reaches_the_shared_instance(self):
        """Sharing is only safe because the timings are the one thing it
        caches, and the write path reloads them."""
        with open(os.path.join(ROOT, "main_with_face_recognition.py"),
                  encoding="utf-8") as f:
            src = f.read()
        start = src.index('@app.put("/api/admin/session-config/{session_type}")')
        end = src.index("\n@app.", start + 10)
        assert "reload_config()" in src[start:end]
