"""check_staged.py — refuse to commit anything that looks like a secret.

`.gitignore` should already exclude these. This is the backstop for the day it
does not: a credential pushed to GitHub stays in the history even after the
file is deleted, so the cost of missing one is permanent.

Called by push.bat before every commit. Exits 1 (and names the files) if the
staged set contains anything sensitive, 0 otherwise.

    python deploy/check_staged.py            # scan the staged files
    python deploy/check_staged.py a.txt b    # scan the given paths instead

Written in Python rather than findstr because findstr's `$` anchor does not
work reliably, so `.env` and `.env.example` could not be told apart.
"""

import os
import posixpath
import subprocess
import sys

# Files whose NAME looks like an env file are secret, except the committed
# templates — .env.example is tracked on purpose and must stay committable.
ENV_ALLOWED = {".env.example", ".env.sample", ".env.template", ".env.dist"}

SECRET_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
DATABASE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}

# Anything under these directories, at any depth.
SECRET_DIRS = ("student_photos", "backups", "logs")

SECRET_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials.json"}


def reason(path):
    """Why this path must not be committed, or None if it is fine."""
    norm = path.replace("\\", "/").strip()
    # Strip a leading "./" only. lstrip("./") would eat the leading dot of
    # ".env" itself and let the most important case through.
    while norm.startswith("./"):
        norm = norm[2:]
    if not norm:
        return None
    name = posixpath.basename(norm)
    lower = name.lower()
    _, ext = posixpath.splitext(lower)

    parts = norm.split("/")
    for d in SECRET_DIRS:
        if d in parts[:-1] or parts[0] == d:
            if d == "student_photos":
                return "biometric student photos"
            if d == "backups":
                return "database backup"
            return "log file"

    if lower == ".env" or (lower.startswith(".env") and lower not in ENV_ALLOWED):
        return "environment file with credentials"

    if ext in SECRET_EXTENSIONS:
        return "private key / certificate"

    if ext in DATABASE_EXTENSIONS:
        return "database file"

    if lower in SECRET_NAMES:
        return "private key"

    return None


def staged_files():
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print("[ERROR] could not read the staged file list from git")
        sys.exit(2)
    return [l for l in out.stdout.splitlines() if l.strip()]


def main():
    paths = sys.argv[1:] or staged_files()
    offenders = [(p, reason(p)) for p in paths]
    offenders = [(p, r) for p, r in offenders if r]

    if not offenders:
        return 0

    print("STOPPED - these staged files must never be committed:")
    print()
    for p, r in offenders:
        print(f"    {p}")
        print(f"        ({r})")
    print()
    print("A secret pushed to GitHub stays in the history even after you")
    print("delete the file. Check .gitignore, then unstage with:  git reset")
    return 1


if __name__ == "__main__":
    sys.exit(main())
