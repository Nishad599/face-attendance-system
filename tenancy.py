"""Tenant scoping: which institute a request may see.

One deployment can serve several institutes. Nearly every table already carries
a `course_id`, and `courses.institute_id` says whose batch that is, so almost
all scoping reduces to "which course ids may this session touch".

THE CORE RULE — tenancy is inert at one institute.

    Every function here falls back to today's unscoped behaviour while
    `institute_count(conn) <= 1`. A single-institute deployment therefore
    behaves exactly as it did before Phase 10, and the scoping rules only
    start biting when a second institute is created. That is what makes this
    migration safe to ship to a running server.

Written so it can be unit-tested without importing the app (which needs the
buffalo_l model). Every function takes an open connection and never raises
except where a caller is explicitly asking for a permission check.
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Shape of the deployment
# ---------------------------------------------------------------------------

def institute_count(conn) -> int:
    """How many institutes exist. 0 means pre-migration."""
    try:
        return conn.execute("SELECT COUNT(*) FROM institutes").fetchone()[0]
    except Exception:
        return 0


def is_single_tenant(conn) -> bool:
    """True while scoping must stay transparent.

    Also true before migrate_phase10 has run, so the app keeps working on an
    un-migrated database rather than refusing every request.
    """
    return institute_count(conn) <= 1


def default_institute_id(conn) -> Optional[int]:
    """The only institute, when there is only one."""
    try:
        row = conn.execute("SELECT id FROM institutes ORDER BY id LIMIT 1").fetchone()
        return row[0] if row else None
    except Exception:
        return None


def list_institutes(conn, active_only: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT id, name, slug, contact_email, is_active FROM institutes"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY name"
    try:
        rows = conn.execute(sql).fetchall()
    except Exception:
        return []
    return [{"id": r[0], "name": r[1], "slug": r[2],
             "contact_email": r[3], "is_active": bool(r[4])} for r in rows]


def institute_by_slug(conn, slug) -> Optional[int]:
    if not slug:
        return None
    try:
        row = conn.execute(
            "SELECT id FROM institutes WHERE slug = ? AND is_active = 1",
            (str(slug).strip().lower(),),
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Resolving a principal's institute
# ---------------------------------------------------------------------------

def institute_for_session(session: Optional[Dict[str, Any]]) -> Optional[int]:
    """The institute this session is confined to.

    None means "not confined" — a superadmin, or a session created before
    Phase 10 on a single-institute deployment. Callers must treat None as
    "every institute", which is safe precisely because the single-tenant
    fallback makes it the only possibility outside a superadmin.
    """
    if not session:
        return None
    info = session.get("user_info") or {}
    if info.get("role") == "superadmin":
        return None
    value = info.get("institute_id")
    return int(value) if value is not None else None


def is_superadmin(session: Optional[Dict[str, Any]]) -> bool:
    if not session:
        return False
    return (session.get("user_info") or {}).get("role") == "superadmin"


def institute_of_course(conn, course_id) -> Optional[int]:
    if course_id is None:
        return None
    try:
        row = conn.execute(
            "SELECT institute_id FROM courses WHERE id = ?", (int(course_id),)
        ).fetchone()
        return row[0] if row and row[0] is not None else None
    except Exception:
        return None


def institute_of_student(conn, student_db_id) -> Optional[int]:
    if student_db_id is None:
        return None
    try:
        row = conn.execute(
            "SELECT c.institute_id FROM students s "
            "LEFT JOIN courses c ON c.id = s.course_id WHERE s.id = ?",
            (int(student_db_id),),
        ).fetchone()
        return row[0] if row and row[0] is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Course scoping — what nearly every query actually needs
# ---------------------------------------------------------------------------

def course_ids_for_institute(conn, institute_id, active_only: bool = False) -> List[int]:
    """Every batch belonging to an institute."""
    if institute_id is None:
        return []
    sql = "SELECT id FROM courses WHERE institute_id = ?"
    if active_only:
        sql += " AND is_active = 1"
    try:
        return [r[0] for r in conn.execute(sql, (int(institute_id),)).fetchall()]
    except Exception:
        return []


def visible_course_ids(conn, session: Optional[Dict[str, Any]]) -> Optional[List[int]]:
    """Course ids this session may read, or None for "no restriction".

    None is returned for a superadmin and on a single-institute deployment, so
    existing callers that treat None as "all batches" — which is what
    teacher_allowed_course_ids() already does for admins — keep working
    unchanged.
    """
    if is_superadmin(session):
        return None
    if is_single_tenant(conn):
        return None
    institute_id = institute_for_session(session)
    if institute_id is None:
        # Multi-tenant deployment but the session predates Phase 10. Refuse to
        # guess: an empty scope shows nothing rather than everything.
        return []
    return course_ids_for_institute(conn, institute_id)


def course_in_institute(conn, course_id, institute_id) -> bool:
    """Whether a batch belongs to an institute. True when unscoped."""
    if institute_id is None:
        return True
    return institute_of_course(conn, course_id) == int(institute_id)


def session_may_touch_course(conn, session, course_id) -> bool:
    """The single check an endpoint should make before acting on a batch."""
    if is_superadmin(session):
        return True
    if is_single_tenant(conn):
        return True
    return course_in_institute(conn, course_id, institute_for_session(session))


# ---------------------------------------------------------------------------
# Holidays
# ---------------------------------------------------------------------------

def holiday_scope_clause(conn, institute_id):
    """(sql_fragment, params) selecting holidays that apply to an institute.

    `holidays.course_id IS NULL` has always meant "every batch". With one
    institute that was correct; with two it would hand one customer's public
    holiday to another's students, so an institute-wide holiday is now scoped
    by institute_id as well.

    On a single-institute deployment the clause stays wide open, which keeps
    behaviour identical and also covers rows written before Phase 10 whose
    institute_id was never set.
    """
    if institute_id is None or is_single_tenant(conn):
        return "", []
    return " AND (institute_id = ? OR institute_id IS NULL)", [int(institute_id)]
