"""Phase 1 migration: batches, DB-backed accounts, persistent sessions, security.

Idempotent — safe to run multiple times. Backs up SQLite database first if SQLite is used.
Now supports PostgreSQL natively.
"""
import os
import shutil
from datetime import datetime
from db import get_connection, is_postgres
from auth_utils import hash_password, default_student_password

DB_PATH = "attendance.db"


def backup_db():
    if is_postgres():
        return
    if not os.path.exists(DB_PATH):
        print("[WARN] attendance.db not found; a fresh DB will be created on app start.")
        return
    os.makedirs("backups", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join("backups", f"attendance.db.migrate-{stamp}")
    shutil.copy2(DB_PATH, dest)
    print(f"[OK] Backed up SQLite DB -> {dest}")


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


def add_column(cur, table, col, decl):
    if col not in columns(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        print(f"[OK] {table}: added column {col}")
    else:
        print(f"[skip] {table}.{col} already exists")


def ensure_default_course(cur):
    cur.execute("SELECT id FROM courses WHERE id = 1")
    if not cur.fetchone():
        cur.execute(
            """INSERT INTO courses (id, name, start_date, end_date, description, is_active, created_at)
                   VALUES (1, 'Default Batch', ?, ?, 'Auto-created default batch', 1, ?)""",
            (datetime.now().strftime("%Y-01-01"),
             datetime.now().strftime("%Y-12-31"),
             datetime.now().isoformat()),
        )
        print("[OK] Created default course (id=1)")


def migrate():
    backup_db()
    conn = get_connection()
    cur = conn.cursor()

    SERIAL = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # --- students: new columns ---
    add_column(cur, "students", "course_id", "INTEGER")
    add_column(cur, "students", "password_hash", "TEXT")
    add_column(cur, "students", "must_change_password", "INTEGER DEFAULT 1")
    add_column(cur, "students", "dob", "TEXT")

    # --- attendance / slot_attendance / holidays: course_id ---
    add_column(cur, "attendance", "course_id", "INTEGER")
    add_column(cur, "slot_attendance", "course_id", "INTEGER")
    add_column(cur, "holidays", "course_id", "INTEGER")  # NULL = global holiday

    # --- users table (admin/teacher) ---
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS users (
                id {SERIAL},
                username TEXT UNIQUE NOT NULL,
                name TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin','teacher')),
                is_active INTEGER DEFAULT 1,
                must_change_password INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    print("[OK] users table ready")

    # --- teacher_batches (teacher -> course) ---
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS teacher_batches (
                id {SERIAL},
                user_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, course_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            )"""
    )
    print("[OK] teacher_batches table ready")

    # --- persistent sessions ---
    cur.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_type TEXT NOT NULL,
                user_info TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    print("[OK] sessions table ready")

    # --- seed default course + assign existing students ---
    ensure_default_course(cur)
    cur.execute("UPDATE students SET course_id = 1 WHERE course_id IS NULL")
    print(f"[OK] Assigned {cur.rowcount} student(s) without a batch to the default course")

    # --- backfill attendance/slot_attendance.course_id from student ---
    cur.execute(
        """UPDATE attendance
              SET course_id = (SELECT s.course_id FROM students s WHERE s.id = attendance.student_id)
            WHERE course_id IS NULL"""
    )
    cur.execute(
        """UPDATE slot_attendance
              SET course_id = (SELECT s.course_id FROM students s WHERE s.id = slot_attendance.student_id)
            WHERE course_id IS NULL"""
    )

    # --- give existing students a login (default password from DOB, must change) ---
    cur.execute("SELECT id, dob, password_hash FROM students")
    seeded = 0
    for sid, dob, pw in cur.fetchall():
        if not pw:
            cur.execute(
                "UPDATE students SET password_hash = ?, must_change_password = 1 WHERE id = ?",
                (hash_password(default_student_password(dob)), sid),
            )
            seeded += 1
    print(f"[OK] Seeded default login for {seeded} existing student(s)")

    # --- seed admin/teacher accounts (migrated from hardcoded creds) ---
    seed_users = [
        ("admin", "Administrator", "admin123", "admin"),
        ("teacher", "Teacher", "teacher123", "teacher"),
    ]
    for username, name, pw, role in seed_users:
        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        if not cur.fetchone():
            cur.execute(
                """INSERT INTO users (username, name, password_hash, role, is_active, must_change_password)
                       VALUES (?, ?, ?, ?, 1, 1)""",
                (username, name, hash_password(pw), role),
            )
            print(f"[OK] Seeded {role} account: {username}")
        else:
            print(f"[skip] user {username} already exists")

    # Assign the seeded teacher to the default batch so they can see something
    cur.execute("SELECT id FROM users WHERE username = 'teacher'")
    row = cur.fetchone()
    if row:
        if is_postgres():
            cur.execute(
                "INSERT INTO teacher_batches (user_id, course_id) VALUES (?, 1) ON CONFLICT (user_id, course_id) DO NOTHING",
                (row[0],),
            )
        else:
            cur.execute(
                "INSERT OR IGNORE INTO teacher_batches (user_id, course_id) VALUES (?, 1)",
                (row[0],),
            )

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 1 migration complete.")


if __name__ == "__main__":
    migrate()
