"""
send_reports.py — send monthly attendance reports by email.

Run from the project directory (inside the venv). Examples:

    python send_reports.py --dry-run              # show what WOULD be sent
    python send_reports.py --test-email me@x.com  # send one sample email to yourself
    python send_reports.py                        # send last month's reports
    python send_reports.py --month 2026-08        # send a specific month
    python send_reports.py --batch 3              # only one batch
    python send_reports.py --students-only        # skip teacher summaries

Suggested cron (1st of each month, 07:00, reports the month just ended):

    0 7 1 * * cd /home/user1/face-attendance-system && \
      ./venv/bin/python send_reports.py >> logs/reports.log 2>&1

Safe to re-run: --dry-run never sends, and every attempt is written to email_log.
"""

import argparse
import sys
import time
from datetime import date

from db import get_connection
import mailer
import reports


def parse_month(value):
    if not value:
        return reports.previous_month()
    try:
        y, m = value.split("-")
        y, m = int(y), int(m)
        if not 1 <= m <= 12:
            raise ValueError
        return y, m
    except (ValueError, AttributeError):
        print(f"[ERROR] --month must look like 2026-08 (got: {value})")
        sys.exit(2)


def active_batches(course_id=None):
    conn = get_connection()
    cur = conn.cursor()
    if course_id:
        cur.execute("SELECT id, name FROM courses WHERE id = ? AND is_active = 1", (course_id,))
    else:
        cur.execute("SELECT id, name FROM courses WHERE is_active = 1 ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows]


def batch_students(course_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, email FROM students "
        "WHERE course_id = ? AND status IN ('active','pending_registration') ORDER BY name",
        (course_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [(r[0], r[1], r[2]) for r in rows]


def batch_teachers(course_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT u.id, u.name, COALESCE(NULLIF(u.email, ''), u.username) FROM teacher_batches tb "
        "JOIN users u ON u.id = tb.user_id "
        "WHERE tb.course_id = ? AND u.is_active = 1",
        (course_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [(r[0], r[1], r[2]) for r in rows]


def looks_like_email(value):
    return bool(value) and "@" in str(value) and "." in str(value).split("@")[-1]


def main():
    ap = argparse.ArgumentParser(description="Send monthly attendance report emails")
    ap.add_argument("--month", help="YYYY-MM (default: last month)")
    ap.add_argument("--batch", type=int, help="only this course/batch id")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    ap.add_argument("--test-email", help="send one sample student+teacher email here and exit")
    ap.add_argument("--students-only", action="store_true", help="skip teacher summaries")
    ap.add_argument("--teachers-only", action="store_true", help="skip student reports")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between sends (default 1)")
    args = ap.parse_args()

    year, month = parse_month(args.month)
    label = date(year, month, 1).strftime("%B %Y")

    print(f"=== Monthly reports for {label} ===")
    if not mailer.is_configured() and not args.dry_run:
        print("[ERROR] SMTP is not configured. Set SMTP_USER and SMTP_PASSWORD in .env")
        print("        (Gmail needs an App Password: https://myaccount.google.com/apppasswords)")
        sys.exit(1)

    # --- test mode: one email to the given address, then stop ---
    if args.test_email:
        batches = active_batches(args.batch)
        if not batches:
            print("[ERROR] No active batches to build a sample from.")
            sys.exit(1)
        cid, cname = batches[0]
        trep = reports.teacher_monthly_report(cid, year, month)
        ok, msg = mailer.send_email(
            args.test_email, f"[TEST] {cname} - {label} summary",
            reports.teacher_report_email(trep), kind="test",
        )
        print(f"  teacher sample -> {args.test_email}: {'OK' if ok else 'FAILED - ' + msg}")

        students = batch_students(cid)
        if students:
            srep = reports.student_monthly_report(students[0][0], year, month)
            ok2, msg2 = mailer.send_email(
                args.test_email, f"[TEST] Attendance report - {label}",
                reports.student_report_email(srep), kind="test",
            )
            print(f"  student sample -> {args.test_email}: {'OK' if ok2 else 'FAILED - ' + msg2}")
        return

    sent = failed = skipped = 0

    for cid, cname in active_batches(args.batch):
        print(f"\n--- {cname} (id={cid}) ---")

        # ---- student reports ----
        if not args.teachers_only:
            for sid, sname, semail in batch_students(cid):
                if not looks_like_email(semail):
                    print(f"  [skip] {sname}: no valid email")
                    skipped += 1
                    continue
                rep = reports.student_monthly_report(sid, year, month)
                if not rep:
                    skipped += 1
                    continue
                subject = f"Your attendance report - {label}"
                if args.dry_run:
                    print(f"  [dry] {semail}: {rep['present_days']}/{rep['working_days']} = {rep['rate']}%")
                    continue
                ok, msg = mailer.send_email(
                    semail, subject, reports.student_report_email(rep), kind="monthly_student"
                )
                if ok:
                    sent += 1
                    print(f"  [sent] {semail} ({rep['rate']}%)")
                else:
                    failed += 1
                    print(f"  [FAIL] {semail}: {msg}")
                time.sleep(args.delay)

        # ---- teacher summary ----
        if not args.students_only:
            trep = reports.teacher_monthly_report(cid, year, month)
            for _uid, tname, tusername in batch_teachers(cid):
                temail = tusername if looks_like_email(tusername) else None
                if not temail:
                    print(f"  [skip] teacher {tname or tusername}: no email address set")
                    skipped += 1
                    continue
                subject = f"{cname} - {label} attendance summary"
                if args.dry_run:
                    print(f"  [dry] {temail}: avg {trep['avg_rate']}%, {trep['at_risk_count']} at risk")
                    continue
                ok, msg = mailer.send_email(
                    temail, subject, reports.teacher_report_email(trep), kind="monthly_teacher"
                )
                if ok:
                    sent += 1
                    print(f"  [sent] {temail} (batch summary)")
                else:
                    failed += 1
                    print(f"  [FAIL] {temail}: {msg}")
                time.sleep(args.delay)

    print(f"\n=== done: {sent} sent, {failed} failed, {skipped} skipped ===")
    if args.dry_run:
        print("(dry run - nothing was actually sent)")


if __name__ == "__main__":
    main()
