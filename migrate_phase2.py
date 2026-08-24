"""Phase 7+ migration: terminal PINs per batch and the grievances table.

Idempotent — safe to run multiple times. Backs up attendance.db first.

Adds:
  * courses.terminal_pin_hash  (hashed PIN for the batch attendance terminal)
  * grievances table           (student attendance disputes)
"""
import os
import shutil
import sqlite3
from datetime import datetime

DB_PATH = "attendance.db"


def backup_db():
    if not os.path.exists(DB_PATH):
        print(f"[WARN] {DB_PATH} not found; nothing to migrate yet.")
        return False
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate2-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up DB -> {dest}")
    return True


def columns(cur, table):
    return {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}


def migrate():
    if not backup_db():
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if "terminal_pin_hash" not in columns(cur, "courses"):
        cur.execute("ALTER TABLE courses ADD COLUMN terminal_pin_hash TEXT")
        print("[OK] courses: added terminal_pin_hash")
    else:
        print("[skip] courses.terminal_pin_hash already exists")

    cur.execute(
        """CREATE TABLE IF NOT EXISTS grievances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                course_id INTEGER,
                date DATE NOT NULL,
                session_type TEXT,               -- NULL = whole day
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','approved','rejected')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_by INTEGER,
                reviewed_at TIMESTAMP,
                review_note TEXT,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE SET NULL
            )"""
    )
    print("[OK] grievances table ready")

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 7+ migration complete.")


if __name__ == "__main__":
    migrate()
