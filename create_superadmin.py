"""create_superadmin.py — make the platform owner account.

migrate_phase10 added the 'superadmin' role, but deliberately created no
account for it: a privileged login that appears by itself, with a password
nobody can account for, is worse than having to run one command.

    python create_superadmin.py                 # prompts for a password
    python create_superadmin.py --username boss # pick the username
    python create_superadmin.py --list          # who already has the role

A superadmin belongs to no institute (institute_id stays NULL) and sees every
one of them through /superadmin. Everyone else is refused there.

The password is read from a prompt, never from an argument — a command line
ends up in your shell history and in the process list.
"""

import argparse
import getpass
import sys

from auth_utils import hash_password, verify_password
from db import get_connection


MIN_LENGTH = 10


def list_owners(conn):
    try:
        rows = conn.execute(
            "SELECT id, username, name, is_active FROM users "
            "WHERE role = 'superadmin' ORDER BY username").fetchall()
    except Exception as e:
        print(f"[ERROR] cannot read users: {e}")
        print("        Has migrate_phase10.py run?")
        return 2
    if not rows:
        print("No platform owner exists yet.")
        return 0
    print(f"{len(rows)} platform owner(s):")
    for r in rows:
        print(f"   [{r[0]}] {r[1]}  {r[2] or ''}  {'active' if r[3] else 'INACTIVE'}")
    return 0


def read_password():
    while True:
        pw = getpass.getpass("Password: ")
        if len(pw) < MIN_LENGTH:
            print(f"  Too short — at least {MIN_LENGTH} characters.")
            continue
        if pw != getpass.getpass("Repeat:   "):
            print("  They do not match.")
            continue
        return pw


def main():
    ap = argparse.ArgumentParser(description="Create or update the platform owner.")
    ap.add_argument("--username", default="owner")
    ap.add_argument("--name", default="Platform Owner")
    ap.add_argument("--list", action="store_true", help="show existing owners and exit")
    args = ap.parse_args()

    conn = get_connection()

    if args.list:
        return list_owners(conn)

    try:
        conn.execute("SELECT 1 FROM institutes LIMIT 1").fetchone()
    except Exception:
        print("[ERROR] No institutes table. Run migrate_phase10.py first.")
        return 2

    existing = conn.execute(
        "SELECT id, role FROM users WHERE username = ?", (args.username,)).fetchone()

    if existing and existing[1] != "superadmin":
        print(f"[ERROR] '{args.username}' already exists as a {existing[1]}.")
        print("        Refusing to change an existing account's role — pick")
        print("        another username with --username.")
        return 1

    if existing:
        print(f"'{args.username}' is already a platform owner. "
              f"Setting a new password.")
    else:
        print(f"Creating platform owner '{args.username}'.")

    password = read_password()
    pw_hash = hash_password(password)

    try:
        if existing:
            conn.execute(
                "UPDATE users SET password_hash = ?, is_active = 1, "
                "must_change_password = 0 WHERE id = ?", (pw_hash, existing[0]))
        else:
            conn.execute(
                "INSERT INTO users (username, name, password_hash, role, is_active, "
                "must_change_password, institute_id) "
                "VALUES (?, ?, ?, 'superadmin', 1, 0, NULL)",
                (args.username, args.name, pw_hash))
        conn.commit()
    except Exception as e:
        print(f"[ERROR] {e}")
        return 1

    # Prove it, rather than assume the write landed as intended.
    row = conn.execute(
        "SELECT password_hash, role, institute_id FROM users WHERE username = ?",
        (args.username,)).fetchone()
    if not row or row[1] != "superadmin" or not verify_password(password, row[0]):
        print("[ERROR] the account did not save correctly.")
        return 1
    if row[2] is not None:
        print("[WARN] institute_id is set; a platform owner should belong to none.")

    print()
    print(f"[OK] '{args.username}' can now sign in at /login and lands on /superadmin.")
    print("     It belongs to no institute and sees every one of them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
