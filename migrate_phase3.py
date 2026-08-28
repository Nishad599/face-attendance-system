"""Phase 3 migration: email log, password-reset OTPs, self-registration requests.

Idempotent — safe to run multiple times. Backs up SQLite first if SQLite is used.
Works on both SQLite and PostgreSQL via db.py.
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
    dest = os.path.join("backups", f"attendance.db.migrate3-{stamp}")
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

    # --- staff email (needed for monthly summaries + OTP reset) ---------
    add_column(cur, "users", "email", "TEXT")
    # If a username already looks like an email, use it as the address.
    cur.execute("UPDATE users SET email = username WHERE (email IS NULL OR email = '') AND username LIKE '%@%'")

    # --- email send log -------------------------------------------------
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS email_log (
                id {SERIAL},
                to_email TEXT NOT NULL,
                subject TEXT,
                kind TEXT,                    -- welcome | monthly_student | monthly_teacher | otp | ...
                status TEXT,                  -- sent | failed | skipped
                error TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    print("[OK] email_log table ready")

    # --- password reset OTPs -------------------------------------------
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS password_resets (
                id {SERIAL},
                principal_type TEXT NOT NULL,   -- 'student' or 'staff'
                principal_id INTEGER NOT NULL,
                otp_hash TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    print("[OK] password_resets table ready")

    # --- student self-registration requests (teacher-approved) ----------
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS face_registration_requests (
                id {SERIAL},
                student_id INTEGER NOT NULL,
                course_id INTEGER,
                session_id TEXT,
                photo_count INTEGER DEFAULT 0,
                encoding_blob BLOB,
                status TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','approved','rejected')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_by INTEGER,
                reviewed_at TIMESTAMP,
                review_note TEXT
            )"""
    )
    print("[OK] face_registration_requests table ready")

    # --- audit log (who changed attendance / students) ------------------
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS audit_log (
                id {SERIAL},
                actor_type TEXT,              -- admin | teacher | student | terminal
                actor_id INTEGER,
                actor_name TEXT,
                action TEXT NOT NULL,         -- bulk_mark | delete_student | grievance_action | ...
                target TEXT,                  -- what was affected
                details TEXT,                 -- JSON-ish summary
                course_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
    )
    print("[OK] audit_log table ready")

    conn.commit()
    conn.close()
    print("\n[DONE] Phase 3 migration complete.")


if __name__ == "__main__":
    migrate()
