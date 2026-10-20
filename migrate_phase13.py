"""Phase 13: index slot_attendance, and drop three tables nothing uses.

## The indexes

`slot_attendance` carried no index at all, and it is written on every mark and
read by every report and export. Three access patterns need covering:

    (student_id, date)   "has this student already marked this slot today?",
                         run once per recognised face on the camera path
    (date, slot_id)      the per-day and per-slot counts the exports group by
    (course_id, date)    the batch roster views

`attendance` already has four indexes; this is the table that had none.

## The tables

Three exist, are created on every startup, and are never read:

    session_attendance        a third attendance store that nothing ever wrote
                              to. Manual marking used to write only
                              slot_attendance, and reports read `attendance`,
                              so this one sat empty from the start.
    daily_attendance_summary  a cache recomputed on every single mark -- four
                              COUNT queries and a write per student -- that no
                              query ever read. Its counts were unscoped by
                              institute too, so they would have been wrong on
                              a multi-tenant deployment.
    course_settings           created and never touched again. No INSERT, no
                              SELECT, anywhere.

They are not free: they make the schema read as though features exist, and the
next person has to work out which of the three attendance tables is the real
one. (That question has already cost this project a bug where manual marks
never appeared in any report.)

**This migration refuses to drop a table that has rows in it.** Zero rows on
the development machine is not evidence about the VM, and a DROP is not
something to be clever about -- if anything is in there, it stops and says so
rather than deciding on your behalf.

Checked before writing this: `face_encodings` and `registration_sessions` look
equally empty and are both live -- per-photo encodings and the in-progress
enrolment flow. Row counts are not what makes a table dead.

Idempotent -- safe to run multiple times. Backs up SQLite first.
"""

import os
import shutil
import sys
from datetime import datetime

from db import get_connection, is_postgres, table_exists

DB_PATH = "attendance.db"

INDEXES = [
    ("idx_slot_attendance_student_date", "slot_attendance", "(student_id, date)"),
    ("idx_slot_attendance_date_slot", "slot_attendance", "(date, slot_id)"),
    ("idx_slot_attendance_course_date", "slot_attendance", "(course_id, date)"),
    # The audit log is read newest-first and filtered by action.
    ("idx_audit_action", "audit_log", "(action, id)"),
    # email_log grows forever and the dashboard counts it by status on every
    # 30-second poll of /api/admin/overview -- three COUNT(*) over the whole
    # table. (It has no student_id; a first draft of this indexed one, and the
    # column guard below caught it.)
    ("idx_email_log_status", "email_log", "(status)"),
]

DROP = ["session_attendance", "daily_attendance_summary", "course_settings"]


def backup_db():
    if is_postgres():
        return
    if not os.path.exists(DB_PATH):
        print(f"[WARN] {DB_PATH} not found; nothing to migrate yet.")
        return
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate13-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


def index_exists(conn, name):
    if is_postgres():
        row = conn.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s",
            (name,)).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
            (name,)).fetchone()
    return row is not None


def row_count(conn, table):
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        return None


def columns_of(conn, table):
    if is_postgres():
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s", (table,)).fetchall()
    else:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}
    return {r[0] for r in rows}


def migrate(dry_run=False):
    conn = get_connection()
    did_anything = False

    print("Indexes")
    planned = []
    for name, table, cols in INDEXES:
        if not table_exists(conn, table):
            print(f"  [skip] {table} does not exist")
            continue
        # An index on a column the table does not have fails the whole run.
        have = columns_of(conn, table)
        want = {c.strip() for c in cols.strip("()").split(",")}
        missing = want - have
        if missing:
            print(f"  [skip] {name}: {table} has no {', '.join(sorted(missing))}")
            continue
        if index_exists(conn, name):
            print(f"  [skip] {name} already exists")
            continue
        planned.append((name, table, cols))

    for name, table, cols in planned:
        if dry_run:
            print(f"  would create {name} on {table} {cols}")
            continue
        if not did_anything:
            backup_db()
            did_anything = True
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}")
        print(f"  [OK] created {name} on {table} {cols}")

    print("\nTables nothing reads")
    for table in DROP:
        if not table_exists(conn, table):
            print(f"  [skip] {table} is already gone")
            continue
        n = row_count(conn, table)
        if n is None:
            print(f"  [WARN] {table}: could not count rows; leaving it alone")
            continue
        if n > 0:
            print(f"  [STOP] {table} has {n} row(s). NOT dropping it.")
            print(f"         Nothing in the code reads this table, so those rows")
            print(f"         came from somewhere this migration does not know")
            print(f"         about. Look at them before deciding:")
            print(f"           sqlite3 {DB_PATH} 'SELECT * FROM {table} LIMIT 5;'")
            continue
        if dry_run:
            print(f"  would drop {table} (0 rows)")
            continue
        if not did_anything:
            backup_db()
            did_anything = True
        conn.execute(f"DROP TABLE {table}")
        print(f"  [OK] dropped {table} (was empty)")

    if dry_run:
        conn.close()
        print("\n[DRY RUN] nothing was changed.")
        return

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 13 migration complete.")


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
