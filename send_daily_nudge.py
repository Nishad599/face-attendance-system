"""
send_daily_nudge.py — tell students the same day that they weren't marked.

The weekly alert and the monthly report are both retrospective. A student whose
face wasn't recognised, or who was rejected by the liveness check, currently
finds out days later — long past the point where a teacher could confirm they
were actually there. This closes that loop to a few hours.

Run late afternoon, after the last slot. Examples:

    python send_daily_nudge.py --dry-run          # who WOULD be nudged
    python send_daily_nudge.py                    # send today's nudges
    python send_daily_nudge.py --date 2026-09-03  # a specific day
    python send_daily_nudge.py --batch 2          # one batch only

Suggested cron (Mon-Sat at 17:30):

    30 17 * * 1-6 cd $HOME/student && \
      ./venv/bin/python send_daily_nudge.py >> logs/nudge.log 2>&1

Skips Sundays, holidays, students on approved leave, and anyone already
nudged for that date (alert_log, kind='not_marked'). Safe to re-run.
"""

import argparse
import sys
import time
from datetime import date, datetime, timedelta

from db import get_connection
import mailer
import reports


def parse_day(value):
    if not value:
        return date.today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        print(f"[ERROR] --date must look like 2026-09-03 (got: {value})")
        sys.exit(2)


def looks_like_email(value):
    return bool(value) and "@" in str(value) and "." in str(value).split("@")[-1]


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


def is_working_day(conn, course_id, day):
    """Mon-Sat, excluding holidays that apply to this batch."""
    if day.weekday() == 6:
        return False
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM holidays WHERE date = ? AND (course_id IS NULL OR course_id = ?)",
        (day.strftime("%Y-%m-%d"), course_id),
    )
    return cur.fetchone() is None


def unmarked_students(conn, course_id, day):
    """Students in the batch with no attendance row for `day`, excluding
    anyone on approved leave and anyone who joined later."""
    cur = conn.cursor()
    day_s = day.strftime("%Y-%m-%d")
    cur.execute(
        "SELECT s.id, s.name, s.email FROM students s "
        "WHERE s.course_id = ? AND s.status IN ('active','pending_registration') "
        "  AND (s.joining_date IS NULL OR s.joining_date <= ?) "
        "  AND NOT EXISTS (SELECT 1 FROM attendance a "
        "                  WHERE a.student_id = s.id AND a.date = ?) "
        "ORDER BY s.name",
        (course_id, day_s, day_s),
    )
    rows = cur.fetchall()

    # approved leave covering this date is an excused absence, not a miss
    try:
        cur.execute(
            "SELECT student_id FROM leave_requests WHERE status = 'approved' "
            "AND start_date <= ? AND end_date >= ?", (day_s, day_s),
        )
        on_leave = {r[0] for r in cur.fetchall()}
    except Exception:
        on_leave = set()

    return [(r[0], r[1], r[2]) for r in rows if r[0] not in on_leave]


def already_nudged(conn, student_id, day):
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM alert_log WHERE student_id = ? AND kind = 'not_marked' AND period = ?",
            (student_id, day.strftime("%Y-%m-%d")),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def record_nudge(conn, student_id, day):
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO alert_log (student_id, kind, period, rate) "
            "VALUES (?, 'not_marked', ?, NULL)",
            (student_id, day.strftime("%Y-%m-%d")),
        )
        conn.commit()
    except Exception as e:
        print(f"  [warn] could not record nudge for student {student_id}: {e}")


def nudge_email(name, day, batch):
    """Short, factual, and actionable — this is not a telling-off."""
    first = str(name).split()[0] if name else "there"
    body = (
        f'<p style="margin:0 0 12px;">Our records show no attendance for you on '
        f'<b>{day:%A, %d %B %Y}</b> in {batch}.</p>'
        '<p style="margin:0 0 12px;">If you were absent, you can ignore this message.</p>'
        '<p style="margin:0 0 8px;"><b>If you were present</b>, the camera may not have '
        'recognised you. Raise a dispute from your portal today, while your teacher can '
        'still confirm it — disputes are much easier to resolve the same day.</p>'
    )
    if mailer.base_url():
        body += (f'<p style="margin:16px 0 0;"><a href="{mailer.base_url()}/student" '
                 f'style="display:inline-block;padding:10px 18px;background:#0052CC;'
                 f'color:#ffffff;text-decoration:none;border-radius:4px;">'
                 f'Open my portal</a></p>')
    return mailer.render_email(
        title="You weren't marked present today",
        intro=f"Hi {first}, a quick check on today's attendance.",
        body_html=body,
        footer="If the camera repeatedly fails to recognise you, ask your teacher to "
               "re-register your face.",
    )


def main():
    ap = argparse.ArgumentParser(description="Same-day 'not marked' nudge")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--batch", type=int, help="only this course/batch id")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    ap.add_argument("--force", action="store_true", help="ignore the once-per-day guard")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between sends")
    args = ap.parse_args()

    day = parse_day(args.date)
    print(f"=== Same-day attendance nudge for {day:%A %d %B %Y} ===")

    if not mailer.is_configured() and not args.dry_run:
        print("[ERROR] SMTP is not configured. Set SMTP_USER and SMTP_PASSWORD in .env")
        sys.exit(1)

    conn = get_connection()
    sent = failed = skipped = 0

    for cid, cname in active_batches(args.batch):
        if not is_working_day(conn, cid, day):
            print(f"\n--- {cname}: not a working day, skipping ---")
            continue

        missing = unmarked_students(conn, cid, day)
        print(f"\n--- {cname} (id={cid}): {len(missing)} unmarked ---")

        # A whole batch unmarked usually means the class did not happen, or the
        # terminal was down — mailing everyone would be noise, not signal.
        total = conn.cursor()
        total.execute(
            "SELECT COUNT(*) FROM students WHERE course_id = ? "
            "AND status IN ('active','pending_registration')", (cid,))
        enrolled = total.fetchone()[0] or 0
        if enrolled and len(missing) == enrolled:
            print("  [skip] nobody in this batch was marked — looks like no class "
                  "or a terminal problem, not individual absences")
            skipped += len(missing)
            continue

        for sid, sname, semail in missing:
            if not looks_like_email(semail):
                skipped += 1
                continue
            if not args.force and already_nudged(conn, sid, day):
                skipped += 1
                continue
            if args.dry_run:
                print(f"  [dry] {semail} ({sname})")
                continue

            ok, msg = mailer.send_email(
                semail, f"You weren't marked present on {day:%d %b}",
                nudge_email(sname, day, cname), kind="not_marked",
            )
            if ok:
                sent += 1
                record_nudge(conn, sid, day)
                print(f"  [sent] {semail}")
            else:
                failed += 1
                print(f"  [FAIL] {semail}: {msg}")
            time.sleep(args.delay)

    conn.close()
    print(f"\n=== done: {sent} sent, {failed} failed, {skipped} skipped ===")
    if args.dry_run:
        print("(dry run - nothing was actually sent)")


if __name__ == "__main__":
    main()
