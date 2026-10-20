"""Phase 9 migration: make attendance dedup an actual database guarantee.

`attendance` is the table every report, analytic and export reads, and it has
never carried a uniqueness constraint. Two places insert into it with
`INSERT OR IGNORE`, which reads as "don't create a duplicate" but silently does
nothing of the kind without an index to conflict against:

    attendance_manager.mark_attendance_with_slot()   (the camera path)
    main_with_face_recognition.detect_attendance()   (the non-slot camera path)

Today duplicates are prevented one layer up, by `slot_attendance`'s
UNIQUE(student_id, date, slot_id) — the camera path checks that first and
returns early. So correctness currently depends on two tables agreeing, and any
new write path that forgets the `slot_attendance` dance double-counts a student
with nothing to stop it.

This adds the missing index:

    UNIQUE (student_id, date, COALESCE(session_type, ''))

COALESCE rather than the bare column because both backends treat NULLs as
distinct in a unique index, and a whole-day manual or bulk mark deliberately
carries no session_type — without the COALESCE those rows would still be free
to duplicate. The expression keeps one row per student per slot per day, and
exactly one whole-day row per student per day.

Existing duplicates have to go first, so the migration reports them, keeps the
lowest id of each group (the earliest mark — the one that reflects when the
student actually arrived) and deletes the rest.

    python migrate_phase9.py --dry-run    # report duplicates, change nothing
    python migrate_phase9.py              # dedupe, then add the index

Idempotent — safe to run multiple times. Backs up SQLite first.
"""

import os
import shutil
import sys
from datetime import datetime

from db import get_connection, is_postgres

DB_PATH = "attendance.db"
INDEX_NAME = "idx_attendance_unique_slot"


def backup_db():
    if is_postgres():
        return
    if not os.path.exists(DB_PATH):
        print(f"[WARN] {DB_PATH} not found; nothing to migrate yet.")
        return
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate9-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


def index_exists(conn):
    try:
        if is_postgres():
            row = conn.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' "
                "AND indexname = ?", (INDEX_NAME,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (INDEX_NAME,),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def find_duplicates(conn):
    """[(student_id, date, session_type, count)] for every duplicated group."""
    try:
        rows = conn.execute(
            "SELECT student_id, date, COALESCE(session_type, ''), COUNT(*) AS n "
            "FROM attendance "
            "GROUP BY student_id, date, COALESCE(session_type, '') "
            "HAVING COUNT(*) > 1 "
            "ORDER BY date, student_id"
        ).fetchall()
    except Exception as e:
        print(f"[WARN] could not scan for duplicates: {e}")
        return []
    return [(r[0], str(r[1])[:10], r[2], r[3]) for r in rows]


def report(dups):
    if not dups:
        print("[OK] no duplicate attendance rows")
        return 0
    extra = sum(n - 1 for *_rest, n in dups)
    print(f"[WARN] {len(dups)} duplicated group(s), {extra} row(s) to remove:")
    for student_id, the_date, session_type, n in dups[:20]:
        label = session_type or "(whole day)"
        print(f"   student {student_id}  {the_date}  {label}  x{n}")
    if len(dups) > 20:
        print(f"   ... and {len(dups) - 20} more")
    return extra


def dedupe(conn):
    """Keep the lowest id in each group; delete the rest."""
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM attendance WHERE id NOT IN ("
        "    SELECT MIN(id) FROM attendance "
        "    GROUP BY student_id, date, COALESCE(session_type, '')"
        ")"
    )
    removed = cur.rowcount
    conn.commit()
    return removed if removed and removed > 0 else 0


def create_index(conn):
    stmt = (
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
        "ON attendance (student_id, date, COALESCE(session_type, ''))"
    )
    try:
        conn.execute(stmt)
        conn.commit()
        return True, None
    except Exception as e:
        return False, str(e)


def migrate(dry_run=False):
    conn = get_connection()

    if index_exists(conn):
        print(f"[skip] {INDEX_NAME} already exists — attendance is already unique")
        # Still worth reporting: an index cannot have been bypassed, but a
        # partially-applied earlier run could have left the table dirty.
        report(find_duplicates(conn))
        conn.close()
        print("\n[DONE] Phase 9 migration complete.")
        return

    dups = find_duplicates(conn)
    extra = report(dups)

    if dry_run:
        print("\n[DRY RUN] nothing was changed.")
        if extra:
            print(f"          {extra} row(s) would be deleted, keeping the "
                  f"earliest mark in each group.")
        print(f"          {INDEX_NAME} would then be created.")
        conn.close()
        return

    backup_db()

    if extra:
        removed = dedupe(conn)
        print(f"[OK] removed {removed} duplicate row(s), kept the earliest of each")

    ok, err = create_index(conn)
    if ok:
        print(f"[OK] {INDEX_NAME} created — "
              "one row per student per slot per day is now enforced")
    else:
        print(f"[ERROR] could not create {INDEX_NAME}: {err}")
        print("        The table still has duplicates, or the backend rejected "
              "the expression index. Nothing else was changed.")

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 9 migration complete.")


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
