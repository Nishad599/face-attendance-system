import sqlite3
from datetime import datetime, timedelta
import pytz

class AnalyticsManager:
    def __init__(self, db_path='attendance.db'):
        self.db_path = db_path

    def get_class_analytics(self, days=14):
        """Fetches comprehensive class analytics for the last N days"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        timezone = pytz.timezone('Asia/Kolkata')
        end_date = datetime.now(timezone).date()
        start_date = end_date - timedelta(days=days)

        # 1. Total Active Students
        cursor.execute("SELECT COUNT(*) FROM students WHERE status = 'active'")
        total_students = cursor.fetchone()[0] or 1 # Avoid division by zero

        # 2. Attendance Trend
        trend_data = []
        curr = start_date
        while curr <= end_date:
            date_str = curr.strftime('%Y-%m-%d')
            # Only count if not Sunday
            if curr.weekday() != 6:
                # Count unique students present in ANY slot on this day
                cursor.execute("SELECT COUNT(DISTINCT student_id) FROM slot_attendance WHERE date = ?", (date_str,))
                present_count = cursor.fetchone()[0] or 0
                percentage = (present_count / total_students * 100)
                trend_data.append({"date": date_str, "pct": round(percentage, 1)})
            curr += timedelta(days=1)

        # 3. Slot Performance (Last 30 days)
        slots = ['morning_1', 'morning_2', 'afternoon_1', 'afternoon_2']
        slot_stats = {}
        for slot in slots:
            cursor.execute("SELECT COUNT(*) FROM slot_attendance WHERE slot_id = ? AND date >= ?", 
                          (slot, (end_date - timedelta(days=30)).strftime('%Y-%m-%d')))
            slot_stats[slot] = cursor.fetchone()[0] or 0

        # 4. Leaderboard (Overall)
        # Fetch all student attendance counts vs working days
        leaderboard = []
        cursor.execute("SELECT id, name, joining_date FROM students WHERE status = 'active'")
        students = cursor.fetchall()

        for s_id, name, join_date in students:
            # Calculate working days for this student
            s_join = datetime.strptime(join_date, '%Y-%m-%d').date() if join_date else start_date
            calc_start = max(s_join, start_date)
            
            working_days = 0
            t = calc_start
            while t <= end_date:
                if t.weekday() != 6: working_days += 1
                t += timedelta(days=1)
            
            expected_slots = working_days * 4
            cursor.execute("SELECT COUNT(*) FROM slot_attendance WHERE student_id = ? AND date >= ?", 
                          (s_id, calc_start.strftime('%Y-%m-%d')))
            attended = cursor.fetchone()[0] or 0
            
            pct = (attended / expected_slots * 100) if expected_slots > 0 else 0
            leaderboard.append({"name": name, "pct": round(pct, 1)})

        leaderboard.sort(key=lambda x: x['pct'], reverse=True)
        top_performers = leaderboard[:5]
        low_attendance = [l for l in leaderboard if l['pct'] < 75][-5:] # Last 5 with < 75%
        low_attendance.sort(key=lambda x: x['pct'])

        # 5. Peak Slot
        peak_slot_id = max(slot_stats, key=slot_stats.get) if any(slot_stats.values()) else "--"
        peak_slot_name = peak_slot_id.replace('_', ' ').title()

        # 6. Avg Class Attendance
        avg_class_pct = sum([t['pct'] for t in trend_data]) / len(trend_data) if trend_data else 0

        conn.close()
        return {
            "success": True,
            "avg_attendance": round(avg_class_pct, 1),
            "total_students": total_students,
            "peak_slot": peak_slot_name,
            "trend": trend_data,
            "slot_performance": slot_stats,
            "top_performers": top_performers,
            "low_attendance": low_attendance
        }

    def get_heatmap_data(self, days=90):
        """Returns per-day attendance percentage for the last N days (for calendar heatmap)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        timezone = pytz.timezone('Asia/Kolkata')
        end_date = datetime.now(timezone).date()
        start_date = end_date - timedelta(days=days)

        cursor.execute("SELECT COUNT(*) FROM students WHERE status = 'active'")
        total_students = cursor.fetchone()[0] or 1

        heatmap = []
        curr = start_date
        while curr <= end_date:
            if curr.weekday() != 6:  # Skip Sundays
                date_str = curr.strftime('%Y-%m-%d')
                cursor.execute(
                    "SELECT COUNT(DISTINCT student_id) FROM slot_attendance WHERE date = ?",
                    (date_str,)
                )
                present = cursor.fetchone()[0] or 0
                pct = round(present / total_students * 100, 1)
                heatmap.append({"date": date_str, "pct": pct, "present": present})
            curr += timedelta(days=1)

        conn.close()
        return {"success": True, "heatmap": heatmap, "total_students": total_students}

    def get_day_of_week_stats(self, days=60):
        """Returns average attendance % per weekday (Mon=0 … Sat=5) over last N days."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        timezone = pytz.timezone('Asia/Kolkata')
        end_date = datetime.now(timezone).date()
        start_date = end_date - timedelta(days=days)

        cursor.execute("SELECT COUNT(*) FROM students WHERE status = 'active'")
        total_students = cursor.fetchone()[0] or 1

        day_totals = {i: [] for i in range(7)}  # 0=Mon … 6=Sun
        curr = start_date
        while curr <= end_date:
            if curr.weekday() != 6:
                date_str = curr.strftime('%Y-%m-%d')
                cursor.execute(
                    "SELECT COUNT(DISTINCT student_id) FROM slot_attendance WHERE date = ?",
                    (date_str,)
                )
                present = cursor.fetchone()[0] or 0
                pct = round(present / total_students * 100, 1)
                day_totals[curr.weekday()].append(pct)
            curr += timedelta(days=1)

        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
        result = []
        for i, name in enumerate(day_names):
            vals = day_totals[i]
            avg = round(sum(vals) / len(vals), 1) if vals else 0
            result.append({"day": name, "avg_pct": avg})

        conn.close()
        return {"success": True, "days": result}

    def get_at_risk_students(self, threshold=75):
        """Returns all students below attendance threshold with pct and consecutive absence streak."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        timezone = pytz.timezone('Asia/Kolkata')
        today = datetime.now(timezone).date()
        start_date = today - timedelta(days=30)

        cursor.execute("SELECT id, name, student_id, joining_date FROM students WHERE status = 'active'")
        students = cursor.fetchall()

        at_risk = []
        for s_id, name, student_id_str, join_date in students:
            s_join = datetime.strptime(join_date, '%Y-%m-%d').date() if join_date else start_date
            calc_start = max(s_join, start_date)

            working_days = 0
            t = calc_start
            while t <= today:
                if t.weekday() != 6:
                    working_days += 1
                t += timedelta(days=1)

            expected_slots = working_days * 4
            cursor.execute(
                "SELECT COUNT(*) FROM slot_attendance WHERE student_id = ? AND date >= ?",
                (s_id, calc_start.strftime('%Y-%m-%d'))
            )
            attended = cursor.fetchone()[0] or 0
            pct = round(attended / expected_slots * 100, 1) if expected_slots > 0 else 0

            if pct < threshold:
                # Calculate consecutive absence streak
                streak = 0
                check_date = today
                while check_date >= calc_start:
                    if check_date.weekday() != 6:
                        date_str = check_date.strftime('%Y-%m-%d')
                        cursor.execute(
                            "SELECT COUNT(*) FROM slot_attendance WHERE student_id = ? AND date = ?",
                            (s_id, date_str)
                        )
                        if cursor.fetchone()[0] == 0:
                            streak += 1
                        else:
                            break
                    check_date -= timedelta(days=1)

                at_risk.append({
                    "name": name,
                    "student_id": student_id_str,
                    "pct": pct,
                    "streak": streak
                })

        at_risk.sort(key=lambda x: x['pct'])
        conn.close()
        return {"success": True, "at_risk": at_risk, "count": len(at_risk)}

    def get_student_sparkline(self, student_id, days=14):
        """Returns per-day slot count for a student over last N days (for sparkline chart)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        timezone = pytz.timezone('Asia/Kolkata')
        today = datetime.now(timezone).date()
        start_date = today - timedelta(days=days)

        sparkline = []
        curr = start_date
        while curr <= today:
            if curr.weekday() != 6:
                date_str = curr.strftime('%Y-%m-%d')
                cursor.execute(
                    "SELECT COUNT(*) FROM slot_attendance WHERE student_id = ? AND date = ?",
                    (student_id, date_str)
                )
                count = cursor.fetchone()[0] or 0
                sparkline.append({"date": date_str, "slots": count})
            curr += timedelta(days=1)

        conn.close()
        return {"success": True, "sparkline": sparkline}
