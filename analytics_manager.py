from datetime import datetime, timedelta
import pytz
from db import get_connection

# Analytics read from the primary `attendance` table (day-level: a student is
# "present" on a date if they have any attendance row that day). All methods
# accept an optional course_id to scope to a single batch.


class AnalyticsManager:
    def __init__(self, db_path='attendance.db'):
        self.db_path = db_path
        self.tz = pytz.timezone('Asia/Kolkata')

    # ---- helpers -------------------------------------------------------
    def _students(self, cursor, course_id=None):
        """Active + pending students, optionally scoped to a batch."""
        if course_id is not None:
            cursor.execute(
                "SELECT id, name, student_id, joining_date FROM students "
                "WHERE status IN ('active','pending_registration') AND course_id = ?",
                (course_id,),
            )
        else:
            cursor.execute(
                "SELECT id, name, student_id, joining_date FROM students "
                "WHERE status IN ('active','pending_registration')"
            )
        return cursor.fetchall()

    def _present_dates(self, cursor, sids, since):
        """Map student_id -> set of distinct present dates (>= since) from attendance."""
        result = {sid: set() for sid in sids}
        if not sids:
            return result
        ph = ",".join("?" * len(sids))
        cursor.execute(
            f"SELECT student_id, date FROM attendance WHERE student_id IN ({ph}) AND date >= ?",
            [*sids, since.strftime('%Y-%m-%d')],
        )
        for row in cursor.fetchall():
            sid, dt = row[0], row[1]
            try:
                d = datetime.strptime(str(dt)[:10], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                continue
            result.setdefault(sid, set()).add(d)
        return result

    def _holiday_dates(self, cursor, course_id=None):
        cursor.execute(
            "SELECT date FROM holidays WHERE course_id IS NULL OR course_id = ?",
            (course_id,),
        )
        out = set()
        for row in cursor.fetchall():
            try:
                out.add(datetime.strptime(str(row[0])[:10], '%Y-%m-%d').date())
            except (ValueError, TypeError):
                continue
        return out

    # ---- analytics -----------------------------------------------------
    def get_class_analytics(self, days=14, course_id=None):
        conn = get_connection(self.db_path)
        cur = conn.cursor()
        end_date = datetime.now(self.tz).date()
        start_date = end_date - timedelta(days=days)

        students = self._students(cur, course_id)
        sids = [s[0] for s in students]
        total_students = len(students)
        divisor = total_students or 1
        holidays = self._holiday_dates(cur, course_id)
        present_map = self._present_dates(cur, sids, start_date)

        # Attendance trend (daily present %)
        trend_data = []
        curr = start_date
        while curr <= end_date:
            if curr.weekday() != 6 and curr not in holidays:
                present = sum(1 for s in sids if curr in present_map.get(s, set()))
                trend_data.append({"date": curr.strftime('%Y-%m-%d'),
                                   "pct": round(present / divisor * 100, 1)})
            curr += timedelta(days=1)

        # Slot performance (last 30 days) from attendance.session_type
        slots = ['morning_1', 'morning_2', 'afternoon_1', 'afternoon_2']
        slot_stats = {}
        since30 = (end_date - timedelta(days=30)).strftime('%Y-%m-%d')
        for slot in slots:
            if sids:
                ph = ",".join("?" * len(sids))
                cur.execute(
                    f"SELECT COUNT(*) FROM attendance WHERE session_type = ? AND date >= ? "
                    f"AND student_id IN ({ph})", [slot, since30, *sids])
                slot_stats[slot] = cur.fetchone()[0] or 0
            else:
                slot_stats[slot] = 0

        # Leaderboard (present days / working days)
        leaderboard = []
        for s_id, name, _roll, join_date in students:
            try:
                s_join = datetime.strptime(str(join_date)[:10], '%Y-%m-%d').date() if join_date else start_date
            except (ValueError, TypeError):
                s_join = start_date
            calc_start = max(s_join, start_date)
            working_days = 0
            t = calc_start
            while t <= end_date:
                if t.weekday() != 6 and t not in holidays:
                    working_days += 1
                t += timedelta(days=1)
            attended = len([d for d in present_map.get(s_id, set()) if calc_start <= d <= end_date])
            pct = round(attended / working_days * 100, 1) if working_days > 0 else 0
            leaderboard.append({"name": name, "pct": pct})

        leaderboard.sort(key=lambda x: x['pct'], reverse=True)
        top_performers = leaderboard[:5]
        low_attendance = sorted([l for l in leaderboard if l['pct'] < 75], key=lambda x: x['pct'])[:5]

        peak_slot_id = max(slot_stats, key=slot_stats.get) if any(slot_stats.values()) else "--"
        peak_slot_name = peak_slot_id.replace('_', ' ').title() if peak_slot_id != "--" else "--"
        avg_class_pct = round(sum(t['pct'] for t in trend_data) / len(trend_data), 1) if trend_data else 0

        conn.close()
        return {
            "success": True,
            "avg_attendance": avg_class_pct,
            "total_students": total_students,
            "peak_slot": peak_slot_name,
            "trend": trend_data,
            "slot_performance": slot_stats,
            "top_performers": top_performers,
            "low_attendance": low_attendance,
        }

    def get_heatmap_data(self, days=90, course_id=None):
        conn = get_connection(self.db_path)
        cur = conn.cursor()
        end_date = datetime.now(self.tz).date()
        start_date = end_date - timedelta(days=days)
        students = self._students(cur, course_id)
        sids = [s[0] for s in students]
        divisor = len(students) or 1
        holidays = self._holiday_dates(cur, course_id)
        present_map = self._present_dates(cur, sids, start_date)

        heatmap = []
        curr = start_date
        while curr <= end_date:
            if curr.weekday() != 6 and curr not in holidays:
                present = sum(1 for s in sids if curr in present_map.get(s, set()))
                heatmap.append({"date": curr.strftime('%Y-%m-%d'),
                                "pct": round(present / divisor * 100, 1), "present": present})
            curr += timedelta(days=1)
        conn.close()
        return {"success": True, "heatmap": heatmap, "total_students": len(students)}

    def get_day_of_week_stats(self, days=60, course_id=None):
        conn = get_connection(self.db_path)
        cur = conn.cursor()
        end_date = datetime.now(self.tz).date()
        start_date = end_date - timedelta(days=days)
        students = self._students(cur, course_id)
        sids = [s[0] for s in students]
        divisor = len(students) or 1
        holidays = self._holiday_dates(cur, course_id)
        present_map = self._present_dates(cur, sids, start_date)

        day_totals = {i: [] for i in range(7)}
        curr = start_date
        while curr <= end_date:
            if curr.weekday() != 6 and curr not in holidays:
                present = sum(1 for s in sids if curr in present_map.get(s, set()))
                day_totals[curr.weekday()].append(round(present / divisor * 100, 1))
            curr += timedelta(days=1)

        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
        result = []
        for i, name in enumerate(day_names):
            vals = day_totals[i]
            result.append({"day": name, "avg_pct": round(sum(vals) / len(vals), 1) if vals else 0})
        conn.close()
        return {"success": True, "days": result}

    def get_at_risk_students(self, threshold=75, course_id=None):
        conn = get_connection(self.db_path)
        cur = conn.cursor()
        today = datetime.now(self.tz).date()
        start_date = today - timedelta(days=30)
        students = self._students(cur, course_id)
        sids = [s[0] for s in students]
        holidays = self._holiday_dates(cur, course_id)
        present_map = self._present_dates(cur, sids, start_date)

        at_risk = []
        for s_id, name, roll, join_date in students:
            try:
                s_join = datetime.strptime(str(join_date)[:10], '%Y-%m-%d').date() if join_date else start_date
            except (ValueError, TypeError):
                s_join = start_date
            calc_start = max(s_join, start_date)
            working_days = 0
            t = calc_start
            while t <= today:
                if t.weekday() != 6 and t not in holidays:
                    working_days += 1
                t += timedelta(days=1)
            pres = present_map.get(s_id, set())
            attended = len([d for d in pres if calc_start <= d <= today])
            pct = round(attended / working_days * 100, 1) if working_days > 0 else 0

            if pct < threshold:
                streak = 0
                check = today
                while check >= calc_start:
                    if check.weekday() != 6 and check not in holidays:
                        if check in pres:
                            break
                        streak += 1
                    check -= timedelta(days=1)
                at_risk.append({"name": name, "student_id": roll, "pct": pct, "streak": streak})

        at_risk.sort(key=lambda x: x['pct'])
        conn.close()
        return {"success": True, "at_risk": at_risk, "count": len(at_risk)}

    def get_student_sparkline(self, student_id, days=14, course_id=None):
        conn = get_connection(self.db_path)
        cur = conn.cursor()
        today = datetime.now(self.tz).date()
        start_date = today - timedelta(days=days)
        present_map = self._present_dates(cur, [student_id], start_date)
        pres = present_map.get(student_id, set())

        sparkline = []
        curr = start_date
        while curr <= today:
            if curr.weekday() != 6:
                sparkline.append({"date": curr.strftime('%Y-%m-%d'),
                                  "slots": 1 if curr in pres else 0})
            curr += timedelta(days=1)
        conn.close()
        return {"success": True, "sparkline": sparkline}
