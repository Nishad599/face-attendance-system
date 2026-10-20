"""Phase 10 migration: multi-tenancy foundation.

Introduces the institute as the thing a deployment can hold several of, so one
server can serve more than one customer without their data touching.

  * institutes            - the tenant. Everything else hangs off it.
  * courses.institute_id  - the anchor. Nearly every table already carries a
                            course_id, so a row's institute is reachable from
                            it; only courses needed a direct link.
  * users.institute_id    - which institute a staff account belongs to.
                            NULL means platform-level, i.e. a superadmin.
  * holidays.institute_id - holidays.course_id IS NULL has always meant "every
                            batch". With a second tenant that silently became
                            "every batch EVERYWHERE", so an institute-wide
                            holiday needs to say whose institute.
  * users.role gains 'superadmin'. SQLite cannot alter a CHECK constraint, so
                            the table is rebuilt, ids preserved.

Everything that exists today is backfilled into one institute, so a
single-tenant deployment behaves exactly as it did. Name it with
DEFAULT_INSTITUTE_NAME in .env before the first run if you want something other
than the default; renaming afterwards is just an UPDATE.

IMPORTANT: tenancy is inert while there is one institute. Every scoping rule
added in this phase falls back to current behaviour when institute_count() == 1,
so this migration cannot change what your running deployment does. The rules
start biting the moment a second institute exists.

Idempotent — safe to run multiple times. Backs up SQLite first.
"""

import os
import re
import shutil
import sys
from datetime import datetime

from db import get_connection, is_postgres, column_exists, table_exists

DB_PATH = "attendance.db"
DEFAULT_NAME = os.getenv("DEFAULT_INSTITUTE_NAME", "CDAC Mumbai").strip() or "CDAC Mumbai"


def backup_db():
    if is_postgres():
        return
    if not os.path.exists(DB_PATH):
        print(f"[WARN] {DB_PATH} not found; nothing to migrate yet.")
        return
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate10-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "institute"


