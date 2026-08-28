"""Password hashing, default passwords, and login-gating rules."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from auth_utils import hash_password, verify_password, default_student_password


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
    """Mirrors login_blocked()/record_login_failure()."""

    MAX, WINDOW = 8, 10

    def _blocked(self, attempts, key):
        cutoff = datetime.now() - timedelta(minutes=self.WINDOW)
        tries = [t for t in attempts.get(key, []) if t > cutoff]
        attempts[key] = tries
        return len(tries) >= self.MAX

    def test_locks_after_max_attempts(self):
        a = {}
        for _ in range(self.MAX - 1):
            a.setdefault("u", []).append(datetime.now())
        assert not self._blocked(a, "u")
        a["u"].append(datetime.now())
        assert self._blocked(a, "u")

    def test_lock_is_per_identifier(self):
        a = {"victim": [datetime.now()] * self.MAX}
        assert self._blocked(a, "victim")
        assert not self._blocked(a, "bystander")

    def test_old_attempts_expire_out_of_window(self):
        stale = datetime.now() - timedelta(minutes=self.WINDOW + 1)
        a = {"u": [stale] * self.MAX}
        assert not self._blocked(a, "u")

    def test_clearing_on_success_unblocks(self):
        a = {"u": [datetime.now()] * self.MAX}
        assert self._blocked(a, "u")
        a.pop("u", None)
        assert not self._blocked(a, "u")


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
