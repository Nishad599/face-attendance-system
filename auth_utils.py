"""Shared authentication helpers: password hashing/verification and defaults.

Uses only the Python standard library (hashlib pbkdf2) so no new dependency is
required. Password format stored in the DB:

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""
import hashlib
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """Hash a plaintext password with a fresh random salt."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS
    )
    return f"{_ALGO}${_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a plaintext password against a stored hash. Safe against bad input."""
    if not stored or not password:
        return False
    try:
        algo, iters, salt, hexhash = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters)
        )
        return secrets.compare_digest(dk.hex(), hexhash)
    except (ValueError, AttributeError):
        return False


def default_student_password(dob: str) -> str:
    """Default student password derived from date of birth.

    Keeps only digits from the DOB (e.g. '2001-05-14' -> '20010514'). If the DOB
    is missing or too short, fall back to a shared default the student must change.
    """
    digits = "".join(ch for ch in (dob or "") if ch.isdigit())
    return digits if len(digits) >= 6 else "changeme123"
