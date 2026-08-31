"""Is a given date a working day for a given batch?

Attendance must never be recorded on a Sunday or a holiday — not by the
camera, not by a teacher marking manually, and not as the side effect of
approving a dispute. Previously only one path checked holidays (and it
ignored Sundays entirely, and ignored which batch the holiday belonged to),
so the same date could be blocked in one place and accepted in another.

Working days are Monday to Saturday, excluding holidays that apply to the
batch. A holiday row with course_id NULL is institute-wide; one with a
course_id applies only to that batch.

Every function takes an open connection and never raises.
"""

from datetime import datetime

SUNDAY = 6


def as_date(value):
    """Coerce a date/datetime/'YYYY-MM-DD' into a date, or None."""
    if value is None:
        return None
    if hasattr(value, "year") and hasattr(value, "month"):
        return value.date() if hasattr(value, "hour") else value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def day_name(value):
    """'Monday' etc., or '' if the value isn't a usable date.

    Used to label dates in the teacher's review screens — approving a request
    for 'Sunday 7 Sep' should be obviously wrong at a glance.
    """
    d = as_date(value)
    return d.strftime("%A") if d else ""


def holiday_name(conn, course_id, the_date):
    """The name of the holiday on this date for this batch, or None."""
    d = as_date(the_date)
    if not d:
        return None
    try:
        row = conn.execute(
            "SELECT name FROM holidays WHERE date = ? "
            "AND (course_id IS NULL OR course_id = ?) LIMIT 1",
            (d.strftime("%Y-%m-%d"), course_id),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0] or "Holiday"


def check(conn, course_id, the_date):
    """(is_working_day, reason).

    `reason` is a human-readable explanation when the day is NOT a working
    day, suitable for showing straight back to the user, and None otherwise.
    """
    d = as_date(the_date)
    if not d:
        return False, "That is not a valid date"

    if d.weekday() == SUNDAY:
        return False, f"{d:%d %b %Y} is a Sunday — attendance cannot be marked"

    name = holiday_name(conn, course_id, d)
    if name:
        return False, f"{d:%d %b %Y} is a holiday ({name}) — attendance cannot be marked"

    return True, None


def is_working_day(conn, course_id, the_date):
    """Convenience boolean for callers that don't need the reason."""
    return check(conn, course_id, the_date)[0]
