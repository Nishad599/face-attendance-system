"""Phase 4 migration: student profile-change requests (teacher-approved).

Students can propose edits to their own details; the change is only applied
once a teacher approves it. Idempotent — safe to run multiple times.
"""
import os
import shutil
from datetime import datetime
from db import get_connection, is_postgres

DB_PATH = "attendance.db"


def backup_db():
    if is_postgres() or not os.path.exists(DB_PATH):
        return
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate4-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


def migrate():
    backup_db()
    conn = get_connection()
    cur = conn.cursor()
    SERIAL = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"

    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS profile_change_requests (
                id {SERIAL},
                student_id INTEGER NOT NULL,
                course_id INTEGER,
                changes TEXT NOT NULL,        -- JSON {{field: new_value}}
                old_values TEXT,              -- JSON snapshot at request time
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','approved','rejected')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_by INTEGER,
                reviewed_at TIMESTAMP,
                review_note TEXT
            )"""
    )
    print("[OK] profile_change_requests table ready")

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 4 migration complete.")


if __name__ == "__main__":
    migrate()
