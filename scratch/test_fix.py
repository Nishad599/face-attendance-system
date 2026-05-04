import sqlite3
import pytz
from datetime import datetime

def test_manual_mark():
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()
    
    student_id = 2 # Assuming student 2 exists
    date_str = '2026-05-04'
    session_type = 'full_day'
    reason = 'Test manual mark'
    
    # Simulate the logic
    timezone = pytz.timezone('Asia/Kolkata')
    now = datetime.now(timezone)
    current_time = now.strftime('%H:%M:%S')
    current_timestamp = now.strftime('%Y-%m-%d %H:%M:%S')

    slots_to_mark = ['morning_1', 'morning_2', 'afternoon_1', 'afternoon_2']
    
    for slot in slots_to_mark:
        cursor.execute('''
            INSERT OR IGNORE INTO slot_attendance 
            (student_id, date, slot_id, time_marked, is_manual, manual_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (student_id, date_str, slot, current_time, True, reason, current_timestamp))
    
    conn.commit()
    print("Marked full day for student 2 on 2026-05-04")
    
    # Now check if it reflects in the "Today's Attendance" style query
    cursor.execute('''
        SELECT s.name, sa_m1.slot_id, sa_m1.created_at
        FROM students s
        LEFT JOIN slot_attendance sa_m1 ON s.id = sa_m1.student_id 
            AND sa_m1.date = ? AND sa_m1.slot_id = 'morning_1'
        WHERE s.id = ?
    ''', (date_str, student_id))
    
    result = cursor.fetchone()
    print(f"Query Result: {result}")
    
    conn.close()

if __name__ == "__main__":
    test_manual_mark()
