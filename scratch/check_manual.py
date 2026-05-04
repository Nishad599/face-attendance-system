import sqlite3
conn = sqlite3.connect('attendance.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM slot_attendance WHERE is_manual = 1")
rows = cursor.fetchall()
if not rows:
    print("No manual records found")
for row in rows:
    print(row)
conn.close()
