"""Phase 11 migration: scope the audit log to an institute.

`audit_log` recorded `course_id`, which is NULL for anything not about a
batch — including every platform-level action a superadmin takes. The admin
dashboard then read the table with no filter at all, so an institute admin saw
entries like:

    Platform Owner · create_institute · cdac ycp
    Platform Owner · create_institute_admin · ycpadmin

That tells one customer that another customer exists, and names their
administrator.

Adds `audit_log.institute_id`:

    a number  -> the institute this action belongs to; its admins may see it
    NULL      -> platform level; only the superadmin may see it

Existing rows are backfilled from their batch where they have one, and
otherwise attributed to the first institute — they were all written before
any second institute existed, so that is where they belong.

Idempotent — safe to run multiple times. Backs up SQLite first.
"""

import os
import shutil
import sys
from datetime import datetime

from db import get_connection, is_postgres, column_exists, table_exists

DB_PATH = "attendance.db"


def backup_db():
    if is_postgres():
        return
    if not os.path.exists(DB_PATH):
        print(f"[WARN] {DB_PATH} not found; nothing to migrate yet.")
        return
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate11-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


def migrate(dry_run=False):
    conn = get_connection()

    if not table_exists(conn, "audit_log"):
        print("[WARN] audit_log does not exist; run migrate_phase3.py first.")
        return

    already = column_exists(conn, "audit_log", "institute_id")
    unscoped = 0
    try:
        unscoped = conn.execute(
            "SELECT COUNT(*) FROM audit_log" +
            (" WHERE institute_id IS NULL" if already else "")).fetchone()[0]
    except Exception:
        unscoped = 0

    if dry_run:
        print(f"audit_log.institute_id {'exists' if already else 'would be added'}; "
              f"{unscoped} row(s) would be attributed.")
        conn.close()
        return

    if already:
        print("[skip] audit_log.institute_id already exists")
    else:
        backup_db()
        conn.execute("ALTER TABLE audit_log ADD COLUMN institute_id INTEGER")
        print("[OK] audit_log: added column institute_id")

    # Rows about a batch belong to that batch's institute.
    conn.execute(
        "UPDATE audit_log SET institute_id = ("
        "    SELECT c.institute_id FROM courses c WHERE c.id = audit_log.course_id"
        ") WHERE institute_id IS NULL AND course_id IS NOT NULL")

    # Everything else predates any second institute, so it belongs to the
    # first one. Platform-level rows written from here on are left NULL on
    # purpose and are only ever shown to a superadmin.
    first = conn.execute("SELECT id FROM institutes ORDER BY id LIMIT 1").fetchone()
    if first:
        conn.execute(
            "UPDATE audit_log SET institute_id = ? WHERE institute_id IS NULL "
            "AND actor_type <> 'superadmin'", (first[0],))

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_institute ON audit_log (institute_id)")
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    platform = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE institute_id IS NULL").fetchone()[0]
    print(f"[OK] {total} audit row(s): {total - platform} attributed to an institute, "
          f"{platform} platform-level")

    conn.close()
    print("\n[DONE] Phase 11 migration complete.")


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
