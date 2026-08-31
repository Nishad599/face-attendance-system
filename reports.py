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


def day_credit(session_types, half_day=True):
    """How much of a day a student is credited with, from the session_type
    values recorded for them on that date.

    With half-day off, any attendance on a date is a full day (the original
    behaviour). With it on, the morning slots are worth half the day and the
    afternoon slots the other half — so arriving after lunch, or leaving at
    lunch, scores 0.5 instead of a full day.

    Anything unrecognised (including a NULL session_type, which is what a
    whole-day manual or bulk mark writes) credits the full day. Never
    penalise a student for a marking style the system itself chose.
    """
    if not half_day:
        return 1.0
    halves = set()
    for st in session_types:
        s = str(st).strip().lower() if st else ""
        if not s:
            return 1.0
        if s.startswith("morning"):
            halves.add("morning")
        elif s.startswith("afternoon"):
            halves.add("afternoon")
        else:
            return 1.0
    if not halves:
        return 1.0
    return min(1.0, 0.5 * len(halves))


def _half_day_enabled(cur, course_id):
    """Whether this batch counts half days. Opt-in per batch: switching it on
    changes every percentage the batch has ever shown."""
    if not course_id:
        return False
    try:
        cur.execute("SELECT half_day_enabled FROM courses WHERE id = ?", (course_id,))
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:
        return False        # pre-migrate_phase6 database


