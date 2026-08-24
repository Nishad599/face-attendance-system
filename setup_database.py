#!/usr/bin/env python3
"""
Create the attendance database schema.

Works with both SQLite and PostgreSQL — backend is chosen automatically
via the DATABASE_URL environment variable (see db.py).
"""
import os
from datetime import datetime
from db import get_connection, is_postgres


def _serial():
    """Primary key clause for auto-incrementing IDs."""
    return "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _blob():
    """Binary column type."""
    return "BYTEA" if is_postgres() else "BLOB"


def setup_database():
    """Create attendance database with all required tables."""

    # SQLite: back up existing file before recreating
    if not is_postgres() and os.path.exists('attendance.db'):
        backup_name = f'attendance_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
        os.rename('attendance.db', backup_name)
        print(f"📦 Backed up existing database to {backup_name}")

    conn = get_connection()
    cursor = conn.cursor()

    SERIAL = _serial()
    BLOB = _blob()

    # Students table
    print("📋 Creating students table...")
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS students (
            id {SERIAL},
            student_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            face_encoding {BLOB},
            registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active',
            photo_count INTEGER DEFAULT 0,
            verification_score REAL DEFAULT 0.0
        )
    ''')

    # Attendance table
    print("📋 Creating attendance table...")
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS attendance (
            id {SERIAL},
            student_id INTEGER,
            date DATE,
            time_in TIME,
            status TEXT DEFAULT 'present',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    ''')

    # Face encodings table (for multiple photos per student)
    print("📋 Creating face_encodings table...")
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS face_encodings (
            id {SERIAL},
            student_id INTEGER,
            encoding_data {BLOB},
            photo_path TEXT,
            quality_score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    ''')

    # Registration sessions table
    print("📋 Creating registration_sessions table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS registration_sessions (
            session_id TEXT PRIMARY KEY,
            student_data TEXT,
            photos_uploaded INTEGER DEFAULT 0,
            status TEXT DEFAULT 'in_progress',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    ''')

    # Create indexes for better performance
    print("🔍 Creating indexes...")
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_student_id ON students(student_id)",
        "CREATE INDEX IF NOT EXISTS idx_student_email ON students(email)",
        "CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(date)",
        "CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id)",
    ]:
        cursor.execute(idx_sql)

    # Insert sample data for testing
    print("📝 Adding sample data...")
    if is_postgres():
        cursor.execute('''
            INSERT INTO students (student_id, name, email, status)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (student_id) DO NOTHING
        ''', ('TEST001', 'Test Student', 'test@example.com', 'active'))
    else:
        cursor.execute('''
            INSERT OR IGNORE INTO students (student_id, name, email, status)
            VALUES (?, ?, ?, ?)
        ''', ('TEST001', 'Test Student', 'test@example.com', 'active'))

    conn.commit()
    conn.close()

    print("✅ Database setup complete!")
    print("📊 Tables created: students, attendance, face_encodings, registration_sessions")
    print("🔍 Indexes created for better performance")
    print("📝 Sample test student added")


if __name__ == "__main__":
    setup_database()
