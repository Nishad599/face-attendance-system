import os
import shutil
import re
import json
import sqlite3
from datetime import datetime

def create_student_photo_directory(student_id, student_name):
    """Create a directory for student's photos"""
    # Clean student name for folder name (remove special characters)
    clean_name = re.sub(r'[^\w\s-]', '', student_name).strip()
    clean_name = re.sub(r'[-\s]+', '_', clean_name)
    
    # Create directory name: StudentID_StudentName
    dir_name = f"{student_id}_{clean_name}"
    student_dir = os.path.join('student_photos', dir_name)
    
    # Create directory if it doesn't exist
    os.makedirs(student_dir, exist_ok=True)
    
    return student_dir

def get_student_photo_path(student_id, student_name, session_id, timestamp):
    """Get the full path for a student's photo"""
    student_dir = create_student_photo_directory(student_id, student_name)
    
    # Create filename: photo_1.jpg, photo_2.jpg, etc.
    existing_files = [f for f in os.listdir(student_dir) if f.startswith('photo_') and f.endswith('.jpg')]
    photo_number = len(existing_files) + 1
    
    filename = f"photo_{photo_number}_{timestamp}.jpg"
    return os.path.join(student_dir, filename)

def organize_existing_photos():
    """Organize existing photos into student directories"""
    if not os.path.exists('student_photos'):
        print("No student_photos directory found")
        return
    
    # Get all students from database
    conn = sqlite3.connect('attendance.db')
    cursor = conn.cursor()
    cursor.execute('SELECT student_id, name FROM students WHERE status = "active"')
    students = cursor.fetchall()
    conn.close()
    
    if not students:
        print("No students found in database")
        return
    
    moved_count = 0
    # Move existing photos to student directories
    for file in os.listdir('student_photos'):
        file_path = os.path.join('student_photos', file)
        if file.endswith('.jpg') and os.path.isfile(file_path):
            # Try to match file to student (if filename contains session info)
            moved = False
            for student_id, student_name in students:
                if student_id in file or student_name.lower().replace(' ', '_') in file.lower():
                    student_dir = create_student_photo_directory(student_id, student_name)
                    new_path = os.path.join(student_dir, f"existing_{file}")
                    
                    try:
                        shutil.move(file_path, new_path)
                        print(f"📂 Moved {file} to {student_dir}")
                        moved_count += 1
                        moved = True
                        break
                    except Exception as e:
                        print(f"❌ Error moving {file}: {e}")
            
            if not moved:
                # Move to 'unknown' directory
                unknown_dir = os.path.join('student_photos', 'unknown')
                os.makedirs(unknown_dir, exist_ok=True)
                new_path = os.path.join(unknown_dir, file)
                try:
                    shutil.move(file_path, new_path)
                    print(f"📂 Moved {file} to unknown directory")
                    moved_count += 1
                except Exception as e:
                    print(f"❌ Error moving {file}: {e}")
    
    print(f"✅ Organized {moved_count} photos")
