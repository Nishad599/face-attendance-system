"""Phase 8 migration: make holidays per-batch.

`holidays.date` carried a UNIQUE constraint on the date ALONE, which meant the
whole institute could only ever have one holiday on a given date. Two batches
running side by side cannot both take Independence Day off, and importing a
second batch's calendar fails with a UNIQUE violation the moment its holidays
overlap the first's.

Rebuilds the table with UNIQUE(date, course_id) instead, so:
  * course_id = NULL  -> institute-wide holiday (applies to every batch)
  * course_id = <id>  -> that batch only

Existing rows are preserved exactly. Idempotent: detects whether the old
constraint is still present and does nothing if already migrated.
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
    dest = os.path.join("backups", f"attendance.db.migrate8-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


def needs_migration(conn):
    """True while the old date-only UNIQUE is still in place."""
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='holidays'"
        ).fetchone()
    except Exception:
        return False
    if not row or not row[0]:
        return False
    sql = " ".join(row[0].split()).lower()
    # The old schema declares it inline: "date DATE NOT NULL UNIQUE"
    return "date date not null unique" in sql


def migrate_sqlite(conn):
    cur = conn.cursor()

    # SQLite cannot drop a column constraint, so rebuild and copy.
    cur.execute("PRAGMA foreign_keys=OFF")
    cur.execute("DROP TABLE IF EXISTS holidays_new")
    cur.execute(
        """CREATE TABLE holidays_new (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               date DATE NOT NULL,
               name TEXT NOT NULL,
               type TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               course_id INTEGER
           )"""
    )
    cur.execute(
        "INSERT INTO holidays_new (id, date, name, type, created_at, course_id) "
        "SELECT id, date, name, type, created_at, course_id FROM holidays"
    )
    moved = cur.execute("SELECT COUNT(*) FROM holidays_new").fetchone()[0]

    cur.execute("DROP TABLE holidays")
    cur.execute("ALTER TABLE holidays_new RENAME TO holidays")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_holidays_date_course "
        "ON holidays (date, course_id)"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_holidays_date ON holidays (date)")
    cur.execute("PRAGMA foreign_keys=ON")
    return moved


def migrate_postgres(conn):
    cur = conn.cursor()
    # On PG the constraint is a named object, so it can simply be dropped.
    for stmt in (
        "ALTER TABLE holidays DROP CONSTRAINT IF EXISTS holidays_date_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_holidays_date_course "
        "ON holidays (date, course_id)",
        "CREATE INDEX IF NOT EXISTS idx_holidays_date ON holidays (date)",
    ):
        try:
            cur.execute(stmt)
        except Exception as e:
            print(f"[WARN] {stmt.split()[0]}: {e}")
    return cur.execute("SELECT COUNT(*) FROM holidays").fetchone()[0]


def add_mid_quiz_column(conn):
    """The PGCP-AI schedule carries a mid-quiz date per module; PGCP-BDA's
    did not. Nullable, so calendars without one are unaffected."""
    cur = conn.cursor()
    try:
        if is_postgres():
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='subjects'")
            cols = {r[0] for r in cur.fetchall()}
        else:
            cols = {r[1] for r in cur.execute("PRAGMA table_info(subjects)")}
        if "mid_quiz_date" not in cols:
            cur.execute("ALTER TABLE subjects ADD COLUMN mid_quiz_date DATE")
            print("[OK] subjects: added column mid_quiz_date")
        else:
            print("[skip] subjects.mid_quiz_date already exists")
    except Exception as e:
        print(f"[WARN] subjects.mid_quiz_date: {e}")


def migrate():
    backup_db()
    conn = get_connection()
    add_mid_quiz_column(conn)

    if is_postgres():
        moved = migrate_postgres(conn)
        print(f"[OK] holidays: UNIQUE now (date, course_id); {moved} row(s) intact")
    elif not needs_migration(conn):
        print("[skip] holidays already allows per-batch dates")
    else:
        moved = migrate_sqlite(conn)
        print(f"[OK] holidays rebuilt with UNIQUE(date, course_id); "
              f"{moved} row(s) preserved")

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 8 migration complete.")


if __name__ == "__main__":
    migrate()
