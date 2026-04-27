import sqlite3
import pytz
from datetime import datetime

def migrate_database(db_path='attendance.db'):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("🚀 Starting database migration from 2 slots to 4 slots...")
    
    # 1. Update session configurations if needed
    print("🔄 Deleting old 2-slot defaults and ensuring 4-slot config...")
    cursor.execute("DELETE FROM session_configs")
    
    cursor.execute('SELECT id FROM courses WHERE is_active = 1 LIMIT 1')
    course_row = cursor.fetchone()
    
    course_id = course_row[0] if course_row else 1
    
    cursor.execute('''
        INSERT INTO session_configs (course_id, session_type, start_time, end_time, is_active)
        VALUES 
        (?, 'morning_1', '08:30:00', '09:30:00', 1),
        (?, 'morning_2', '11:00:00', '11:15:00', 1),
        (?, 'afternoon_1', '13:45:00', '14:00:00', 1),
        (?, 'afternoon_2', '16:15:00', '16:45:00', 1)
    ''', (course_id, course_id, course_id, course_id))
    
    # 2. Migrate slot_attendance
    print("🔄 Migrating historical slot_attendance records...")
    
    # Fetch old records
    cursor.execute("SELECT id, student_id, date, slot_id, time_marked, detection_confidence, is_manual, manual_reason, created_at FROM slot_attendance WHERE slot_id IN ('morning', 'afternoon')")
    old_records = cursor.fetchall()
    
    print(f"📦 Found {len(old_records)} old records to migrate.")
    
    new_records = []
    ids_to_delete = []
    
    for row in old_records:
        rec_id, student_id, date, slot_id, time_marked, confidence, is_manual, manual_reason, created_at = row
        ids_to_delete.append(rec_id)
        
        if slot_id == 'morning':
            new_records.append((student_id, date, 'morning_1', time_marked, confidence, is_manual, manual_reason, created_at))
            new_records.append((student_id, date, 'morning_2', time_marked, confidence, is_manual, manual_reason, created_at))
        elif slot_id == 'afternoon':
            new_records.append((student_id, date, 'afternoon_1', time_marked, confidence, is_manual, manual_reason, created_at))
            new_records.append((student_id, date, 'afternoon_2', time_marked, confidence, is_manual, manual_reason, created_at))
    
    # Insert new records
    for nr in new_records:
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO slot_attendance 
                (student_id, date, slot_id, time_marked, detection_confidence, is_manual, manual_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', nr)
        except sqlite3.Error as e:
            print(f"⚠️ Error migrating record: {e}")
            
    # Delete old records
    if ids_to_delete:
        id_str = ",".join(map(str, ids_to_delete))
        cursor.execute(f"DELETE FROM slot_attendance WHERE id IN ({id_str})")
        print(f"✅ Successfully converted {len(old_records)} old records into {len(new_records)} new 4-slot records.")
    
    conn.commit()
    print("🎉 Database migration complete!")

if __name__ == '__main__':
    migrate_database()