def create_institutes(conn):
    SERIAL = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS institutes (
            id {SERIAL},
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            contact_email TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("[OK] institutes table ready")


def seed_default_institute(conn):
    """One institute to hold everything that already exists."""
    row = conn.execute("SELECT id, name FROM institutes ORDER BY id LIMIT 1").fetchone()
    if row:
        print(f"[skip] institute {row[0]} ({row[1]}) already exists")
        return row[0]
    conn.execute(
        "INSERT INTO institutes (name, slug, is_active) VALUES (?, ?, 1)",
        (DEFAULT_NAME, slugify(DEFAULT_NAME)),
    )
    conn.commit()
    new_id = conn.execute("SELECT id FROM institutes ORDER BY id LIMIT 1").fetchone()[0]
    print(f"[OK] created institute {new_id}: {DEFAULT_NAME}")
    return new_id


def add_institute_columns(conn, institute_id):
    for table in ("courses", "users", "holidays"):
        if not table_exists(conn, table):
            print(f"[WARN] {table} does not exist; skipping")
            continue
        if column_exists(conn, table, "institute_id"):
            print(f"[skip] {table}.institute_id already exists")
        else:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN institute_id INTEGER")
            print(f"[OK] {table}: added column institute_id")

    # Backfill. Courses and staff belong to the one existing institute.
    conn.execute("UPDATE courses SET institute_id = ? WHERE institute_id IS NULL",
                 (institute_id,))
    conn.execute("UPDATE users SET institute_id = ? WHERE institute_id IS NULL "
                 "AND role <> 'superadmin'", (institute_id,))

    # A holiday inherits its batch's institute; an institute-wide one (NULL
    # course_id) belongs to the institute it was created in, which can only be
    # this one.
    conn.execute(
        "UPDATE holidays SET institute_id = ("
        "    SELECT c.institute_id FROM courses c WHERE c.id = holidays.course_id"
        ") WHERE institute_id IS NULL AND course_id IS NOT NULL"
    )
    conn.execute("UPDATE holidays SET institute_id = ? WHERE institute_id IS NULL",
                 (institute_id,))
    conn.commit()
    print("[OK] backfilled courses, users and holidays into the default institute")


def needs_role_rebuild(conn):
    """True while users.role still refuses 'superadmin'."""
    if is_postgres():
        return False        # handled by the ALTER below
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if not row or not row[0]:
        return False
    sql = " ".join(row[0].split()).lower()
    return "check (role in ('admin','teacher'))" in sql


def add_superadmin_role(conn):
    """Widen users.role to admit 'superadmin'."""
    if is_postgres():
        for stmt in (
            "ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check",
            "ALTER TABLE users ADD CONSTRAINT users_role_check "
            "CHECK (role IN ('superadmin','admin','teacher'))",
        ):
            try:
                conn.execute(stmt)
            except Exception as e:
                print(f"[WARN] {stmt.split()[0]}: {e}")
        conn.commit()
        print("[OK] users.role now admits 'superadmin'")
        return

    if not needs_role_rebuild(conn):
        print("[skip] users.role already admits 'superadmin'")
        return

    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=OFF")
    cur.execute("DROP TABLE IF EXISTS users_new")
    cur.execute("""
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            name TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('superadmin','admin','teacher')),
            is_active INTEGER DEFAULT 1,
            must_change_password INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            email TEXT,
            institute_id INTEGER
        )
    """)
    # Ids are preserved: teacher_batches.user_id points at them.
    cur.execute(
        "INSERT INTO users_new (id, username, name, password_hash, role, is_active, "
        "                       must_change_password, created_at, email, institute_id) "
        "SELECT id, username, name, password_hash, role, is_active, "
        "       must_change_password, created_at, email, institute_id FROM users"
    )
    moved = cur.execute("SELECT COUNT(*) FROM users_new").fetchone()[0]
    cur.execute("DROP TABLE users")
    cur.execute("ALTER TABLE users_new RENAME TO users")
    cur.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    print(f"[OK] users rebuilt with the superadmin role; {moved} account(s) preserved")


def add_indexes(conn):
    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_courses_institute ON courses (institute_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_institute ON users (institute_id)",
        "CREATE INDEX IF NOT EXISTS idx_holidays_institute ON holidays (institute_id)",
    ):
        try:
            conn.execute(stmt)
        except Exception as e:
            print(f"[WARN] {stmt.split('idx_')[1].split()[0]}: {e}")
    conn.commit()
    print("[OK] tenancy indexes ready")


def report(conn):
    n_inst = conn.execute("SELECT COUNT(*) FROM institutes").fetchone()[0]
    print()
    print(f"Institutes: {n_inst}")
    for row in conn.execute(
        "SELECT i.id, i.name, i.slug, "
        "       (SELECT COUNT(*) FROM courses c WHERE c.institute_id = i.id), "
        "       (SELECT COUNT(*) FROM users u WHERE u.institute_id = i.id) "
        "FROM institutes i ORDER BY i.id"
    ):
        print(f"  [{row[0]}] {row[1]} ({row[2]}) — {row[3]} batch(es), {row[4]} staff")

    orphans = conn.execute(
        "SELECT COUNT(*) FROM courses WHERE institute_id IS NULL").fetchone()[0]
    if orphans:
        print(f"  [WARN] {orphans} course(s) still have no institute")

    if n_inst == 1:
        print()
        print("  Single institute: every tenancy rule falls back to current")
        print("  behaviour, so nothing about this deployment changes.")


def migrate():
    backup_db()
    conn = get_connection()

    create_institutes(conn)
    institute_id = seed_default_institute(conn)
    add_institute_columns(conn, institute_id)
    add_superadmin_role(conn)
    add_indexes(conn)
    report(conn)

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 10 migration complete.")


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        print("Phase 10 would create the institutes table, add institute_id to")
        print("courses/users/holidays, backfill everything into one institute")
        print(f"named {DEFAULT_NAME!r}, and widen users.role to admit 'superadmin'.")
        print("No --dry-run inspection is available; the migration is idempotent.")
    else:
        migrate()
