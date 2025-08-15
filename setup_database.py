#!/usr/bin/env python3
import sqlite3
import os
from datetime import datetime

def setup_database():
    """Create attendance database with all required tables"""
    
    # Remove existing database if exists
    if os.path.exists('attendance.db'):
        backup_name = f'attendance_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
        os.rename('attendance.db', backup_name)
        print(f"📦 Backed up existing database to {backup_name}")
    
    # Create new database
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()
    
    # Students table
    print("📋 Creating students table...")
    cursor.execute('''
        CREATE TABLE students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            face_encoding BLOB,
            registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active',
            photo_count INTEGER DEFAULT 0,
            verification_score REAL DEFAULT 0.0
        )
    ''')
    
    # Attendance table
    print("📋 Creating attendance table...")
    cursor.execute('''
        CREATE TABLE attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute('''
        CREATE TABLE face_encodings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            encoding_data BLOB,
            photo_path TEXT,
            quality_score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    ''')
    
    # Registration sessions table
    print("📋 Creating registration_sessions table...")
    cursor.execute('''
        CREATE TABLE registration_sessions (
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
    cursor.execute('CREATE INDEX idx_student_id ON students(student_id)')
    cursor.execute('CREATE INDEX idx_student_email ON students(email)')
    cursor.execute('CREATE INDEX idx_attendance_date ON attendance(date)')
    cursor.execute('CREATE INDEX idx_attendance_student ON attendance(student_id)')
    
    # Insert sample data for testing
    print("📝 Adding sample data...")
    cursor.execute('''
        INSERT INTO students (student_id, name, email, status) 
        VALUES ('TEST001', 'Test Student', 'test@example.com', 'active')
    ''')
    
    conn.commit()
    conn.close()
    
    print("✅ Database setup complete!")
    print("📊 Tables created: students, attendance, face_encodings, registration_sessions")
    print("🔍 Indexes created for better performance")
    print("📝 Sample test student added")

if __name__ == "__main__":
    setup_database()
