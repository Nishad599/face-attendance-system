import sqlite3
conn = sqlite3.connect('attendance.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM slot_attendance ORDER BY id DESC LIMIT 5")
rows = cursor.fetchall()
for row in rows:
    print(row)
conn.close()
