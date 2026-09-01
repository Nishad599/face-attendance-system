"""Subjects, the weekly timetable, and subject-wise attendance.

One table drives two features: `timetable` maps (course, weekday, slot) to a
subject, which gives students a readable weekly schedule AND tells the marking
code which module a given attendance row belongs to.

Attendance stores `subject_id` at the moment it is marked rather than joining
through the timetable at read time. If the timetable is rearranged next term,
history must keep pointing at the subject the class was actually taken for.

Every function takes an open connection and degrades quietly on a database
that has not run migrate_phase6 — attendance marking must never fail because
a batch has no timetable set up.
"""

from datetime import datetime, timedelta

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]

# Kept in step with SESSION_SLOT_TYPES in the app.
SLOTS = ["morning_1", "morning_2", "afternoon_1", "afternoon_2"]

SLOT_LABELS = {
    "morning_1": "Morning 1",
    "morning_2": "Morning 2",
    "afternoon_1": "Afternoon 1",
    "afternoon_2": "Afternoon 2",
}


def _as_date(value):
    if hasattr(value, "weekday"):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def subject_for_slot(conn, course_id, the_date, session_type):
    """Which subject occupies this slot on this date, or None.

    Returns None for anything unmapped rather than raising — a batch with no
    timetable still marks attendance, just without subject attribution.
    """
    d = _as_date(the_date)
    if not course_id or not d or not session_type:
        return None
    try:
        row = conn.execute(
            "SELECT subject_id FROM timetable "
            "WHERE course_id = ? AND weekday = ? AND session_type = ?",
            (course_id, d.weekday(), str(session_type).strip().lower()),
        ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def list_subjects(conn, course_id, active_only=True):
    sql = ("SELECT id, name, code, min_attendance, start_date, end_date, is_active "
           "FROM subjects WHERE course_id = ?")
    params = [course_id]
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY name"
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []
    return [{"id": r[0], "name": r[1], "code": r[2], "min_attendance": r[3],
             "start_date": str(r[4])[:10] if r[4] else None,
             "end_date": str(r[5])[:10] if r[5] else None,
             "is_active": bool(r[6])} for r in rows]


def get_grid(conn, course_id):
    """The weekly timetable as a list of rows, one per weekday.

    Sunday is included but normally empty — working days are Mon-Sat.
    """
    subjects = {s["id"]: s for s in list_subjects(conn, course_id, active_only=False)}
    try:
        rows = conn.execute(
            "SELECT weekday, session_type, subject_id, room FROM timetable "
            "WHERE course_id = ?", (course_id,)
        ).fetchall()
    except Exception:
        rows = []

    by_key = {(r[0], r[1]): (r[2], r[3]) for r in rows}
    grid = []
    for wd in range(7):
        cells = []
        for slot in SLOTS:
            subject_id, room = by_key.get((wd, slot), (None, None))
            subj = subjects.get(subject_id)
            cells.append({
                "session_type": slot,
                "slot_label": SLOT_LABELS.get(slot, slot),
                "subject_id": subject_id,
                "subject_name": subj["name"] if subj else None,
                "subject_code": subj["code"] if subj else None,
                "room": room,
            })
        grid.append({"weekday": wd, "day": WEEKDAYS[wd], "slots": cells})
    return grid


def set_grid(conn, course_id, entries):
    """Replace the timetable for a batch.

    `entries` is a list of {weekday, session_type, subject_id, room}. An entry
    with a falsy subject_id clears that cell.
    """
    cur = conn.cursor()
    written = 0
    for e in entries or []:
        try:
            wd = int(e.get("weekday"))
            slot = str(e.get("session_type") or "").strip().lower()
        except (TypeError, ValueError):
            continue
        if not 0 <= wd <= 6 or slot not in SLOTS:
            continue

        subject_id = e.get("subject_id") or None
        room = (e.get("room") or "").strip() or None

        cur.execute(
            "DELETE FROM timetable WHERE course_id = ? AND weekday = ? AND session_type = ?",
            (course_id, wd, slot),
        )
        if subject_id or room:
            cur.execute(
                "INSERT INTO timetable (course_id, weekday, session_type, subject_id, room) "
                "VALUES (?, ?, ?, ?, ?)",
                (course_id, wd, slot, subject_id, room),
            )
            written += 1
    conn.commit()
    return written


def course_calendar(conn, course_id, today=None):
    """The published academic calendar: modules and events on one timeline.

    Modules and events live in separate tables on purpose — only a module can
    have attendance attributed to it — but students need to read them as a
    single schedule, so they are merged here and sorted by date.
    """
    from datetime import date as _date
    today = _as_date(today) or _date.today()

    items = []

    try:
        rows = conn.execute(
            "SELECT id, name, code, sequence, faculty, coordinator, hours, "
            "       teaching_days, exam_date, start_date, end_date, min_attendance "
            "FROM subjects WHERE course_id = ? AND is_active = 1", (course_id,)
        ).fetchall()
    except Exception:
        rows = []
    for r in rows:
        start, end = _as_date(r[9]), _as_date(r[10])
        items.append({
            "type": "module", "id": r[0], "title": r[1], "code": r[2],
            "sequence": r[3], "faculty": r[4], "coordinator": r[5],
            "hours": r[6], "days": r[7],
            "exam_date": str(r[8])[:10] if r[8] else None,
            "start_date": str(r[9])[:10] if r[9] else None,
            "end_date": str(r[10])[:10] if r[10] else None,
            "min_attendance": r[11],
            "_sort": start or today,
        })

    try:
        rows = conn.execute(
            "SELECT id, title, kind, start_date, end_date, notes, coordinator "
            "FROM academic_events WHERE course_id = ?", (course_id,)
        ).fetchall()
    except Exception:
        rows = []
    for r in rows:
        start, end = _as_date(r[3]), _as_date(r[4])
        items.append({
            "type": "event", "id": r[0], "title": r[1], "kind": r[2],
            "start_date": str(r[3])[:10] if r[3] else None,
            "end_date": str(r[4])[:10] if r[4] else None,
            "notes": r[5], "coordinator": r[6],
            "_sort": start or today,
        })

    for it in items:
        start = _as_date(it.get("start_date"))
        end = _as_date(it.get("end_date")) or start
        if not start:
            it["status"] = "upcoming"
        elif end and end < today:
            it["status"] = "done"
        elif start <= today and (not end or today <= end):
            it["status"] = "current"
        else:
            it["status"] = "upcoming"

    # Sort by start date, then put modules before same-day events so a module
    # beginning on the day of an event reads first.
    items.sort(key=lambda i: (i["_sort"], 0 if i["type"] == "module" else 1))
    for it in items:
        it.pop("_sort", None)
    return items


def _holiday_dates(conn, course_id):
    out = set()
    try:
        rows = conn.execute(
            "SELECT date FROM holidays WHERE course_id IS NULL OR course_id = ?",
            (course_id,),
        ).fetchall()
    except Exception:
        return out
    for r in rows:
        d = _as_date(r[0])
        if d:
            out.add(d)
    return out


def subject_attendance(conn, course_id, student_db_id=None, upto=None):
    """Per-subject attendance for a batch, or for one student.

    A subject's expected classes are counted from the timetable: every date in
    the batch window whose weekday/slot maps to that subject, excluding Sundays
    and holidays. Attended classes come from attendance rows carrying the
    subject_id, which is why marking records it at write time.
    """
    from datetime import date as _date
    upto = _as_date(upto) or _date.today()

    subjects = list_subjects(conn, course_id)
    if not subjects:
        return []

    row = conn.execute(
        "SELECT start_date, end_date FROM courses WHERE id = ?", (course_id,)
    ).fetchone()
    if not row:
        return []
    course_start = _as_date(row[0]) or upto
    course_end = _as_date(row[1]) or upto
    end = min(upto, course_end)

    try:
        tt = conn.execute(
            "SELECT weekday, session_type, subject_id FROM timetable "
            "WHERE course_id = ? AND subject_id IS NOT NULL", (course_id,)
        ).fetchall()
    except Exception:
        tt = []
    if not tt:
        return []

    holidays = _holiday_dates(conn, course_id)

    # students in scope
    if student_db_id:
        students = [(student_db_id,)]
    else:
        students = conn.execute(
            "SELECT id FROM students WHERE course_id = ? "
            "AND status IN ('active','pending_registration')", (course_id,)
        ).fetchall()
    student_ids = [s[0] for s in students]
    if not student_ids:
        return []

    # expected slot-occurrences per subject
    expected = {s["id"]: 0 for s in subjects}
    for subj in subjects:
        s_start = _as_date(subj["start_date"]) or course_start
        s_end = _as_date(subj["end_date"]) or end
        s_end = min(s_end, end)
        slots_for = [(wd, st) for wd, st, sid in tt if sid == subj["id"]]
        if not slots_for:
            continue
        d = max(s_start, course_start)
        while d <= s_end:
            if d.weekday() != 6 and d not in holidays:
                expected[subj["id"]] += sum(1 for wd, _st in slots_for if wd == d.weekday())
            d += timedelta(days=1)

    placeholders = ",".join("?" for _ in student_ids)
    attended = {}
    try:
        rows = conn.execute(
            f"SELECT subject_id, COUNT(*) FROM attendance "
            f"WHERE subject_id IS NOT NULL AND student_id IN ({placeholders}) "
            f"AND date <= ? GROUP BY subject_id",
            [*student_ids, end.strftime("%Y-%m-%d")],
        ).fetchall()
        attended = {r[0]: r[1] for r in rows}
    except Exception:
        attended = {}

    n_students = len(student_ids)
    out = []
    for subj in subjects:
        exp = expected.get(subj["id"], 0) * (1 if student_db_id else n_students)
        att = attended.get(subj["id"], 0)
        rate = round(att / exp * 100, 1) if exp else 0.0
        out.append({
            "subject_id": subj["id"],
            "name": subj["name"],
            "code": subj["code"],
            "min_attendance": subj["min_attendance"],
            "expected": exp,
            "attended": att,
            "rate": rate,
            "at_risk": bool(exp) and rate < (subj["min_attendance"] or 75),
        })
    out.sort(key=lambda s: s["rate"])
    return out
