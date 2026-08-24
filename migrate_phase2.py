"""Phase 7+ migration: terminal PINs per batch and the grievances table.

Idempotent — safe to run multiple times. Backs up SQLite database first if SQLite is used.
Now supports PostgreSQL natively.
"""
import os
import shutil
from datetime import datetime
from db import get_connection, is_postgres

DB_PATH = "attendance.db"


def backup_db():
    if is_postgres():
        return False
    if not os.path.exists(DB_PATH):
        print(f"[WARN] {DB_PATH} not found; nothing to migrate yet.")
        return False
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate2-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")
    return True


def columns(cur, table):
    if is_postgres():
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,)
        )
        return {row[0] for row in cur.fetchall()}
    else:
        return {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}


def migrate():
    if not is_postgres():
        backup_db()
    conn = get_connection()
    cur = conn.cursor()

    if "terminal_pin_hash" not in columns(cur, "courses"):
        cur.execute("ALTER TABLE courses ADD COLUMN terminal_pin_hash TEXT")
        print("[OK] courses: added terminal_pin_hash")
    else:
        print("[skip] courses.terminal_pin_hash already exists")

    SERIAL = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"

    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS grievances (
                id {SERIAL},
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