def _present_credits(cur, student_db_id, start, end, half_day):
    """{date: credit} for every date the student has attendance on."""
    cur.execute(
        "SELECT date, session_type FROM attendance "
        "WHERE student_id = ? AND date >= ? AND date <= ?",
        (student_db_id, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
    )
    by_date = {}
    for row in cur.fetchall():
        try:
            d = datetime.strptime(str(row[0])[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        by_date.setdefault(d, []).append(row[1])
    return {d: day_credit(sts, half_day) for d, sts in by_date.items()}


def _approved_leave_dates(cur, student_db_id, start, end):
    """Dates covered by an approved leave request, within [start, end].

    An approved absence is excused: the day is dropped from the denominator
    rather than counted against the student. Returns an empty set if the
    leave_requests table does not exist yet (pre-migrate_phase5 databases).
    """
    try:
        cur.execute(
            "SELECT start_date, end_date FROM leave_requests "
            "WHERE student_id = ? AND status = 'approved' "
            "AND start_date <= ? AND end_date >= ?",
            (student_db_id, end.strftime("%Y-%m-%d"), start.strftime("%Y-%m-%d")),
        )
        rows = cur.fetchall()
    except Exception:
        return set()

    out = set()
    for s, e in [(r[0], r[1]) for r in rows]:
        try:
            d = datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
            last = datetime.strptime(str(e)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        while d <= last:
            if start <= d <= end:
                out.add(d)
            d += timedelta(days=1)
    return out


def _student_figures(cur, student_db_id, period_start, period_end, holidays,
                     joining_date, half_day=False):
    """Present/absent/working-day figures for one student over a period.

    Shared by the monthly, cumulative and institute reports so they cannot
    drift apart. Excludes days before the student joined and days covered by
    an approved leave request. With half_day on, a day can be credited 0.5.
    """
    start = _effective_start(period_start, joining_date)
    work = _working_days(start, period_end, holidays)
    leave = _approved_leave_dates(cur, student_db_id, start, period_end)
    work = [d for d in work if d not in leave]
    credits = _present_credits(cur, student_db_id, start, period_end, half_day)

    credited = sum(credits.get(d, 0.0) for d in work)
    # "Absent" means no credit at all; a half day is listed separately so a
    # student can see the difference between missing a day and missing a half.
    absent_days = [d for d in work if credits.get(d, 0.0) <= 0]
    partial_days = [d for d in work if 0 < credits.get(d, 0.0) < 1]

    return {
        "start": start,
        "end": period_end,
        "present_days": round(credited, 1),
        "absent_days": absent_days,
        "partial_days": partial_days,
        "working": work,
        "leave_days": len(leave),
        "rate": round(credited / len(work) * 100, 1) if work else 0.0,
    }


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
    holidays = _holiday_dates(cur, row[4])
    half_day = _half_day_enabled(cur, row[4])
    # _student_figures skips days before the student joined (a mid-month joiner
    # must not look absent for days they weren't enrolled) and days covered by
    # approved leave. Matches how analytics_manager computes rates.
    fig = _student_figures(cur, student_db_id, start, end, holidays, row[6], half_day)

    conn.close()
    return {
        "student_db_id": row[0],
        "roll_no": row[1],
        "name": row[2],
        "email": row[3],
        "course_id": row[4],
        "batch": row[5] or "-",
        "month_label": fig["start"].strftime("%B %Y"),
        "period": f'{fig["start"]:%d %b} - {end:%d %b %Y}',
        "present_days": fig["present_days"],
        "absent_days": len(fig["absent_days"]),
        "partial_days": len(fig["partial_days"]),
        "half_day": half_day,
        "working_days": len(fig["working"]),
        "leave_days": fig["leave_days"],
        "rate": fig["rate"],
        "absent_dates": [d.strftime("%d %b (%a)") for d in fig["absent_days"]],
        "partial_dates": [d.strftime("%d %b (%a)") for d in fig["partial_days"]],
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

    half_day = _half_day_enabled(cur, course_id)
    rows, rate_sum = [], 0.0
    for sid, roll, name, joining in students:
        fig = _student_figures(cur, sid, start, end, holidays, joining, half_day)
        rate_sum += fig["rate"]
        rows.append({"roll_no": roll, "name": name,
                     "present": fig["present_days"],
                     "working_days": len(fig["working"]),
                     "leave_days": fig["leave_days"],
                     "partial_days": len(fig["partial_days"]),
                     "rate": fig["rate"]})

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


def student_cumulative_report(student_db_id: int, at_risk_threshold: float = 75.0):
    """Course-to-date figures for one student (batch start -> today).

    Drives the weekly low-attendance alert. Monthly reports are retrospective;
    a student needs the running total to know whether they can still recover.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT s.id, s.student_id, s.name, s.email, s.course_id, c.name, "
        "       s.joining_date, c.start_date, c.end_date "
        "FROM students s LEFT JOIN courses c ON c.id = s.course_id WHERE s.id = ?",
        (student_db_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    today = date.today()
    try:
        start = datetime.strptime(str(row[7])[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        start = today.replace(month=1, day=1)

    holidays = _holiday_dates(cur, row[4])
    half_day = _half_day_enabled(cur, row[4])
    fig = _student_figures(cur, student_db_id, start, today, holidays, row[6], half_day)

    # How many of the remaining days must be attended to reach the threshold?
    # This is the number that actually changes behaviour.
    remaining = 0
    try:
        course_end = datetime.strptime(str(row[8])[:10], "%Y-%m-%d").date()
        if course_end > today:
            future = _working_days(today + timedelta(days=1), course_end, holidays)
            remaining = len(future)
    except (ValueError, TypeError):
        course_end = None

    present = fig["present_days"]
    total_work = len(fig["working"])
    needed = None
    if remaining:
        # smallest n where (present + n) / (total_work + remaining) >= threshold
        target = at_risk_threshold / 100.0
        need = target * (total_work + remaining) - present
        needed = max(0, min(remaining, int(need) + (1 if need > int(need) else 0)))

    conn.close()
    return {
        "student_db_id": row[0],
        "roll_no": row[1],
        "name": row[2],
        "email": row[3],
        "course_id": row[4],
        "batch": row[5] or "-",
        "period": f"{start:%d %b %Y} - {today:%d %b %Y}",
        "present_days": present,
        "absent_days": len(fig["absent_days"]),
        "working_days": total_work,
        "leave_days": fig["leave_days"],
        "rate": fig["rate"],
        "remaining_days": remaining,
        "days_needed": needed,
        "threshold": at_risk_threshold,
        "recent_absences": [d.strftime("%d %b (%a)") for d in fig["absent_days"][-8:]],
    }


def institute_report(year: int, month: int, at_risk_threshold: float = 75.0):
    """Every active batch in one view — the admin-level report teachers'
    per-batch exports cannot give."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM courses WHERE is_active = 1 ORDER BY name")
    batches = [(r[0], r[1]) for r in cur.fetchall()]
    conn.close()

    rows = []
    total_students = at_risk_total = 0
    rate_sum = 0.0
    for cid, cname in batches:
        rep = teacher_monthly_report(cid, year, month, at_risk_threshold)
        rows.append({
            "course_id": cid,
            "batch": cname,
            "students": rep["total_students"],
            "working_days": rep["working_days"],
            "avg_rate": rep["avg_rate"],
            "at_risk_count": rep["at_risk_count"],
            "at_risk": rep["at_risk"],
        })
        total_students += rep["total_students"]
        at_risk_total += rep["at_risk_count"]
        # Weight each batch by its student count so a 4-student batch does not
        # swing the institute average as hard as a 60-student one.
        rate_sum += rep["avg_rate"] * rep["total_students"]

    start, end = month_bounds(year, month)
    rows.sort(key=lambda r: r["avg_rate"])
    return {
        "month_label": start.strftime("%B %Y"),
        "period": f"{start:%d %b} - {end:%d %b %Y}",
        "batches": rows,
        "batch_count": len(rows),
        "total_students": total_students,
        "at_risk_total": at_risk_total,
        "avg_rate": round(rate_sum / total_students, 1) if total_students else 0.0,
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


def low_attendance_alert_email(rep):
    """HTML body for the weekly low-attendance warning.

    Deliberately forward-looking: the monthly report already says what
    happened, so this one leads with what the student still has to do.
    """
    colour = _pct_colour(rep["rate"])
    big = (
        f'<div style="text-align:center;margin:8px 0 20px;">'
        f'<div style="font-size:40px;font-weight:bold;color:{colour};">{rep["rate"]}%</div>'
        f'<div style="color:#6B778C;font-size:12px;">ATTENDANCE SO FAR</div></div>'
    )

    action = ""
    needed = rep.get("days_needed")
    remaining = rep.get("remaining_days") or 0
    if needed is None or not remaining:
        action = ('<p style="margin:0;padding:12px;background:#FFEBE6;color:#BF2600;border-radius:4px;">'
                  f'<b>You are below {rep["threshold"]:.0f}%.</b> Please speak to your teacher '
                  'about how to make this up.</p>')
    elif needed >= remaining:
        action = ('<p style="margin:0;padding:12px;background:#FFEBE6;color:#BF2600;border-radius:4px;">'
                  f'<b>Attending every one of the {remaining} remaining days will still leave you '
                  f'short of {rep["threshold"]:.0f}%.</b> Please speak to your teacher now — the '
                  'earlier this is raised, the more options you have.</p>')
    else:
        action = ('<p style="margin:0;padding:12px;background:#FFFAE6;color:#7A5B00;border-radius:4px;">'
                  f'<b>You can still recover.</b> Attend at least <b>{needed}</b> of the '
                  f'{remaining} remaining working days to finish at {rep["threshold"]:.0f}% or above.</p>')

    table = mailer.stat_table([
        ("Batch", rep["batch"]),
        ("Period", rep["period"]),
        ("Days present", rep["present_days"]),
        ("Days absent", rep["absent_days"]),
        ("Working days so far", rep["working_days"]),
        ("Approved leave (not counted)", rep["leave_days"]),
        ("Working days remaining", remaining),
    ])

    recent = ""
    if rep["recent_absences"]:
        recent = ('<p style="margin:16px 0 6px;font-weight:bold;">Most recent absences</p>'
                  f'<p style="margin:0 0 8px;color:#6B778C;">{", ".join(rep["recent_absences"])}</p>')

    link = ""
    if mailer.base_url():
        link = (f'<p style="margin:16px 0 0;"><a href="{mailer.base_url()}/student" '
                f'style="color:#0052CC;">View your full attendance</a></p>')

    return mailer.render_email(
        title="Your attendance needs attention",
        intro=f"Hi {str(rep['name']).split()[0] if rep['name'] else 'there'}, "
              f"this is a heads-up about your attendance in {rep['batch']}.",
        body_html=big + action + table + recent + link,
        footer="If a day looks wrong, raise a dispute from your student portal. "
               "Planned absences can be submitted as a leave request in advance.",
    )


def institute_report_email(rep):
    """HTML body for the admin's cross-batch monthly summary."""
    table = mailer.stat_table([
        ("Period", rep["period"]),
        ("Batches", rep["batch_count"]),
        ("Students", rep["total_students"]),
        ("Average attendance", f'{rep["avg_rate"]}%'),
        ("Below 75%", rep["at_risk_total"]),
    ])

    trs = "".join(
        f'<tr><td style="padding:6px 12px;border-bottom:1px solid #DFE1E6;">{b["batch"]}'
        f'<br><span style="color:#6B778C;font-size:12px;">{b["students"]} students</span></td>'
        f'<td style="padding:6px 12px;border-bottom:1px solid #DFE1E6;text-align:right;'
        f'font-weight:bold;color:{_pct_colour(b["avg_rate"])};">{b["avg_rate"]}%</td>'
        f'<td style="padding:6px 12px;border-bottom:1px solid #DFE1E6;text-align:right;'
        f'color:#6B778C;">{b["at_risk_count"]} at risk</td></tr>'
        for b in rep["batches"]
    )
    body = table
    if rep["batches"]:
        body += ('<p style="margin:16px 0 6px;font-weight:bold;">By batch (lowest first)</p>'
                 f'<table style="width:100%;border-collapse:collapse;">{trs}</table>')
    else:
        body += '<p style="color:#6B778C;">No active batches.</p>'

    if mailer.base_url():
        body += (f'<p style="margin:16px 0 0;"><a href="{mailer.base_url()}/dashboard" '
                 f'style="color:#0052CC;">Open the admin dashboard</a></p>')

    return mailer.render_email(
        title=f"Institute attendance - {rep['month_label']}",
        intro=f"Attendance across all active batches for {rep['month_label']}.",
        body_html=body,
        footer="Per-batch registers and CSV exports are available in the portal.",
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
