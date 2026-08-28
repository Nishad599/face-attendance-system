"""
reports.py — monthly attendance report data + email HTML builders.

Pure data/formatting: nothing here sends email (see mailer.py / send_reports.py).
All figures come from the primary `attendance` table, counting a student present
on a date if they have any attendance row that day. Working days are Mon-Sat
excluding holidays that apply to the batch.
"""

from datetime import date, datetime, timedelta
from calendar import monthrange

from db import get_connection
import mailer


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def month_bounds(year: int, month: int):
    """First and last date of a month (last capped at today for the current month)."""
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    today = date.today()
    if last > today:
        last = today
    return first, last


def previous_month(today: date = None):
    """(year, month) of the month before `today`."""
    today = today or date.today()
    first_of_this = today.replace(day=1)
    prev = first_of_this - timedelta(days=1)
    return prev.year, prev.month


def _holiday_dates(cur, course_id):
    out = set()
    cur.execute("SELECT date FROM holidays WHERE course_id IS NULL OR course_id = ?", (course_id,))
    for row in cur.fetchall():
        try:
            out.add(datetime.strptime(str(row[0])[:10], "%Y-%m-%d").date())
        except (ValueError, TypeError):
            continue
    return out


def _working_days(start, end, holidays):
    days, d = [], start
    while d <= end:
        if d.weekday() != 6 and d not in holidays:      # 6 = Sunday
            days.append(d)
        d += timedelta(days=1)
    return days


