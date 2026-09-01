"""Phase 7 migration: full academic calendar.

The Phase 6 `subjects` table already carries a module's name and date range.
A real course schedule needs more than that:

  * subjects gains faculty, hours, exam_date, coordinator and a sequence
    number, so the published calendar can be reproduced exactly.
  * academic_events holds everything that is NOT a module and therefore has
    no attendance attached — induction, revision days, exams, the picnic,
    mock interviews, convocation, placements.

Keeping events out of `subjects` matters: anything in `subjects` can be
assigned to a timetable slot and have attendance attributed to it, and
"BATCH PICNIC" must never become an attendance module.

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
    dest = os.path.join("backups", f"attendance.db.migrate7-{stamp}")
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

    # --- richer module records -------------------------------------------
    add_column(cur, "subjects", "sequence", "INTEGER")      # module number in the schedule
    add_column(cur, "subjects", "faculty", "TEXT")
    add_column(cur, "subjects", "coordinator", "TEXT")
    add_column(cur, "subjects", "hours", "TEXT")            # free text: "120 hrs (36+44+40)"
    add_column(cur, "subjects", "teaching_days", "INTEGER")
    add_column(cur, "subjects", "exam_date", "DATE")

    # --- non-module calendar entries -------------------------------------
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS academic_events (
                id {SERIAL},
                course_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'event',
                       -- induction | exam | revision | activity | interview
                       -- | placement | event
                start_date DATE NOT NULL,
                end_date DATE,
                notes TEXT,
                coordinator TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_course "
        "ON academic_events (course_id, start_date)"
    )
    # Re-importing the same calendar must update rather than duplicate.
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_unique "
        "ON academic_events (course_id, title, start_date)"
    )
    print("[OK] academic_events table ready")

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 7 migration complete.")


if __name__ == "__main__":
    migrate()
