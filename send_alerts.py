"""
send_alerts.py — weekly low-attendance warnings + optional admin digest.

Monthly reports are retrospective: a student below 75% finds out after the
month has already closed. This runs weekly on the running course-to-date
total, so there is still time to act.

Run from the project directory (inside the venv). Examples:

    python send_alerts.py --dry-run            # show who WOULD be warned
    python send_alerts.py                      # send the weekly alerts
    python send_alerts.py --threshold 80       # warn below 80% instead of 75%
    python send_alerts.py --batch 3            # only one batch
    python send_alerts.py --admin-digest       # also mail admins a summary
    python send_alerts.py --force              # ignore the once-a-week guard

Suggested cron (every Monday, 07:30):

    30 7 * * 1 cd $HOME/student && \
      ./venv/bin/python send_alerts.py >> logs/alerts.log 2>&1

Each send is recorded in alert_log (student + kind + ISO week), so re-running
in the same week is a no-op unless --force is passed. Every attempt is also
written to email_log by mailer.send_email.
"""

import argparse
import sys
import time
from datetime import date

from db import get_connection
import mailer
import reports


def iso_period(today=None):
    """ISO week label, e.g. 2026-W35 — the de-duplication key."""
    today = today or date.today()
    y, w, _ = today.isocalendar()
    return f"{y}-W{w:02d}"


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


def already_alerted(student_id, period, kind="low_attendance"):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM alert_log WHERE student_id = ? AND kind = ? AND period = ?",
            (student_id, kind, period),
        )
        return cur.fetchone() is not None
    except Exception:
        # Table missing (migrate_phase5 not run) — do not block sending.
        return False
    finally:
        conn.close()


def record_alert(student_id, period, rate, kind="low_attendance"):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO alert_log (student_id, kind, period, rate) VALUES (?, ?, ?, ?)",
            (student_id, kind, period, rate),
        )
        conn.commit()
    except Exception as e:
        # A duplicate here just means it was already logged; anything else is
        # worth seeing in the cron log but must not abort the whole run.
        print(f"  [warn] could not record alert for student {student_id}: {e}")
    finally:
        conn.close()


def admin_emails():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(NULLIF(email, ''), username) FROM users "
        "WHERE role = 'admin' AND is_active = 1"
    )
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return [r for r in rows if looks_like_email(r)]


def looks_like_email(value):
    return bool(value) and "@" in str(value) and "." in str(value).split("@")[-1]


def main():
    ap = argparse.ArgumentParser(description="Send weekly low-attendance alerts")
    ap.add_argument("--threshold", type=float, default=75.0,
                    help="warn students below this percentage (default 75)")
    ap.add_argument("--batch", type=int, help="only this course/batch id")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    ap.add_argument("--force", action="store_true",
                    help="send even if an alert was already sent this week")
    ap.add_argument("--admin-digest", action="store_true",
                    help="also email admins an institute-wide summary")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between sends (default 1)")
    args = ap.parse_args()

    period = iso_period()
    print(f"=== Low-attendance alerts, week {period} (threshold {args.threshold}%) ===")

    if not mailer.is_configured() and not args.dry_run:
        print("[ERROR] SMTP is not configured. Set SMTP_USER and SMTP_PASSWORD in .env")
        sys.exit(1)

    sent = failed = skipped = ok_students = 0

    for cid, cname in active_batches(args.batch):
        print(f"\n--- {cname} (id={cid}) ---")
        for sid, sname, semail in batch_students(cid):
            rep = reports.student_cumulative_report(sid, args.threshold)
            if not rep:
                skipped += 1
                continue

            # Only warn students who have actually accrued some working days;
            # a batch that started yesterday would otherwise alert everyone.
            if rep["working_days"] < 5:
                skipped += 1
                continue

            if rep["rate"] >= args.threshold:
                ok_students += 1
                continue

            if not looks_like_email(semail):
                print(f"  [skip] {sname}: below threshold ({rep['rate']}%) but no valid email")
                skipped += 1
                continue

            if not args.force and already_alerted(sid, period):
                print(f"  [skip] {sname}: already alerted this week")
                skipped += 1
                continue

            if args.dry_run:
                need = rep["days_needed"]
                tail = f", needs {need}/{rep['remaining_days']} remaining" if need is not None else ""
                print(f"  [dry] {semail}: {rep['rate']}% "
                      f"({rep['present_days']}/{rep['working_days']}){tail}")
                continue

            ok, msg = mailer.send_email(
                semail, f"Attendance alert - you are at {rep['rate']}%",
                reports.low_attendance_alert_email(rep), kind="low_attendance",
            )
            if ok:
                sent += 1
                record_alert(sid, period, rep["rate"])
                print(f"  [sent] {semail} ({rep['rate']}%)")
            else:
                failed += 1
                print(f"  [FAIL] {semail}: {msg}")
            time.sleep(args.delay)

    # --- optional admin digest ------------------------------------------
    if args.admin_digest:
        print("\n--- admin digest ---")
        today = date.today()
        rep = reports.institute_report(today.year, today.month, args.threshold)
        recipients = admin_emails()
        if not recipients:
            print("  [skip] no admin account has a valid email address")
        for addr in recipients:
            if args.dry_run:
                print(f"  [dry] {addr}: {rep['batch_count']} batches, "
                      f"avg {rep['avg_rate']}%, {rep['at_risk_total']} at risk")
                continue
            ok, msg = mailer.send_email(
                addr, f"Institute attendance - {rep['month_label']}",
                reports.institute_report_email(rep), kind="admin_digest",
            )
            if ok:
                sent += 1
                print(f"  [sent] {addr}")
            else:
                failed += 1
                print(f"  [FAIL] {addr}: {msg}")
            time.sleep(args.delay)

    print(f"\n=== done: {sent} sent, {failed} failed, {skipped} skipped, "
          f"{ok_students} above threshold ===")
    if args.dry_run:
        print("(dry run - nothing was actually sent)")


if __name__ == "__main__":
    main()