def _effective_start(period_start, joining_date):
    """Later of the period start and the student's joining date.

    A student who joined mid-period must not be counted absent for the days
    before they enrolled.
    """
    if not joining_date:
        return period_start
    try:
        joined = datetime.strptime(str(joining_date)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return period_start
    return max(period_start, joined)


def _present_dates(cur, student_db_id, start, end):
    cur.execute(
        "SELECT DISTINCT date FROM attendance WHERE student_id = ? AND date >= ? AND date <= ?",
        (student_db_id, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
    )
    out = set()
    for row in cur.fetchall():
        try:
            out.add(datetime.strptime(str(row[0])[:10], "%Y-%m-%d").date())
        except (ValueError, TypeError):
            continue
    return out


# ---------------------------------------------------------------------------
# report data
# ---------------------------------------------------------------------------

def student_monthly_report(student_db_id: int, year: int, month: int):
    """Monthly figures for one student. Returns None if the student is missing."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT s.id, s.student_id, s.name, s.email, s.course_id, c.name, s.joining_date "
        "FROM students s LEFT JOIN courses c ON c.id = s.course_id WHERE s.id = ?",
        (student_db_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    start, end = month_bounds(year, month)
    # Don't count days before the student joined — otherwise a mid-month joiner
    # looks absent for days they weren't enrolled (and gets wrongly flagged
    # at-risk). Matches how analytics_manager computes rates.
    start = _effective_start(start, row[6])
    holidays = _holiday_dates(cur, row[4])
    work = _working_days(start, end, holidays)
    present = _present_dates(cur, student_db_id, start, end)

    present_days = [d for d in work if d in present]
    absent_days = [d for d in work if d not in present]
    rate = round(len(present_days) / len(work) * 100, 1) if work else 0.0

    conn.close()
    return {
        "student_db_id": row[0],
        "roll_no": row[1],
        "name": row[2],
        "email": row[3],
        "course_id": row[4],
        "batch": row[5] or "-",
        "month_label": start.strftime("%B %Y"),
        "period": f"{start:%d %b} - {end:%d %b %Y}",
        "present_days": len(present_days),
        "absent_days": len(absent_days),
        "working_days": len(work),
        "rate": rate,
        "absent_dates": [d.strftime("%d %b (%a)") for d in absent_days],
    }


def teacher_monthly_report(course_id: int, year: int, month: int, at_risk_threshold: float = 75.0):
    """Batch-level monthly figures for a teacher."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM courses WHERE id = ?", (course_id,))
    crow = cur.fetchone()
    batch_name = crow[0] if crow else f"Batch {course_id}"

    start, end = month_bounds(year, month)
    holidays = _holiday_dates(cur, course_id)
    work = _working_days(start, end, holidays)

    cur.execute(
        "SELECT id, student_id, name, joining_date FROM students "
        "WHERE course_id = ? AND status IN ('active','pending_registration') ORDER BY name",
        (course_id,),
    )
    students = cur.fetchall()

    rows, rate_sum = [], 0.0
    for sid, roll, name, joining in students:
        # Each student's window starts when they joined (see _effective_start)
        s_start = _effective_start(start, joining)
        s_work = work if s_start == start else _working_days(s_start, end, holidays)
        present = _present_dates(cur, sid, s_start, end)
        pcount = sum(1 for d in s_work if d in present)
        rate = round(pcount / len(s_work) * 100, 1) if s_work else 0.0
        rate_sum += rate
        rows.append({"roll_no": roll, "name": name, "present": pcount,
                     "working_days": len(s_work), "rate": rate})

    conn.close()
    rows.sort(key=lambda r: r["rate"])
    at_risk = [r for r in rows if r["rate"] < at_risk_threshold]
    return {
        "course_id": course_id,
        "batch": batch_name,
        "month_label": start.strftime("%B %Y"),
        "period": f"{start:%d %b} - {end:%d %b %Y}",
        "total_students": len(students),
        "working_days": len(work),
        "avg_rate": round(rate_sum / len(students), 1) if students else 0.0,
        "at_risk": at_risk,
        "at_risk_count": len(at_risk),
        "students": rows,
        "best": list(reversed(rows[-5:])) if rows else [],
    }


# ---------------------------------------------------------------------------
# email HTML
# ---------------------------------------------------------------------------

def _pct_colour(rate):
    return "#DE350B" if rate < 75 else ("#FF8B00" if rate < 85 else "#00875A")


def student_report_email(rep):
    """HTML body for a student's monthly report."""
    colour = _pct_colour(rep["rate"])
    big = (
        f'<div style="text-align:center;margin:8px 0 20px;">'
        f'<div style="font-size:40px;font-weight:bold;color:{colour};">{rep["rate"]}%</div>'
        f'<div style="color:#6B778C;font-size:12px;">ATTENDANCE THIS MONTH</div></div>'
    )
    table = mailer.stat_table([
        ("Batch", rep["batch"]),
        ("Period", rep["period"]),
        ("Days present", rep["present_days"]),
        ("Days absent", rep["absent_days"]),
        ("Working days", rep["working_days"]),
    ])

    absents = ""
    if rep["absent_dates"]:
        shown = ", ".join(rep["absent_dates"][:20])
        more = "" if len(rep["absent_dates"]) <= 20 else f" (+{len(rep['absent_dates']) - 20} more)"
        absents = (
            f'<p style="margin:0 0 8px;font-weight:bold;">Days you were absent</p>'
            f'<p style="margin:0 0 16px;color:#6B778C;">{shown}{more}</p>'
        )

    note = ""
    if rep["rate"] < 75:
        note = ('<p style="margin:0;padding:12px;background:#FFEBE6;color:#BF2600;border-radius:4px;">'
                '<b>Your attendance is below 75%.</b> Please speak to your teacher.</p>')

    link = ""
    if mailer.base_url():
        link = (f'<p style="margin:16px 0 0;"><a href="{mailer.base_url()}/student" '
                f'style="color:#0052CC;">View full attendance in the portal</a></p>')

    return mailer.render_email(
        title=f"Attendance report - {rep['month_label']}",
        intro=f"Hi {rep['name'].split()[0]}, here is your attendance summary for {rep['month_label']}.",
        body_html=big + table + absents + note + link,
        footer="If a day looks wrong, raise a dispute from your student portal.",
    )


def teacher_report_email(rep):
    """HTML body for a teacher's batch monthly report."""
    table = mailer.stat_table([
        ("Batch", rep["batch"]),
        ("Period", rep["period"]),
        ("Students", rep["total_students"]),
        ("Working days", rep["working_days"]),
        ("Average attendance", f'{rep["avg_rate"]}%'),
        ("Below 75%", rep["at_risk_count"]),
    ])

    def rows_table(title, items, colour):
        if not items:
            return ""
        trs = "".join(
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #DFE1E6;">{i["name"]}'
            f'<br><span style="color:#6B778C;font-size:12px;">{i["roll_no"]}</span></td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #DFE1E6;text-align:right;'
            f'font-weight:bold;color:{colour};">{i["rate"]}%</td></tr>'
            for i in items
        )
        return (f'<p style="margin:16px 0 6px;font-weight:bold;">{title}</p>'
                f'<table style="width:100%;border-collapse:collapse;">{trs}</table>')

    body = table
    body += rows_table(f"Needs attention (below 75%)", rep["at_risk"][:10], "#DE350B")
    body += rows_table("Top attendance", rep["best"], "#00875A")

    if mailer.base_url():
        body += (f'<p style="margin:16px 0 0;"><a href="{mailer.base_url()}/teacher" '
                 f'style="color:#0052CC;">Open the teacher portal</a></p>')

    return mailer.render_email(
        title=f"{rep['batch']} - {rep['month_label']} summary",
        intro=f"Monthly attendance summary for <b>{rep['batch']}</b>.",
        body_html=body,
        footer="Full registers and CSV export are available in the teacher portal.",
    )


def welcome_email(name, roll_no, password, batch=None):
    """HTML body sent to a newly onboarded student with their login details."""
    table = mailer.stat_table([
        ("Roll number (username)", roll_no),
        ("Temporary password", password),
        ("Batch", batch or "-"),
    ])
    link = ""
    if mailer.base_url():
        link = (f'<p style="margin:16px 0 0;"><a href="{mailer.base_url()}/login" '
                f'style="display:inline-block;padding:10px 18px;background:#0052CC;color:#ffffff;'
                f'text-decoration:none;border-radius:4px;">Log in to your portal</a></p>')
    warn = ('<p style="margin:0;padding:12px;background:#FFFAE6;color:#7A5B00;border-radius:4px;">'
            'You will be asked to set a new password the first time you log in. '
            'Keep these details private.</p>')
    return mailer.render_email(
        title="Your attendance portal account",
        intro=f"Hi {str(name).split()[0] if name else 'there'}, an account has been created for you.",
        body_html=table + warn + link,
        footer="If you did not expect this email, please contact your institute.",
    )


def otp_email(name, otp, minutes=15):
    """HTML body for a password-reset OTP."""
    code = (f'<div style="text-align:center;margin:12px 0 20px;">'
            f'<div style="display:inline-block;padding:14px 28px;background:#F4F5F7;'
            f'border:1px dashed #C1C7D0;border-radius:6px;font-size:30px;'
            f'letter-spacing:6px;font-weight:bold;color:#172B4D;">{otp}</div></div>')
    body = code + (
        f'<p style="margin:0 0 8px;">This code expires in <b>{minutes} minutes</b> '
        f'and can be used once.</p>'
        f'<p style="margin:0;color:#6B778C;">If you did not request a password reset, '
        f'ignore this email — your password has not changed.</p>'
    )
    return mailer.render_email(
        title="Password reset code",
        intro=f"Hi {str(name).split()[0] if name else 'there'}, use the code below to reset your password.",
        body_html=body,
        footer="Never share this code with anyone.",
    )
