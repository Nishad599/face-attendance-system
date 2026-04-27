#!/bin/bash

echo "=== FACE ATTENDANCE SYSTEM DEBUG & FIX GUIDE ==="

echo "1. CHECK YOUR DATABASE CONTENT"
echo "Run this SQLite command to see your current data:"

sqlite3 attendance.db << 'EOF'
.headers on
.mode table

SELECT "=== STUDENTS TABLE ===" as info;
SELECT * FROM students;

SELECT "=== SLOT_ATTENDANCE TABLE ===" as info;
SELECT * FROM slot_attendance ORDER BY date DESC;

SELECT "=== TODAY'S ATTENDANCE ===" as info;
SELECT 
    s.name,
    s.student_id,
    sa.date,
    sa.slot_id,
    sa.time_marked
FROM students s 
LEFT JOIN slot_attendance sa ON s.id = sa.student_id 
WHERE sa.date = '2025-08-21'
ORDER BY sa.slot_id;

SELECT "=== RECENT ATTENDANCE (Last 7 days) ===" as info;
SELECT 
    s.name,
    sa.date,
    sa.slot_id,
    sa.time_marked
FROM students s 
JOIN slot_attendance sa ON s.id = sa.student_id 
WHERE sa.date >= date('now', '-7 days')
ORDER BY sa.date DESC, sa.slot_id;

.quit
EOF

echo ""
echo "2. CHECK IF YOU HAVE THE RIGHT API ENDPOINT"
echo "Search for this in your main_with_face_recognition.py file:"
echo "    @app.get(\"/api/attendance/student/{student_id}/slots\")"
echo ""
echo "If NOT found, add the missing endpoint code from the fix above."

echo ""
echo "3. TEST THE API DIRECTLY"
echo "Open your browser and go to:"
echo "    https://your-server:8000/api/attendance/student/1/slots"
echo ""
echo "This should return JSON data with your attendance."

echo ""
echo "4. FIX MISSING ATTENDANCE FOR 2025-08-21"
echo "If attendance for 2025-08-21 is missing, manually add it:"

sqlite3 attendance.db << 'EOF'
-- Check what we have for student ID 1 on 2025-08-21
SELECT * FROM slot_attendance WHERE student_id = 1 AND date = '2025-08-21';

-- If no records, add them manually (adjust time as needed)
INSERT OR IGNORE INTO slot_attendance 
(student_id, date, slot_id, time_marked, detection_confidence, is_manual, manual_reason)
VALUES 
(1, '2025-08-21', 'morning', '09:15:00', 0.85, 1, 'Manual entry for testing'),
(1, '2025-08-21', 'afternoon', '14:15:00', 0.85, 1, 'Manual entry for testing');

.quit
EOF

echo ""
echo "5. RESTART YOUR SERVER"
echo "After making code changes, restart your server:"
echo "    python main_with_face_recognition.py"

echo ""
echo "6. CLEAR BROWSER CACHE"
echo "Clear your browser cache or open in incognito mode"

echo ""
echo "7. CHECK CONSOLE LOGS"
echo "Open browser Developer Tools (F12) and check for JavaScript errors"

echo ""
echo "8. VERIFY CALENDAR WORKS"
echo "Select your student from dropdown and check if calendar shows correct data"

echo ""
echo "=== COMMON ISSUES & SOLUTIONS ==="

echo ""
echo "ISSUE: Calendar shows 'absent' for all days"
echo "SOLUTION: Check browser console for JavaScript errors"
echo "          Verify API endpoint returns correct data"

echo ""
echo "ISSUE: Missing attendance for specific date"
echo "SOLUTION: Check if you were in the correct time slot when marking"
echo "          Use manual attendance marking feature"

echo ""
echo "ISSUE: Database has data but calendar doesn't show it"
echo "SOLUTION: Check date format consistency (YYYY-MM-DD)"
echo "          Verify API endpoint is working correctly"

echo ""
echo "=== QUICK TEST COMMANDS ==="

echo ""
echo "Test API endpoint with curl:"
echo "curl -k https://localhost:8000/api/attendance/student/1/slots"

echo ""
echo "Check logs in real-time:"
echo "tail -f logs/*.log"

echo ""
echo "=== END OF DEBUG GUIDE ==="
