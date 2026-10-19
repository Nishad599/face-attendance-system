"""Phase 12 migration: an institute's own contact details.

/about and /contact were hardcoded marketing for one institute — its campus,
its programmes, its achievements, and on the contact page a named individual
with direct phone numbers. Every institute onboarded onto this deployment
would have seen them, which is the last thing blocking the product from being
sold to anyone else.

Adds the fields those pages need, so they render from the institute's own
record instead:

    tagline, about, address, phone, website

All optional. An institute that fills in nothing gets a page with its name and
nothing else, which is correct — better an empty page than someone else's.

Idempotent — safe to run multiple times. Backs up SQLite first.
"""

import os
import shutil
import sys
from datetime import datetime

from db import get_connection, is_postgres, column_exists, table_exists

DB_PATH = "attendance.db"

COLUMNS = [
    ("tagline", "TEXT"),
    ("about", "TEXT"),
    ("address", "TEXT"),
    ("phone", "TEXT"),
    ("website", "TEXT"),
]


def backup_db():
    if is_postgres():
        return
    if not os.path.exists(DB_PATH):
        print(f"[WARN] {DB_PATH} not found; nothing to migrate yet.")
        return
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate12-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


def migrate(dry_run=False):
    conn = get_connection()

    if not table_exists(conn, "institutes"):
        print("[WARN] institutes does not exist; run migrate_phase10.py first.")
        return

    missing = [c for c, _ in COLUMNS if not column_exists(conn, "institutes", c)]
    if dry_run:
        print("would add:", ", ".join(missing) if missing else "nothing")
        conn.close()
        return

    if missing:
        backup_db()
    for col, coltype in COLUMNS:
        if column_exists(conn, "institutes", col):
            print(f"[skip] institutes.{col} already exists")
            continue
        conn.execute(f"ALTER TABLE institutes ADD COLUMN {col} {coltype}")
        print(f"[OK] institutes: added column {col}")
    conn.commit()
    conn.close()
    print("\n[DONE] Phase 12 migration complete.")


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
