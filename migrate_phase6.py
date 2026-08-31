"""Phase 6 migration: subjects/timetable, half-day attendance, DPDP consent.

Adds:
  * subjects            - modules within a batch, each with its own minimum %
  * timetable           - which subject occupies which slot on which weekday.
                          Serves both the student timetable view and subject
                          attribution for attendance.
  * attendance.subject_id - denormalised AT MARK TIME so that later timetable
                          edits never rewrite attendance history.
  * courses.half_day_enabled - opt-in per batch. Half-day changes every
                          percentage the batch has ever shown, so it must not
                          switch on silently for existing batches.
  * consent_records     - DPDP Act 2023 consent for biometric processing.

Idempotent — safe to run multiple times. Backs up SQLite first.
"""
import os
import shutil
from datetime import datetime
from db import get_connection, is_postgres

DB_PATH = "attendance.db"


def backup_db():
    if is_postgres():
        return
    if not os.path.exists(DB_PATH):
        print(f"[WARN] {DB_PATH} not found; nothing to migrate yet.")
        return
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate6-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


def columns(cur, table):
    if is_postgres():
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        )
        return {row[0] for row in cur.fetchall()}
    return {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}


def add_column(cur, table, col, decl):
    try:
        if col not in columns(cur, table):
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            print(f"[OK] {table}: added column {col}")
        else:
            print(f"[skip] {table}.{col} already exists")
    except Exception as e:
        print(f"[WARN] {table}.{col}: {e}")


def migrate():
    backup_db()
    conn = get_connection()
    cur = conn.cursor()
    SERIAL = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # --- subjects / modules ---------------------------------------------
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS subjects (
                id {SERIAL},
                course_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                code TEXT,
                min_attendance REAL DEFAULT 75,
                start_date DATE,
                end_date DATE,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_subjects_course ON subjects (course_id)")
    print("[OK] subjects table ready")

    # --- timetable --------------------------------------------------------
    # weekday uses Python's date.weekday(): 0=Monday .. 6=Sunday.
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS timetable (
                id {SERIAL},
                course_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL,
                session_type TEXT NOT NULL,
                subject_id INTEGER,
                room TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_timetable_slot "
        "ON timetable (course_id, weekday, session_type)"
    )
    print("[OK] timetable table ready")

    # --- attendance -> subject -------------------------------------------
    # Recorded when the mark is made. If the timetable changes next term, past
    # attendance must keep pointing at the subject it was actually taken for.
    add_column(cur, "attendance", "subject_id", "INTEGER")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_attendance_subject ON attendance (subject_id)"
    )

    # --- half-day, opt-in per batch --------------------------------------
    add_column(cur, "courses", "half_day_enabled", "INTEGER DEFAULT 0")

    # --- DPDP Act 2023 consent -------------------------------------------
    # Face encodings are biometric data. The Act requires a record of free,
    # informed, specific consent, and the ability to withdraw it. One row per
    # grant/withdrawal so the history is auditable rather than overwritten.
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS consent_records (
                id {SERIAL},
                student_id INTEGER NOT NULL,
                purpose TEXT NOT NULL DEFAULT 'biometric_attendance',
                policy_version TEXT NOT NULL,
                granted INTEGER NOT NULL,
                granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                withdrawn_at TIMESTAMP,
                ip_address TEXT,
                user_agent TEXT
            )"""
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_consent_student "
        "ON consent_records (student_id, purpose)"
    )
    print("[OK] consent_records table ready")

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 6 migration complete.")


if __name__ == "__main__":
    migrate()
