import sqlite3
from datetime import datetime, timedelta

def bulk_mark():
    db_path = 'attendance.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Set Joining Date for everyone
    print("📍 Setting universal joining date to 2026-02-25...")
    cursor.execute("UPDATE students SET joining_date = '2026-02-25'")
    
    # 2. Define target dates to mark 100% present
    specific_dates = ['2026-04-25', '2026-04-17', '2026-03-09']
    
    # Date Range: 25 Feb to 04 March
    start_date = datetime(2026, 2, 25)
    end_date = datetime(2026, 3, 4)
    range_dates = []
    curr = start_date
    while curr <= end_date:
        # Avoid Sundays
        if curr.weekday() != 6:
            range_dates.append(curr.strftime('%Y-%m-%d'))
        curr += timedelta(days=1)
        
    all_target_dates = list(set(specific_dates + range_dates))
    
    # 3. Get all active students
    cursor.execute("SELECT id FROM students WHERE status = 'active'")
    student_ids = [row[0] for row in cursor.fetchall()]
    
    print(f"👥 Found {len(student_ids)} students to process for {len(all_target_dates)} dates.")
    
    # 4. Mark attendance for all 4 slots
    slots = ['morning_1', 'morning_2', 'afternoon_1', 'afternoon_2']
    attendance_count = 0
    
    for date_str in all_target_dates:
        print(f"📅 Processing {date_str}...")
        for student_id in student_ids:
            for slot in slots:
                # Insert if not exists
                cursor.execute('''
                    INSERT OR IGNORE INTO slot_attendance 
                    (student_id, date, slot_id, time_marked, is_manual, manual_reason)
                    VALUES (?, ?, ?, ?, 1, 'Bulk marked by admin')
                ''', (student_id, date_str, slot, '09:00:00'))
                if cursor.rowcount > 0:
                    attendance_count += 1
                    
    conn.commit()
    print(f"🎉 Successfully inserted {attendance_count} attendance records!")
    print("✅ All students are now marked present for the requested dates.")

if __name__ == "__main__":
    bulk_mark()
