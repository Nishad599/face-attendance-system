#!/bin/bash

# SQLite Database Debug Script
# This script will show schema and data for all tables

DB_FILE="attendance.db"

echo "🔍 SQLite Database Debug Report"
echo "================================"
echo "Database: $DB_FILE"
echo "Generated: $(date)"
echo ""

# Function to show table schema and data
debug_table() {
    local table_name=$1
    echo ""
    echo "📋 TABLE: $table_name"
    echo "----------------------------------------"
    
    # Show schema
    echo "🏗️  SCHEMA:"
    sqlite3 $DB_FILE ".schema $table_name"
    echo ""
    
    # Show row count
    row_count=$(sqlite3 $DB_FILE "SELECT COUNT(*) FROM $table_name;")
    echo "📊 ROW COUNT: $row_count"
    echo ""
    
    # Show sample data (limit 10 rows)
    if [ $row_count -gt 0 ]; then
        echo "📄 SAMPLE DATA (First 10 rows):"
        sqlite3 -header -column $DB_FILE "SELECT * FROM $table_name LIMIT 10;"
    else
        echo "📄 No data in this table"
    fi
    echo ""
    echo "=========================================="
}

# Check if database exists
if [ ! -f "$DB_FILE" ]; then
    echo "❌ Database file '$DB_FILE' not found!"
    exit 1
fi

# Show all tables
echo "📚 ALL TABLES:"
sqlite3 $DB_FILE ".tables"
echo ""

# Debug each table
debug_table "students"
debug_table "courses" 
debug_table "session_configs"
debug_table "session_attendance"
debug_table "attendance"
debug_table "holidays"
debug_table "session_windows"
debug_table "slot_attendance"
debug_table "daily_attendance_summary"
debug_table "course_settings"
debug_table "face_encodings"
debug_table "registration_sessions"

echo ""
echo "🎯 SPECIFIC QUERIES FOR DEBUGGING:"
echo "=================================="

echo ""
echo "👤 STUDENTS WITH JOINING DATES:"
sqlite3 -header -column $DB_FILE "SELECT id, name, student_id, email, joining_date, created_at FROM students WHERE status = 'active';"

echo ""
echo "📅 SESSION ATTENDANCE FOR RECENT DATES:"
sqlite3 -header -column $DB_FILE "SELECT sa.date, s.name, sa.session_type, sa.arrival_time, sa.is_manual 
FROM session_attendance sa 
JOIN students s ON sa.student_id = s.id 
WHERE sa.date >= date('now', '-30 days') 
ORDER BY sa.date DESC, s.name;"

echo ""
echo "🏫 ACTIVE COURSES:"
sqlite3 -header -column $DB_FILE "SELECT * FROM courses WHERE is_active = 1;"

echo ""
echo "⏰ SESSION CONFIGURATIONS:"
sqlite3 -header -column $DB_FILE "SELECT sc.*, c.name as course_name 
FROM session_configs sc 
JOIN courses c ON sc.course_id = c.id 
WHERE sc.is_active = 1;"

echo ""
echo "🏖️ HOLIDAYS:"
sqlite3 -header -column $DB_FILE "SELECT * FROM holidays ORDER BY date DESC LIMIT 10;"

echo ""
echo "📊 OLD ATTENDANCE TABLE:"
sqlite3 -header -column $DB_FILE "SELECT a.date, s.name, a.time_in, a.is_manual 
FROM attendance a 
JOIN students s ON a.student_id = s.id 
WHERE a.date >= date('now', '-30 days') 
ORDER BY a.date DESC;"

echo ""
echo "🔄 ATTENDANCE COMPARISON (Old vs New):"
echo "Old attendance table count:"
sqlite3 $DB_FILE "SELECT COUNT(*) as old_attendance_count FROM attendance;"
echo "New session_attendance table count:"
sqlite3 $DB_FILE "SELECT COUNT(*) as session_attendance_count FROM session_attendance;"

echo ""
echo "📈 STUDENT ATTENDANCE SUMMARY:"
sqlite3 -header -column $DB_FILE "
SELECT 
    s.name,
    s.student_id,
    s.joining_date,
    COUNT(DISTINCT sa.date) as days_with_attendance,
    COUNT(CASE WHEN sa.session_type = 'morning' THEN 1 END) as morning_sessions,
    COUNT(CASE WHEN sa.session_type = 'afternoon' THEN 1 END) as afternoon_sessions,
    COUNT(CASE WHEN sa.is_manual = 1 THEN 1 END) as manual_entries
FROM students s 
LEFT JOIN session_attendance sa ON s.id = sa.student_id 
WHERE s.status = 'active'
GROUP BY s.id, s.name, s.student_id, s.joining_date;"

echo ""
echo "🗓️ DAILY ATTENDANCE BREAKDOWN:"
sqlite3 -header -column $DB_FILE "
SELECT 
    sa.date,
    COUNT(DISTINCT sa.student_id) as total_students,
    COUNT(CASE WHEN sa.session_type = 'morning' THEN 1 END) as morning_attendance,
    COUNT(CASE WHEN sa.session_type = 'afternoon' THEN 1 END) as afternoon_attendance,
    COUNT(DISTINCT CASE WHEN sa.session_type = 'morning' THEN sa.student_id END) as unique_morning,
    COUNT(DISTINCT CASE WHEN sa.session_type = 'afternoon' THEN sa.student_id END) as unique_afternoon
FROM session_attendance sa 
WHERE sa.date >= date('now', '-14 days')
GROUP BY sa.date 
ORDER BY sa.date DESC;"

echo ""
echo "✅ Debug script completed!"
echo "Save this output and share with the developer."
