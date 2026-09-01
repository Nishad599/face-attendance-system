"""
import_calendar.py — load a batch's academic calendar from CSV.

Written as a reusable importer rather than a one-off script: PGCP-AI needs the
same treatment, and so will every future intake.

    python import_calendar.py --batch 2 --file data/calendar_pgcp_bda_aug2026.csv --dry-run
    python import_calendar.py --batch 2 --file data/calendar_pgcp_bda_aug2026.csv

CSV columns (header row required):

    type        module | event | holiday
    seq         module number as printed in the schedule (modules only)
    title       module or event name
    start_date  YYYY-MM-DD
    end_date    YYYY-MM-DD (blank = same as start)
    hours       free text, e.g. "120 hrs (36+44+40)"
    faculty     free text
    days        teaching days as printed
    exam_date      YYYY-MM-DD (modules only)
    mid_quiz_date  YYYY-MM-DD (modules only, optional)
    coordinator    free text

Re-running updates existing rows rather than duplicating them, so a corrected
calendar can simply be re-imported.

Only `module` rows become subjects, which is what attendance can be attributed
to. Events (picnics, exams, revision) never become attendance modules.
Holidays go to the `holidays` table, so they are excluded from working days
and attendance cannot be marked on them.
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta

from db import get_connection


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def load_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def batch_info(conn, course_id):
    row = conn.execute(
        "SELECT name, start_date, end_date FROM courses WHERE id = ?", (course_id,)
    ).fetchone()
    return row


def upsert_module(conn, course_id, r, dry):
    title = (r.get("title") or "").strip()
    start = parse_date(r.get("start_date"))
    end = parse_date(r.get("end_date")) or start
    exam = parse_date(r.get("exam_date"))
    mid = parse_date(r.get("mid_quiz_date"))
    seq = (r.get("seq") or "").strip()
    days = (r.get("days") or "").strip()

    if not title or not start:
        return "skip", f"missing title or start date: {r}"

    existing = conn.execute(
        "SELECT id FROM subjects WHERE course_id = ? AND name = ?", (course_id, title)
    ).fetchone()

    if dry:
        return ("update" if existing else "insert"), title

    params = (
        int(seq) if seq.isdigit() else None,
        (r.get("faculty") or "").strip() or None,
        (r.get("coordinator") or "").strip() or None,
        (r.get("hours") or "").strip() or None,
        int(days) if days.isdigit() else None,
        exam.strftime("%Y-%m-%d") if exam else None,
        mid.strftime("%Y-%m-%d") if mid else None,
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    )
    if existing:
        conn.execute(
            "UPDATE subjects SET sequence=?, faculty=?, coordinator=?, hours=?, "
            "teaching_days=?, exam_date=?, mid_quiz_date=?, start_date=?, end_date=?, "
            "is_active=1 WHERE id=?", (*params, existing[0]))
        return "update", title
    conn.execute(
        "INSERT INTO subjects (course_id, name, min_attendance, sequence, faculty, "
        "coordinator, hours, teaching_days, exam_date, mid_quiz_date, start_date, "
        "end_date, is_active) VALUES (?, ?, 75, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (course_id, title, *params))
    return "insert", title


EVENT_KINDS = (
    ("exam", ("exam", "ccee", "cmce", "quiz")),
    ("revision", ("revision",)),
    ("interview", ("interview", "mock")),
    ("placement", ("placement", "ccpp")),
    ("induction", ("inauguration", "induction", "convocation", "farewell")),
    ("activity", ("picnic", "sports", "team building", "team buiding")),
)


def classify(title):
    """Group events so the calendar can colour-code them."""
    low = title.lower()
    for kind, needles in EVENT_KINDS:
        if any(n in low for n in needles):
            return kind
    return "event"


def upsert_event(conn, course_id, r, dry):
    title = (r.get("title") or "").strip()
    start = parse_date(r.get("start_date"))
    end = parse_date(r.get("end_date")) or start
    if not title or not start:
        return "skip", f"missing title or start date: {r}"

    if dry:
        return "insert", title

    kind = classify(title)
    coordinator = (r.get("coordinator") or "").strip() or None
    notes = (r.get("hours") or "").strip() or None
    # The unique index is (course_id, title, start_date), so a repeated import
    # updates rather than duplicating.
    existing = conn.execute(
        "SELECT id FROM academic_events WHERE course_id=? AND title=? AND start_date=?",
        (course_id, title, start.strftime("%Y-%m-%d")),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE academic_events SET end_date=?, kind=?, notes=?, coordinator=? WHERE id=?",
            (end.strftime("%Y-%m-%d"), kind, notes, coordinator, existing[0]))
        return "update", title
    conn.execute(
        "INSERT INTO academic_events (course_id, title, kind, start_date, end_date, "
        "notes, coordinator) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (course_id, title, kind, start.strftime("%Y-%m-%d"),
         end.strftime("%Y-%m-%d"), notes, coordinator))
    return "insert", title


def upsert_holiday(conn, course_id, r, dry):
    """Holidays span a range in the CSV but the holidays table is one row per
    date, so expand them."""
    title = (r.get("title") or "").strip()
    start = parse_date(r.get("start_date"))
    end = parse_date(r.get("end_date")) or start
    if not title or not start:
        return "skip", f"missing title or start date: {r}"

    n = 0
    d = start
    while d <= end:
        ds = d.strftime("%Y-%m-%d")
        if not dry:
            # `type` is NOT NULL in the real schema — omitting it crashed the
            # first production import.
            existing = conn.execute(
                "SELECT id FROM holidays WHERE date = ? AND "
                "(course_id = ? OR course_id IS NULL)", (ds, course_id)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE holidays SET name = ?, type = 'holiday', course_id = ? WHERE id = ?",
                    (title, course_id, existing[0]))
            else:
                conn.execute(
                    "INSERT INTO holidays (date, name, type, course_id) VALUES (?, ?, 'holiday', ?)",
                    (ds, title, course_id))
        n += 1
        d += timedelta(days=1)
    return "insert", f"{title} ({n} day{'s' if n > 1 else ''})"


def check_clashes(conn, course_id):
    """Warn about dates that contradict each other.

    An exam scheduled on a holiday is the sort of thing nobody notices until
    the morning of the exam.
    """
    warnings = []
    holidays = {}
    try:
        for d, name in conn.execute(
            "SELECT date, name FROM holidays WHERE course_id IS NULL OR course_id = ?",
            (course_id,),
        ).fetchall():
            holidays[str(d)[:10]] = name
    except Exception:
        return warnings

    # Exams and mid-quizzes both matter; a picnic on a Sunday is fine, an exam
    # on one is not.
    for name, start, exam, mid in conn.execute(
        "SELECT name, start_date, exam_date, mid_quiz_date FROM subjects "
        "WHERE course_id = ?", (course_id,),
    ).fetchall():
        for label, value in (("exam", exam), ("mid quiz", mid)):
            if not value:
                continue
            key = str(value)[:10]
            if key in holidays:
                warnings.append(
                    f"'{name}' {label} on {key} falls on a holiday ({holidays[key]})")
            d = parse_date(key)
            if d and d.weekday() == 6:
                warnings.append(f"'{name}' {label} on {key} falls on a Sunday")
            # A quiz dated before its own module begins is a transcription slip.
            s_d = parse_date(str(start)[:10]) if start else None
            if d and s_d and d < s_d:
                warnings.append(
                    f"'{name}' {label} on {key} is BEFORE the module starts ({s_d})")

    # Events on a Sunday are deliberate here (mock interviews, picnics), so
    # only a clash with a declared holiday is worth reporting.
    for title, start in conn.execute(
        "SELECT title, start_date FROM academic_events WHERE course_id = ?", (course_id,)
    ).fetchall():
        key = str(start)[:10]
        if key in holidays:
            warnings.append(f"'{title}' on {key} falls on a holiday ({holidays[key]})")
    return warnings


def main():
    ap = argparse.ArgumentParser(description="Import a batch academic calendar from CSV")
    ap.add_argument("--batch", type=int, required=True, help="course/batch id")
    ap.add_argument("--file", required=True, help="path to the CSV")
    ap.add_argument("--dry-run", action="store_true", help="show what would change")
    args = ap.parse_args()

    try:
        rows = load_rows(args.file)
    except FileNotFoundError:
        print(f"[ERROR] No such file: {args.file}")
        sys.exit(2)

    conn = get_connection()
    info = batch_info(conn, args.batch)
    if not info:
        print(f"[ERROR] No batch with id {args.batch}. Run with a valid --batch.")
        sys.exit(2)

    print(f"=== Calendar import for '{info[0]}' (batch {args.batch}) ===")
    if args.dry_run:
        print("(dry run — nothing will be written)\n")

    counts = {"module": 0, "event": 0, "holiday": 0, "skip": 0}
    handlers = {"module": upsert_module, "event": upsert_event, "holiday": upsert_holiday}

    for r in rows:
        kind = (r.get("type") or "").strip().lower()
        handler = handlers.get(kind)
        if not handler:
            print(f"  [skip] unknown type '{kind}': {r.get('title')}")
            counts["skip"] += 1
            continue
        action, label = handler(conn, args.batch, r, args.dry_run)
        if action == "skip":
            print(f"  [skip] {label}")
            counts["skip"] += 1
        else:
            counts[kind] += 1
            print(f"  [{action}] {kind}: {label}")

    if not args.dry_run:
        conn.commit()

        # The course window must cover the calendar, or figures computed from
        # it will silently stop at the old end date.
        bounds = conn.execute(
            "SELECT MIN(start_date), MAX(COALESCE(end_date, start_date)) FROM ("
            "  SELECT start_date, end_date FROM subjects WHERE course_id = ?"
            "  UNION ALL"
            "  SELECT start_date, end_date FROM academic_events WHERE course_id = ?"
            ")", (args.batch, args.batch)).fetchone()
        if bounds and bounds[0] and bounds[1]:
            cur_start, cur_end = str(info[1])[:10], str(info[2])[:10]
            new_start = min(cur_start, str(bounds[0])[:10]) if cur_start else str(bounds[0])[:10]
            new_end = max(cur_end, str(bounds[1])[:10]) if cur_end else str(bounds[1])[:10]
            if (new_start, new_end) != (cur_start, cur_end):
                conn.execute("UPDATE courses SET start_date = ?, end_date = ? WHERE id = ?",
                             (new_start, new_end, args.batch))
                conn.commit()
                print(f"\n[OK] Batch window widened to {new_start} … {new_end}")

    print(f"\n=== {counts['module']} modules, {counts['event']} events, "
          f"{counts['holiday']} holidays, {counts['skip']} skipped ===")

    warnings = check_clashes(conn, args.batch)
    if warnings:
        print("\n[!] Check these — the source calendar contradicts itself:")
        for w in warnings:
            print(f"    - {w}")

    conn.close()
    if args.dry_run:
        print("\n(dry run — nothing was written)")


if __name__ == "__main__":
    main()
