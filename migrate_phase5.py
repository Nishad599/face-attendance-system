"""Phase 5 migration: durable login throttling, planned leave, alert de-duplication.

Idempotent — safe to run multiple times. Backs up SQLite first if SQLite is used.
Works on both SQLite and PostgreSQL via db.py.
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
    dest = os.path.join("backups", f"attendance.db.migrate5-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


def migrate():
    backup_db()
    conn = get_connection()
    cur = conn.cursor()
    SERIAL = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # --- login throttling ------------------------------------------------
    # Previously an in-memory dict, so every restart (i.e. every deploy)
    # cleared all lockouts and handed attackers a fresh budget of attempts.
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS login_attempts (
                id {SERIAL},
                identifier TEXT NOT NULL,     -- lowercased username / roll no / terminal:<course_id>
                attempted_at TIMESTAMP NOT NULL
            )"""
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_identifier "
        "ON login_attempts (identifier, attempted_at)"
    )
    print("[OK] login_attempts table ready")

    # --- planned absences (leave requests) -------------------------------
    # Grievances cover disputes about days already past. This covers days
    # not yet reached, so an approved absence stops polluting at-risk lists.
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS leave_requests (
                id {SERIAL},
                student_id INTEGER NOT NULL,
                course_id INTEGER,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','approved','rejected','cancelled')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_by INTEGER,
                reviewed_at TIMESTAMP,
                review_note TEXT
            )"""
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_leave_requests_student "
        "ON leave_requests (student_id, status)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_leave_requests_course "
        "ON leave_requests (course_id, status)"
    )
    print("[OK] leave_requests table ready")

    # --- alert de-duplication --------------------------------------------
    # send_alerts.py runs weekly from cron; this stops a re-run (or a cron
    # that fires twice) from mailing the same student the same alert again.
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS alert_log (
                id {SERIAL},
                student_id INTEGER NOT NULL,
                kind TEXT NOT NULL,           -- low_attendance
                period TEXT NOT NULL,         -- ISO week, e.g. 2026-W35
                rate REAL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_log_unique "
        "ON alert_log (student_id, kind, period)"
    )
    print("[OK] alert_log table ready")

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 5 migration complete.")


if __name__ == "__main__":
    migrate()
