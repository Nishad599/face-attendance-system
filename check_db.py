"""check_db.py — verify the database has everything the app needs.

Run it anywhere the app runs:  python check_db.py
Works for both SQLite and PostgreSQL (via DATABASE_URL env var).
"""
import sys
from db import get_connection, is_postgres

REQUIRED_COLUMNS = {
    "students": ["course_id", "password_hash", "must_change_password", "dob",
                 "joining_date"],
    "attendance": ["course_id", "is_late", "subject_id"],
    "slot_attendance": ["course_id"],
    "users": ["email", "institute_id"],
    "subjects": ["sequence", "faculty", "exam_date", "mid_quiz_date"],
    # phase 10: tenancy. courses.institute_id is the anchor every other table
    # reaches its institute through; holidays needs its own because an
    # institute-wide holiday has no course_id to resolve from.
    "courses": ["terminal_pin_hash", "half_day_enabled", "institute_id"],
    "holidays": ["course_id", "institute_id"],
    # phase 11: without this the audit log is read unscoped, and one
    # institute's admin sees every platform-level action a superadmin took.
    "audit_log": ["course_id", "institute_id"],
    # phase 12: /about renders from these. Without them the route falls back
    # to the institute's name alone, which is survivable but wrong.
    "institutes": ["tagline", "about", "address", "phone", "website"],
}
REQUIRED_TABLES = ["users", "teacher_batches", "sessions", "grievances",
                   "email_log", "password_resets", "face_registration_requests",
                   "audit_log", "profile_change_requests",
                   # phase 5
                   "login_attempts", "leave_requests", "alert_log",
                   # phase 6
                   "subjects", "timetable", "consent_records",
                   # phase 7
                   "academic_events",
                   # phase 10
                   "institutes"]

# Indexes the app's correctness depends on, not just its speed.
# phase 9: `attendance` carries no UNIQUE constraint of its own, so the two
# `INSERT OR IGNORE` writers only actually dedupe while this index exists.
REQUIRED_INDEXES = {
    "idx_attendance_unique_slot": "attendance",
    # phase 13: slot_attendance had no index at all, and it is written on
    # every mark and read by every report. The first of these answers "has
    # this student already marked this slot today?", which runs once per
    # recognised face.
    "idx_slot_attendance_student_date": "slot_attendance",
    "idx_slot_attendance_date_slot": "slot_attendance",
}


def table_exists(cur, table_name):
    if is_postgres():
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table_name,),
        )
        return cur.fetchone() is not None
    else:
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return cur.fetchone() is not None


def index_exists(cur, index_name):
    try:
        if is_postgres():
            cur.execute(
                "SELECT 1 FROM pg_indexes "
                "WHERE schemaname = 'public' AND indexname = %s",
                (index_name,),
            )
        else:
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (index_name,),
            )
        return cur.fetchone() is not None
    except Exception:
        return False


def cols(cur, table):
    try:
        if is_postgres():
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            )
            return {r[0] for r in cur.fetchall()}
        else:
            return {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def main():
    try:
        conn = get_connection()
    except Exception as e:
        print(f"[FAIL] cannot open database connection: {e}")
        sys.exit(2)
        
    cur = conn.cursor()
    missing = []

    db_type = "PostgreSQL" if is_postgres() else "SQLite"
    print(f"=== Checking {db_type} Database ===\n")

    print("Tables:")
    for t in REQUIRED_TABLES:
        ok = table_exists(cur, t)
        print(f"  [{'OK ' if ok else 'MISSING'}] table {t}")
        if not ok:
            missing.append(f"table:{t}")

    print("\nColumns:")
    for table, need in REQUIRED_COLUMNS.items():
        if not table_exists(cur, table):
            print(f"  [MISSING] table {table} does not exist!")
            missing.append(f"table:{table}")
            continue
        have = cols(cur, table)
        for c in need:
            ok = c in have
            print(f"  [{'OK ' if ok else 'MISSING'}] {table}.{c}")
            if not ok:
                missing.append(f"{table}.{c}")

    print("\nIndexes:")
    for idx, table in REQUIRED_INDEXES.items():
        ok = index_exists(cur, idx)
        print(f"  [{'OK ' if ok else 'MISSING'}] {idx} on {table}")
        if not ok:
            missing.append(f"index:{idx}")

    # Quick data sanity
    print("\nData:")
    try:
        cur.execute("SELECT COUNT(*) FROM students")
        n_students = cur.fetchone()[0]
        
        # Check if courses table exists before querying
        if table_exists(cur, "courses"):
            cur.execute("SELECT COUNT(*) FROM courses")
            n_courses = cur.fetchone()[0]
        else:
            n_courses = 0
            
        print(f"  students: {n_students}   courses: {n_courses}")
        if table_exists(cur, "users"):
            cur.execute("SELECT COUNT(*) FROM users")
            n_users = cur.fetchone()[0]
            print(f"  staff accounts (users): {n_users}")
    except Exception as e:
        print(f"  (could not read counts: {e})")

    conn.close()

    print("\n" + "=" * 40)
    if not missing:
        print("[RESULT] DB is up to date. Nothing to migrate.")
        return

    print(f"[RESULT] {len(missing)} thing(s) missing:")
    for m in missing:
        print(f"   - {m}")
    print("\nFix — run database setup or migrations to fix the schema.")
    sys.exit(1)


if __name__ == "__main__":
    main()
