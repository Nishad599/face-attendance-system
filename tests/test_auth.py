"""Password hashing, default passwords, and login-gating rules."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from auth_utils import hash_password, verify_password, default_student_password
import login_throttle


class TestPasswordHashing:
    def test_roundtrip(self):
        h = hash_password("correct horse")
        assert verify_password("correct horse", h)
        assert not verify_password("wrong horse", h)

    def test_hash_is_salted(self):
        """Same password must not produce the same hash twice."""
        assert hash_password("same") != hash_password("same")

    def test_plaintext_never_stored(self):
        h = hash_password("SuperSecret123")
        assert "SuperSecret123" not in h
        assert h.startswith("pbkdf2_sha256$")

    @pytest.mark.parametrize("bad", [None, "", "not-a-hash", "a$b$c", "pbkdf2_sha256$x$y$z"])
    def test_malformed_hash_rejected_not_raised(self, bad):
        """Bad stored values must return False, never raise."""
        assert verify_password("anything", bad) is False

    def test_empty_password_rejected(self):
        assert verify_password("", hash_password("x")) is False


class TestDefaultStudentPassword:
    @pytest.mark.parametrize("dob,expected", [
        ("2001-05-14", "20010514"),
        ("14/05/2001", "14052001"),
        ("2002-11-02", "20021102"),
    ])
    def test_digits_of_dob(self, dob, expected):
        assert default_student_password(dob) == expected

    @pytest.mark.parametrize("dob", [None, "", "abc", "12"])
    def test_fallback_when_dob_unusable(self, dob):
        assert default_student_password(dob) == "changeme123"


class TestStaffAuthentication:
    """Mirrors authenticate_user(): role gating and active check."""

    def _auth(self, db, username, password, roles):
        row = db.execute(
            "SELECT id, username, name, password_hash, role, is_active FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return None
        _id, uname, name, ph, role, active = row
        if not active or role not in roles or not verify_password(password, ph):
            return None
        return {"id": _id, "role": role}

    @pytest.fixture
    def staff(self, db):
        db.execute(
            "INSERT INTO users (username, name, password_hash, role, is_active) "
            "VALUES ('admin', 'Admin', ?, 'admin', 1)", (hash_password("admin123"),))
        db.execute(
            "INSERT INTO users (username, name, password_hash, role, is_active) "
            "VALUES ('teach', 'Teach', ?, 'teacher', 1)", (hash_password("teach123"),))
        db.execute(
            "INSERT INTO users (username, name, password_hash, role, is_active) "
            "VALUES ('gone', 'Gone', ?, 'teacher', 0)", (hash_password("gone123"),))
        db.commit()
        return db

    def test_valid_admin(self, staff):
        assert self._auth(staff, "admin", "admin123", ["admin", "teacher"])["role"] == "admin"

    def test_valid_teacher(self, staff):
        assert self._auth(staff, "teach", "teach123", ["admin", "teacher"])["role"] == "teacher"

    def test_wrong_password(self, staff):
        assert self._auth(staff, "admin", "nope", ["admin", "teacher"]) is None

    def test_unknown_user(self, staff):
        assert self._auth(staff, "ghost", "x", ["admin", "teacher"]) is None

    def test_role_gate_blocks_teacher_from_admin_only(self, staff):
        """A teacher must not authenticate where only admin is allowed."""
        assert self._auth(staff, "teach", "teach123", ["admin"]) is None

    def test_deactivated_account_cannot_log_in(self, staff):
        assert self._auth(staff, "gone", "gone123", ["admin", "teacher"]) is None


class TestLoginRateLimiting:
    """Exercises the real login_throttle implementation against the DB.

    These used to re-implement an in-memory dict in the test itself, which
    stopped testing anything real once the throttle moved into the database.
    """

    MAX, WINDOW = 8, 10

    def _fail(self, db, key, times=1, when=None):
        for _ in range(times):
            login_throttle.record_failure(db, key, now=when)

    def _blocked(self, db, key):
        return login_throttle.is_blocked(
            db, key, max_attempts=self.MAX, lockout_minutes=self.WINDOW
        )[0]

    def test_locks_after_max_attempts(self, db):
        self._fail(db, "u", self.MAX - 1)
        assert not self._blocked(db, "u")
        self._fail(db, "u")
        assert self._blocked(db, "u")

    def test_lock_is_per_identifier(self, db):
        self._fail(db, "victim", self.MAX)
        assert self._blocked(db, "victim")
        assert not self._blocked(db, "bystander")

    def test_key_is_case_and_space_insensitive(self, db):
        """'Admin ' must not get a separate budget from 'admin'."""
        self._fail(db, "  ADMIN  ", self.MAX)
        assert self._blocked(db, "admin")

    def test_old_attempts_expire_out_of_window(self, db):
        stale = datetime.now() - timedelta(minutes=self.WINDOW + 1)
        self._fail(db, "u", self.MAX, when=stale)
        assert not self._blocked(db, "u")

    def test_expired_rows_are_pruned(self, db):
        """The check also cleans up, so the table cannot grow without bound."""
        stale = datetime.now() - timedelta(minutes=self.WINDOW + 1)
        self._fail(db, "u", self.MAX, when=stale)
        self._blocked(db, "u")
        left = db.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE identifier = 'u'"
        ).fetchone()[0]
        assert left == 0

    def test_clearing_on_success_unblocks(self, db):
        self._fail(db, "u", self.MAX)
        assert self._blocked(db, "u")
        login_throttle.clear(db, "u")
        assert not self._blocked(db, "u")

    def test_lockout_survives_a_restart(self, db_path):
        """The whole point of moving this into the database: a redeploy must
        not hand an attacker a fresh set of attempts."""
        import sqlite3
        first = sqlite3.connect(db_path)
        for _ in range(self.MAX):
            login_throttle.record_failure(first, "persist")
        first.close()                                   # simulate the restart

        second = sqlite3.connect(db_path)
        blocked, wait = login_throttle.is_blocked(
            second, "persist", max_attempts=self.MAX, lockout_minutes=self.WINDOW
        )
        second.close()
        assert blocked, "lockout must survive the process restarting"
        assert wait > 0

    def test_seconds_remaining_is_sane(self, db):
        self._fail(db, "u", self.MAX)
        _blocked, wait = login_throttle.is_blocked(
            db, "u", max_attempts=self.MAX, lockout_minutes=self.WINDOW
        )
        assert 0 < wait <= self.WINDOW * 60

    def test_empty_identifier_is_never_blocked(self, db):
        assert not self._blocked(db, "")
        assert not self._blocked(db, None)

    def test_terminal_keys_are_namespaced_per_batch(self, db):
        """A kiosk PIN under attack must not lock out another batch's kiosk —
        nor collide with a student whose roll number is '3'."""
        self._fail(db, login_throttle.terminal_key(3), self.MAX)
        assert self._blocked(db, login_throttle.terminal_key(3))
        assert not self._blocked(db, login_throttle.terminal_key(4))
        assert not self._blocked(db, "3")

    def test_broken_store_fails_open(self, db):
        """If the table is missing, users must still be able to log in."""
        db.execute("DROP TABLE login_attempts")
        db.commit()
        assert not self._blocked(db, "u")
        login_throttle.record_failure(db, "u")     # must not raise
        login_throttle.clear(db, "u")              # must not raise


class TestOtpReset:
    """Password-reset OTP: verification, expiry, single use."""

    def test_otp_verifies_and_wrong_code_rejected(self, db):
        h = hash_password("483920")
        db.execute(
            "INSERT INTO password_resets (principal_type, principal_id, otp_hash, expires_at) "
            "VALUES ('student', 1, ?, ?)",
            (h, (datetime.now() + timedelta(minutes=15)).isoformat()))
        db.commit()
        stored = db.execute("SELECT otp_hash FROM password_resets").fetchone()[0]
        assert verify_password("483920", stored)
        assert not verify_password("000000", stored)

    def test_expired_otp_detected(self, db):
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        db.execute(
            "INSERT INTO password_resets (principal_type, principal_id, otp_hash, expires_at) "
            "VALUES ('student', 1, ?, ?)", (hash_password("111111"), past))
        db.commit()
        exp = db.execute("SELECT expires_at FROM password_resets").fetchone()[0]
        assert datetime.now() > datetime.fromisoformat(exp)

    def test_new_request_invalidates_previous(self, db):
        exp = (datetime.now() + timedelta(minutes=15)).isoformat()
        db.execute("INSERT INTO password_resets (principal_type, principal_id, otp_hash, expires_at) "
                   "VALUES ('student', 1, ?, ?)", (hash_password("111111"), exp))
        db.execute("UPDATE password_resets SET used = 1 WHERE principal_type='student' "
                   "AND principal_id=1 AND used=0")
        db.execute("INSERT INTO password_resets (principal_type, principal_id, otp_hash, expires_at) "
                   "VALUES ('student', 1, ?, ?)", (hash_password("222222"), exp))
        db.commit()
        unused = db.execute(
            "SELECT COUNT(*) FROM password_resets WHERE principal_id=1 AND used=0").fetchone()[0]
        assert unused == 1
