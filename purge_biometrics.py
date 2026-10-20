"""purge_biometrics.py — delete face data once its retention period is up.

The privacy notice students consent to says, in as many words:

    "Your face data is deleted within N days of your course ending."

Nothing in this system did that. BIOMETRIC_RETENTION_DAYS was read in two
places -- to print that sentence on /privacy, and to show the number on the
Settings page -- and no code anywhere deleted anything on a schedule. The only
deletion was the manual one when a student withdraws consent. So the software
made a promise it did not keep, to every student who ever read the notice.

Under the DPDP Act that is a storage-limitation failure, and it is worse than
an ordinary bug for two reasons: a student cannot discover it, and it gets
inherited by every institute onboarded onto this deployment, each of whom will
point at the same notice.

What gets deleted, for a student whose course ended more than N days ago:

    students.face_encoding      the 512-D embedding, set to NULL
    students.photo_count        reset to 0, so the UI stops claiming photos
    student_photos/<dir>/       the enrolment JPEGs on disk

What is deliberately kept, because the notice is equally explicit that it is:

    attendance, slot_attendance  "part of your academic record"
    consent_records              the proof of what was agreed and when
    the student row itself       name, roll number, email

The trigger is the course END DATE, not the registration date, because that is
what the notice says. A student on a course with no end date, or one that has
not ended, is never touched however old their enrolment.

Usage:

    python purge_biometrics.py --dry-run     # who WOULD be purged, and why
    python purge_biometrics.py               # do it
    python purge_biometrics.py --days 30     # override the retention window
    python purge_biometrics.py --institute 2 # one institute only

Suggested cron (weekly, Sunday 02:15, after the nightly backup):

    15 2 * * 0 cd $HOME/student && \
      ./venv/bin/python purge_biometrics.py >> logs/purge.log 2>&1

Idempotent: a student whose encoding is already NULL and whose photo directory
is already gone is skipped, so re-running costs nothing and a cron that fires
twice cannot do harm. Every purge is written to audit_log.
"""

import argparse
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta

from db import get_connection

DEFAULT_DAYS = int(os.getenv("BIOMETRIC_RETENTION_DAYS", "90") or 90)
PHOTO_ROOT = "student_photos"


def photo_dir_for(roll_no, name):
    """Where enrolment photos went.

    Mirrors photo_utils.create_student_photo_directory rather than calling it,
    because that function creates the directory as a side effect -- which, in a
    script whose job is deletion, would mean a --dry-run leaves new empty
    folders behind.
    """
    clean = re.sub(r"[^\w\s-]", "", name or "").strip()
    clean = re.sub(r"[-\s]+", "_", clean)
    return os.path.join(PHOTO_ROOT, f"{roll_no}_{clean}")


def due_for_purge(conn, days, institute_id=None):
    """Students whose course ended more than `days` ago and who still hold
    biometric data.

    `photo_count > 0` is part of the condition, not just the encoding: a
    student can have had their encoding cleared by a consent withdrawal while
    the JPEGs stayed on disk, and those are biometric data too.
    """
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    sql = (
        "SELECT s.id, s.student_id, s.name, c.name, c.end_date, "
        "       (s.face_encoding IS NOT NULL), s.photo_count, c.institute_id "
        "FROM students s JOIN courses c ON c.id = s.course_id "
        "WHERE c.end_date IS NOT NULL AND c.end_date != '' AND c.end_date < ? "
        "  AND (s.face_encoding IS NOT NULL OR COALESCE(s.photo_count, 0) > 0)"
    )
    params = [cutoff]
    if institute_id is not None:
        sql += " AND c.institute_id = ?"
        params.append(int(institute_id))
    sql += " ORDER BY c.end_date, s.name"
    return cutoff, conn.execute(sql, params).fetchall()


def purge(conn, rows, dry_run=False):
    cleared = photos_removed = 0
    # Per institute, because the audit log is read scoped: a single row with a
    # NULL institute_id is a platform-level entry only the superadmin sees, so
    # the institute whose students were purged would have no record of it.
    by_institute = {}
    for sid, roll, name, batch, end_date, has_enc, photo_count, institute_id in rows:
        d = photo_dir_for(roll, name)
        on_disk = os.path.isdir(d)
        print(f"  {roll:14} {(name or '')[:26]:28} {batch or '-':18} "
              f"ended {end_date}  "
              f"{'encoding' if has_enc else '        '} "
              f"{'photos' if on_disk else '      '}")
        if dry_run:
            continue
        conn.execute(
            "UPDATE students SET face_encoding = NULL, photo_count = 0 "
            "WHERE id = ?", (sid,))
        cleared += 1
        tally = by_institute.setdefault(institute_id, [0, 0])
        tally[0] += 1
        if on_disk:
            try:
                shutil.rmtree(d)
                photos_removed += 1
                tally[1] += 1
            except OSError as e:
                # Report it and carry on: a locked file must not stop the
                # rest of the purge, but it must not be silently forgotten
                # either, because the DB row will say the data is gone.
                print(f"      [WARN] could not remove {d}: {e}", file=sys.stderr)
    if not dry_run:
        for institute_id, (n, dirs) in by_institute.items():
            try:
                conn.execute(
                    "INSERT INTO audit_log (actor_type, actor_name, action, "
                    "target, details, institute_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("system", "purge_biometrics", "purge_biometrics",
                     f"{n} student(s)",
                     f"face data deleted past retention; "
                     f"photo_dirs_removed={dirs}",
                     institute_id, datetime.now().isoformat()))
            except Exception as e:
                print(f"[WARN] purge done but not audited: {e}", file=sys.stderr)
        conn.commit()
    return cleared, photos_removed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="list who would be purged and change nothing")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"retention window in days (default {DEFAULT_DAYS}, "
                         "from BIOMETRIC_RETENTION_DAYS)")
    ap.add_argument("--institute", type=int, default=None,
                    help="limit to one institute id")
    args = ap.parse_args(argv)

    if args.days < 1:
        print("[ERROR] --days must be at least 1. Refusing to purge data for "
              "courses that have only just ended.", file=sys.stderr)
        return 2

    conn = get_connection()
    cutoff, rows = due_for_purge(conn, args.days, args.institute)

    print(f"Retention {args.days} days -> purging face data for courses that "
          f"ended on or before {cutoff}")
    if not rows:
        print("Nothing to purge.")
        return 0

    print(f"\n{len(rows)} student(s) hold biometric data past retention:\n")
    cleared, photos = purge(conn, rows, dry_run=args.dry_run)

    if args.dry_run:
        print(f"\n[DRY RUN] would clear {len(rows)} encoding(s) and their "
              f"photo directories. Nothing was changed.")
    else:
        print(f"\n[DONE] cleared {cleared} encoding(s), removed {photos} "
              f"photo directory(ies).")
        print("Attendance records, consent records and student details are "
              "untouched, as the privacy notice says.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
