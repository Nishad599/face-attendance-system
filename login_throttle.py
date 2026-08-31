"""Durable login throttling.

Previously an in-memory dict in main_with_face_recognition.py, which meant
every restart — i.e. every deploy — wiped all lockouts and handed an attacker
a fresh budget of attempts. Counters now live in the `login_attempts` table.

Kept in its own module so the real implementation can be unit-tested without
importing the app (which needs the buffalo_l face model to load).

Every function takes an open DB connection and never raises: a problem with
the throttle store must not lock legitimate users out of the application.
"""

import os
from datetime import datetime, timedelta

MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "8") or 8)
LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "10") or 10)


def normalise(identifier):
    """Throttle key. Lowercased so 'Admin' and 'admin' share a budget."""
    return (identifier or "").strip().lower()


def is_blocked(conn, identifier, max_attempts=None, lockout_minutes=None, now=None):
    """(blocked, seconds_remaining) for this identifier."""
    max_attempts = MAX_ATTEMPTS if max_attempts is None else max_attempts
    lockout_minutes = LOCKOUT_MINUTES if lockout_minutes is None else lockout_minutes
    now = now or datetime.now()

    key = normalise(identifier)
    if not key:
        return False, 0
    try:
        window_start = now - timedelta(minutes=lockout_minutes)
        # Prune this identifier's expired rows so the table stays small.
        conn.execute(
            "DELETE FROM login_attempts WHERE identifier = ? AND attempted_at < ?",
            (key, window_start.isoformat()),
        )
        rows = conn.execute(
            "SELECT attempted_at FROM login_attempts WHERE identifier = ? "
            "ORDER BY attempted_at ASC",
            (key,),
        ).fetchall()
        conn.commit()

        if len(rows) < max_attempts:
            return False, 0
        oldest = datetime.fromisoformat(str(rows[0][0]))
        remaining = int((oldest + timedelta(minutes=lockout_minutes) - now).total_seconds())
        return True, max(remaining, 1)
    except Exception as e:
        print(f"[WARN] login throttle check failed: {e}")
        return False, 0


def record_failure(conn, identifier, now=None):
    key = normalise(identifier)
    if not key:
        return
    try:
        conn.execute(
            "INSERT INTO login_attempts (identifier, attempted_at) VALUES (?, ?)",
            (key, (now or datetime.now()).isoformat()),
        )
        conn.commit()
    except Exception as e:
        print(f"[WARN] login throttle record failed: {e}")


def clear(conn, identifier):
    """Wipe an identifier's failures — called on a successful login."""
    key = normalise(identifier)
    if not key:
        return
    try:
        conn.execute("DELETE FROM login_attempts WHERE identifier = ?", (key,))
        conn.commit()
    except Exception as e:
        print(f"[WARN] login throttle clear failed: {e}")


def terminal_key(course_id):
    """Throttle key for a batch's kiosk PIN.

    Namespaced so a batch id can never collide with a student roll number,
    and per-batch so one kiosk under attack cannot lock out another.
    """
    return f"terminal:{course_id}"
