from fastapi import FastAPI, HTTPException, Request, Body, Depends, Cookie, Response
from typing import Optional, Dict, Any, List
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr
import sys
from fastapi.responses import HTMLResponse
from photo_utils import create_student_photo_directory, get_student_photo_path
import os
import sqlite3
import base64
import json
import uuid
from datetime import datetime, timedelta
from datetime import time
from typing import Optional, List
from enum import Enum
import numpy as np
from PIL import Image
import io
from camera_manager import camera_manager
from asian_face_model import asian_face_recognizer
from fastapi import Body
from fastapi.staticfiles import StaticFiles  # Add this import
import secrets
import hashlib
from phase1_integration import enhance_existing_attendance_system, add_phase1_api_endpoints
from attendance_manager import create_slot_manager_instance
import pytz

# Convert to a specific timezone (e.g., Asia/Kolkata)
timezone = pytz.timezone('Asia/Kolkata')
localized_time = timezone.localize(datetime(2025, 8, 1))


# Add system path for OpenCV
sys.path.insert(0, '/usr/lib/python3/dist-packages')

# Import libraries with fallbacks
try:
    import cv2
    OPENCV_AVAILABLE = True
    print("✅ OpenCV available")
except ImportError:
    OPENCV_AVAILABLE = False
    print("❌ OpenCV not available")

try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
    print("✅ Face recognition available")
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False
    print("❌ Face recognition not available - using basic mode")

class ManualAttendance(BaseModel):
    student_id: int
    date: str
    reason: Optional[str] = None

class Holiday(BaseModel):
    date: str
    name: str
    type: str

class SessionType(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"

class CourseCreate(BaseModel):
    name: str
    start_date: str
    end_date: str
    description: Optional[str] = None

class SessionConfig(BaseModel):
    session_type: str
    start_time: str
    end_time: str

class SessionAttendance(BaseModel):
    student_id: int
    session_type: str
    arrival_time: Optional[str] = None

app = FastAPI(title="Face Recognition Attendance System", version="2.0.0")

# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request, call_next):
    """Add security headers to all responses"""
    response = await call_next(request)
    
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    
    # Cache control for sensitive pages
    if request.url.path in ["/dashboard", "/admin", "/students", "/attendance"]:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    
    return response
app.mount("/static", StaticFiles(directory="static"), name="static")

# Session Management Configuration
SESSION_SECRET_KEY = secrets.token_urlsafe(32)
ACTIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}
SESSION_TIMEOUT_HOURS = 24

class SessionManager:
    @staticmethod
    def create_session(user_type: str, user_info: dict) -> str:
        """Create a new session and return session token"""
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=SESSION_TIMEOUT_HOURS)
        
        ACTIVE_SESSIONS[session_token] = {
            "user_type": user_type,
            "user_info": user_info,
            "created_at": datetime.now(),
            "expires_at": expires_at,
            "last_activity": datetime.now()
        }
        
        print(f"🔑 Session created for {user_type}: {user_info.get('name', user_info.get('username', 'Unknown'))}")
        return session_token
    
    @staticmethod
    def validate_session(session_token: str) -> Optional[Dict[str, Any]]:
        """Validate session token and return session data if valid"""
        if not session_token or session_token not in ACTIVE_SESSIONS:
            return None
        
        session = ACTIVE_SESSIONS[session_token]
        
        # Check if session has expired
        if datetime.now() > session["expires_at"]:
            del ACTIVE_SESSIONS[session_token]
            return None
        
        # Update last activity
        session["last_activity"] = datetime.now()
        return session
    
    @staticmethod
    def destroy_session(session_token: str) -> bool:
        """Destroy a session"""
        if session_token in ACTIVE_SESSIONS:
            user_info = ACTIVE_SESSIONS[session_token].get("user_info", {})
            print(f"🔓 Session destroyed for: {user_info.get('name', user_info.get('username', 'Unknown'))}")
            del ACTIVE_SESSIONS[session_token]
            return True
        return False
    
    @staticmethod
    def get_active_sessions_count() -> int:
        """Get count of active sessions"""
        return len(ACTIVE_SESSIONS)
    
    @staticmethod
    def cleanup_expired_sessions():
        """Remove expired sessions"""
        current_time = datetime.now()
        expired_tokens = [
            token for token, session in ACTIVE_SESSIONS.items()
            if current_time > session["expires_at"]
        ]
        
        for token in expired_tokens:
            del ACTIVE_SESSIONS[token]
        
        if expired_tokens:
            print(f"🧹 Cleaned up {len(expired_tokens)} expired sessions")

# Session validation dependency
def get_current_session(session_token: str = Cookie(None, alias="session_token")) -> Optional[Dict[str, Any]]:
    """Dependency to get current session from cookie"""
    if not session_token:
        return None
    
    return SessionManager.validate_session(session_token)

def require_authentication(session: Optional[Dict[str, Any]] = Cookie(None, alias="session_token")):
    """Dependency that requires authentication"""
    if not session or not SessionManager.validate_session(session):
        raise HTTPException(status_code=401, detail="Authentication required")
    return session

templates = Jinja2Templates(directory="templates")
# Security middleware functions
def require_admin_access(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Require admin access"""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    if session.get("user_type") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return session

def require_user_or_admin_access(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Allow both user and admin access"""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_type = session.get("user_type")
    if user_type not in ["admin", "user"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return session



class BulkExportRequest(BaseModel):
    start_date: str
    end_date: str
    format: str
    include_weekends: bool = False
    include_holidays: bool = False

# Pydantic models
class StudentInfo(BaseModel):
    name: str
    email: EmailStr
    student_id: str

class FacePhotoData(BaseModel):
    session_id: str
    image_data: str

class RegistrationComplete(BaseModel):
    session_id: str
    
class DetectionImage(BaseModel):
    image_data: str

class AttendanceSystem:
    def __init__(self):
        self.known_face_encodings = []
        self.known_face_names = []
        self.known_face_ids = []
        self.embedding_method = None  # Track which method was used for stored embeddings
        self.conn = sqlite3.connect('attendance.db', check_same_thread=False)
        self.load_student_faces()
        self.init_extended_tables()
        self.init_advanced_tables()
    
    def load_student_faces(self):
        """Load all student face encodings from database with dimension detection"""
        if not hasattr(asian_face_recognizer, 'use_insightface') or not asian_face_recognizer.use_insightface:
            print("⚠️  buffalo_l model not available")
            return
        
        cursor = self.conn.cursor()
        cursor.execute('SELECT id, name, face_encoding FROM students WHERE status = "active" AND face_encoding IS NOT NULL')
        
        self.known_face_encodings = []
        self.known_face_names = []
        self.known_face_ids = []
        
        embedding_dimensions = []
        
        for row in cursor.fetchall():
            student_id, name, face_encoding_blob = row
            if face_encoding_blob:
                face_encoding = np.frombuffer(face_encoding_blob, dtype=np.float64)
                embedding_dimensions.append(len(face_encoding))
                self.known_face_encodings.append(face_encoding)
                self.known_face_names.append(name)
                self.known_face_ids.append(student_id)
        
        # Detect embedding method based on dimensions
        if embedding_dimensions:
            most_common_dim = max(set(embedding_dimensions), key=embedding_dimensions.count)
            if most_common_dim == 512:
                self.embedding_method = "insightface"
                print(f"📊 Loaded {len(self.known_face_encodings)} student faces (InsightFace 512D)")
            elif most_common_dim == 128:
                self.embedding_method = "face_recognition"
                print(f"📊 Loaded {len(self.known_face_encodings)} student faces (face_recognition 128D)")
            else:
                print(f"⚠️  Unknown embedding dimension: {most_common_dim}")
                self.embedding_method = "unknown"
        else:
            print("📊 No student faces loaded")
    
    def start_registration_session(self, name: str, email: str, student_id: str):
        """Start a new registration session"""
        session_id = str(uuid.uuid4())
        
        # Check if student already exists
        cursor = self.conn.cursor()
        cursor.execute('SELECT id FROM students WHERE student_id = ? OR email = ?', 
                      (student_id, email))
        
        if cursor.fetchone():
            return None, "Student already registered with this ID or email"
        
        # Create session
        student_data = {
            'name': name,
            'email': email,
            'student_id': student_id
        }
        
        expires_at = datetime.now() + timedelta(minutes=30)
        
        cursor.execute('''
            INSERT INTO registration_sessions 
            (session_id, student_data, expires_at)
            VALUES (?, ?, ?)
        ''', (session_id, json.dumps(student_data), expires_at.isoformat()))
        
        self.conn.commit()
        return session_id, "Registration session started"
    
    def process_face_photo(self, image_data: str, session_id: str):
        """Process a face photo and extract encoding"""
        if not FACE_RECOGNITION_AVAILABLE:
            return None, "Face recognition not available - using basic mode"
        
        try:
            # Convert base64 to image
            if image_data.startswith('data:image'):
                image_data = image_data.split(',')[1]
            
            image_bytes = base64.b64decode(image_data)
            image = Image.open(io.BytesIO(image_bytes))
            
            # Convert to RGB
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            image_array = np.array(image)
            
            # Use buffalo_l for registration (same as detection)
            detected_faces = asian_face_recognizer.detect_faces_optimized(image_array)

            if len(detected_faces) == 0:
                return None, "No face detected in image"

            if len(detected_faces) > 1:
                return None, "Multiple faces detected. Please ensure only one face is visible"

            # Get buffalo_l face encoding (512D)
            face_data = detected_faces[0]
            face_encoding = face_data['embedding']
            face_locations = [face_data['location']]  # For compatibility

            print(f"[DEBUG] 🎯 REGISTRATION: Generated {len(face_encoding)}D embedding")

            # Print and save the image after embedding is done (for debug)
            debug_img_path = f"debug_registration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            image.save(debug_img_path)
            print(f"[DEBUG] Saved registration face image to {debug_img_path}")
            print(f"[DEBUG] Registration face encoding: {face_encoding[:10]} ... (truncated)")
            
            # Calculate quality score
            face_location = face_locations[0]
            face_height = face_location[2] - face_location[0]
            face_width = face_location[1] - face_location[3]
            face_area = face_height * face_width
            
            image_area = image_array.shape[0] * image_array.shape[1]
            quality_score = min(face_area / image_area * 100, 10.0)
            
            # Get student info for organized storage
            cursor = self.conn.cursor()
            cursor.execute('SELECT student_data FROM registration_sessions WHERE session_id = ?', (session_id,))
            session_data = cursor.fetchone()
            
            if session_data:
                student_info = json.loads(session_data[0])
                student_id = student_info['student_id']
                student_name = student_info['name']
                
                # Save image in organized directory structure
                timestamp = str(int(datetime.now().timestamp()))
                photo_path = get_student_photo_path(student_id, student_name, session_id, timestamp)
                image.save(photo_path, 'JPEG', quality=90)
            else:
                # Fallback to old method
                photo_filename = f"{session_id}_{datetime.now().timestamp()}.jpg"
                photo_path = os.path.join('student_photos', photo_filename)
                image.save(photo_path, 'JPEG', quality=90)
            
            return {
                'encoding': face_encoding,
                'quality_score': quality_score,
                'photo_path': photo_path
            }, "Face processed successfully"
            
        except Exception as e:
            return None, f"Error processing image: {str(e)}"
    
    def add_face_encoding(self, session_id: str, encoding_data):
        """Add face encoding to session"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT student_data, photos_uploaded FROM registration_sessions 
            WHERE session_id = ? AND status = 'in_progress'
        ''', (session_id,))
        
        session = cursor.fetchone()
        if not session:
            return False, "Invalid or expired session"
        
        # Store encoding temporarily
        temp_file = f"temp_encodings_{session_id}.npy"
        
        if os.path.exists(temp_file):
            existing = np.load(temp_file, allow_pickle=True).tolist()
        else:
            existing = []
        
        existing.append({
            'encoding': encoding_data['encoding'].tolist(),
            'quality_score': encoding_data['quality_score'],
            'photo_path': encoding_data['photo_path']
        })
        
        np.save(temp_file, existing)
        
        photos_uploaded = session[1] + 1
        cursor.execute('''
            UPDATE registration_sessions 
            SET photos_uploaded = ?
            WHERE session_id = ?
        ''', (photos_uploaded, session_id))
        
        self.conn.commit()
        return True, f"Photo {photos_uploaded} processed successfully"
    
    def complete_registration(self, session_id: str):
        """Complete student registration"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT student_data, photos_uploaded FROM registration_sessions 
            WHERE session_id = ? AND status = 'in_progress'
        ''', (session_id,))
        
        session = cursor.fetchone()
        if not session:
            return False, "Invalid or expired session"
        
        student_data = json.loads(session[0])
        photos_uploaded = session[1]
        
        if photos_uploaded < 3:  # Minimum 3 photos
            return False, f"Need at least 3 photos, got {photos_uploaded}"
        
        try:
            # Load encodings
            temp_file = f"temp_encodings_{session_id}.npy"
            if not os.path.exists(temp_file):
                return False, "No face encodings found"
            
            encodings_data = np.load(temp_file, allow_pickle=True).tolist()
            
            # Calculate average encoding
            encodings = [np.array(item['encoding']) for item in encodings_data]
            average_encoding = np.mean(encodings, axis=0)
            
            # Calculate verification score
            if FACE_RECOGNITION_AVAILABLE:
                distances = [face_recognition.face_distance([average_encoding], encoding)[0] 
                            for encoding in encodings]
                verification_score = 1.0 - np.mean(distances)
            else:
                verification_score = 0.8  # Default score
            
            # Insert student
            cursor.execute('''
                INSERT INTO students 
                (student_id, name, email, face_encoding, photo_count, verification_score)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                student_data['student_id'],
                student_data['name'],
                student_data['email'],
                average_encoding.tobytes(),
                photos_uploaded,
                verification_score
            ))
            
            new_student_id = cursor.lastrowid
            
            # Insert individual encodings
            for encoding_item in encodings_data:
                cursor.execute('''
                    INSERT INTO face_encodings 
                    (student_id, encoding_data, photo_path, quality_score)
                    VALUES (?, ?, ?, ?)
                ''', (
                    new_student_id,
                    np.array(encoding_item['encoding']).tobytes(),
                    encoding_item['photo_path'],
                    encoding_item['quality_score']
                ))
            
            # Mark session completed
            cursor.execute('''
                UPDATE registration_sessions 
                SET status = 'completed'
                WHERE session_id = ?
            ''', (session_id,))
            
            self.conn.commit()
            
            # Reload student faces
            self.load_student_faces()
            
            # Clean up
            if os.path.exists(temp_file):
                os.remove(temp_file)
            
            return True, f"Registration completed for {student_data['name']}"
            
        except Exception as e:
            return False, f"Registration failed: {str(e)}"
        
    def init_extended_tables(self):
        """Initialize additional tables for enhanced attendance management"""
        cursor = self.conn.cursor()
        # Create holidays table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS holidays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE NOT NULL UNIQUE,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Create course_settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS course_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                academic_year TEXT NOT NULL,
                semester TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Add new columns to attendance table if they don't exist
        try:
            cursor.execute('ALTER TABLE attendance ADD COLUMN manual_reason TEXT')
        except Exception:
            pass  # Column already exists
        try:
            cursor.execute('ALTER TABLE attendance ADD COLUMN is_manual BOOLEAN DEFAULT FALSE')
        except Exception:
            pass  # Column already exists
        self.conn.commit()
    
    def init_advanced_tables(self):
        """Initialize advanced tables for course and session management"""
        cursor = self.conn.cursor()
        
        # Courses table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                description TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Session configurations table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS session_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER,
                session_type TEXT NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                FOREIGN KEY (course_id) REFERENCES courses (id)
            )
        ''')
        
        # Session attendance table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS session_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                course_id INTEGER NOT NULL,
                session_type TEXT NOT NULL,
                date DATE NOT NULL,
                arrival_time TIME,
                is_late BOOLEAN DEFAULT FALSE,
                is_manual BOOLEAN DEFAULT FALSE,
                manual_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students (id),
                FOREIGN KEY (course_id) REFERENCES courses (id),
                UNIQUE(student_id, course_id, session_type, date)
            )
        ''')
        
        # Add columns to existing attendance table
        try:
            cursor.execute('ALTER TABLE attendance ADD COLUMN session_type TEXT DEFAULT "morning"')
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute('ALTER TABLE attendance ADD COLUMN is_late BOOLEAN DEFAULT FALSE')
        except sqlite3.OperationalError:
            pass
        
        # Create default course if none exists
        cursor.execute('SELECT COUNT(*) FROM courses WHERE is_active = TRUE')
        if cursor.fetchone()[0] == 0:
            from datetime import date
            today = date.today()
            start_date = date(today.year, 1, 1)
            end_date = date(today.year, 12, 31)
            
            cursor.execute('''
                INSERT INTO courses (name, start_date, end_date, description)
                VALUES (?, ?, ?, ?)
            ''', (
                f"Default Course - {today.year}",
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d'),
                "Default course created automatically"
            ))
            
            course_id = cursor.lastrowid
            
            cursor.execute('''
                INSERT INTO session_configs (course_id, session_type, start_time, end_time)
                VALUES (?, ?, ?, ?), (?, ?, ?, ?)
            ''', (
                    course_id, 'morning', '08:45:00', '09:30:00',
                    course_id, 'afternoon', '13:45:00', '14:30:00'
            ))
        
        self.conn.commit()

    def get_active_course(self):
        """Get the currently active course"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, name, start_date, end_date, description
            FROM courses 
            WHERE is_active = TRUE 
            ORDER BY created_at DESC LIMIT 1
        ''')
        return cursor.fetchone()

    def is_session_active(self, session_type: str, current_time=None):
        """Check if a session is currently active"""
        if current_time is None:
            current_time = datetime.now().time()
        elif isinstance(current_time, str):
            current_time = datetime.strptime(current_time, '%H:%M:%S').time()
        
        course = self.get_active_course()
        if not course:
            return False
        
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT start_time, end_time
if __name__ == "__main__":
    import uvicorn
    import os
    import subprocess
    
    # SSL certificate files
    cert_file = "cert.pem"
    key_file = "key.pem"
    
    # Generate self-signed certificate if it doesn't exist
    if not os.path.exists(cert_file) or not os.path.exists(key_file):
        print("🔧 Generating SSL certificates...")
        try:
            # Create self-signed certificate
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:4096", 
                "-keyout", key_file, "-out", cert_file, "-days", "365", "-nodes",
                "-subj", "/C=IN/ST=Maharashtra/L=Mumbai/O=CDAC/CN=10.212.13.129"
            ], check=True)
            print("✅ SSL certificates generated!")
        except subprocess.CalledProcessError:
            print("❌ Failed to generate SSL certificates. Install OpenSSL first.")
            print("📊 Running on HTTP: http://10.212.13.129:8000/")
            uvicorn.run("main_with_face_recognition:app", host="10.212.13.129", port=8000)
            exit()
    
    # Run with HTTPS
    print("🔒 HTTPS Dashboard: https://10.212.13.129:8000/")
    print("⚠️  You may see a security warning - click 'Advanced' → 'Proceed to 10.212.13.129 (unsafe)'")
    print("💡 Tip: Bookmark the HTTPS URL to avoid the warning next time")
    
    try:
        uvicorn.run(
            "main_with_face_recognition:app", 
            host="10.212.13.129", 
            port=8000,
            ssl_keyfile=key_file,
            ssl_certfile=cert_file
        )
    except Exception as e:
        print(f"❌ HTTPS failed: {e}")
        print("📊 Falling back to HTTP: http://10.212.13.129:8000/")
        uvicorn.run("main_with_face_recognition:app", host="10.212.13.129", port=8000)            FROM session_configs
            WHERE course_id = ? AND session_type = ? AND is_active = TRUE
        ''', (course[0], session_type))
        
        session_config = cursor.fetchone()
        if not session_config:
            return False
        
        start_time = datetime.strptime(session_config[0], '%H:%M:%S').time()
        end_time = datetime.strptime(session_config[1], '%H:%M:%S').time()
        
        return start_time <= current_time <= end_time

    def get_session_attendance_today(self, session_type: str):
        """Get today's attendance for a specific session"""
        today = datetime.now().date().strftime('%Y-%m-%d')
        course = self.get_active_course()
        
        if not course:
            return []
        
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT s.id, s.name, s.student_id, sa.arrival_time, sa.is_late
            FROM students s
            LEFT JOIN session_attendance sa ON s.id = sa.student_id 
                AND sa.course_id = ? AND sa.session_type = ? AND sa.date = ?
            WHERE s.status = 'active'
            ORDER BY s.name
        ''', (course[0], session_type, today))
        
        return cursor.fetchall()

    def create_course(self, name: str, start_date: str, end_date: str, description: str = None):
        """Create a new course"""
        cursor = self.conn.cursor()
        
        try:
            cursor.execute('UPDATE courses SET is_active = FALSE')
            
            cursor.execute('''
                INSERT INTO courses (name, start_date, end_date, description)
                VALUES (?, ?, ?, ?)
            ''', (name, start_date, end_date, description))
            
            course_id = cursor.lastrowid
            
            cursor.execute('''
                INSERT INTO session_configs (course_id, session_type, start_time, end_time)
                VALUES (?, ?, ?, ?), (?, ?, ?, ?)
            ''', (
                course_id, 'morning', '08:45:00', '09:30:00',     # Change these
                course_id, 'afternoon', '13:45:00', '14:30:00'   # Change these
            ))
            
        except Exception as e:
            return False, f"Failed to create course: {str(e)}"

    def get_student_slot_attendance_data(self, student_id: int):
        """Get comprehensive slot-based attendance data for a specific student"""
        from datetime import date, timedelta, datetime
        cursor = self.conn.cursor()

        print(f"DEBUG: get_student_slot_attendance_data() - slot-based version")
        print(f"[DEBUG] Getting attendance for student_id: {student_id}")

        # Get student joining date
        cursor.execute("SELECT joining_date FROM students WHERE id = ?", (student_id,))
        joining_row = cursor.fetchone()
        
        if joining_row and joining_row[0]:
            try:
                start_date = datetime.strptime(joining_row[0], '%Y-%m-%d').date()
            except:
                start_date = date(2025, 1, 1)  # Start of year if parsing fails
        else:
            start_date = date(2025, 1, 1)  

        timezone = pytz.timezone('Asia/Kolkata')
        end_date = datetime.now(timezone).date()  # Ensure end_date is in the correct timezone
        print(f"[DEBUG] Date range: {start_date} to {end_date}")

        # Get slot attendance records (the working data)
        cursor.execute("""
            SELECT date, slot_id, time_marked
            FROM slot_attendance 
            WHERE student_id = ?
            ORDER BY date, slot_id
        """, (student_id,))
        slot_records = cursor.fetchall()
        print(f"[DEBUG] Found {len(slot_records)} slot records")

        # Get holidays
        cursor.execute("SELECT date, name, type FROM holidays ORDER BY date")
        holidays = cursor.fetchall()
        holiday_dates = []
        for h in holidays:
            try:
                holiday_dates.append(datetime.strptime(h[0], '%Y-%m-%d').date())
            except:
                continue

        # Process slot data
        attendance_dict = {}
        slot_summary = {}
        
        for record in slot_records:
            date_str, slot_id, time_marked = record
            
            if date_str not in slot_summary:
                slot_summary[date_str] = {}
            
            # Convert slot_id to session_type for consistency
            session_type = 'morning' if slot_id == 'morning' else 'afternoon'
            slot_summary[date_str][session_type] = time_marked

        # Calculate attendance for each day
        full_days = 0
        half_days = 0
        total_working_days = 0
        
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            
            # Skip only Sunday (weekday() == 6) and holidays
            if current_date.weekday() == 6 or current_date in holiday_dates:
                current_date += timedelta(days=1)
                continue
                    
            total_working_days += 1
            
            if date_str in slot_summary:
                sessions = slot_summary[date_str]
                has_morning = 'morning' in sessions
                has_afternoon = 'afternoon' in sessions
                
                if has_morning and has_afternoon:
                    attendance_dict[date_str] = 'present'  # Full day
                    full_days += 1
                elif has_morning or has_afternoon:
                    attendance_dict[date_str] = 'partial'  # Half day
                    half_days += 1
                else:
                    attendance_dict[date_str] = 'absent'
            else:
                attendance_dict[date_str] = 'absent'
                
            current_date += timedelta(days=1)

        absent_days = total_working_days - full_days - half_days
        
        # Calculate percentage (full days + half days * 0.5)
        effective_present_days = full_days + (half_days * 0.5)
        attendance_percentage = (effective_present_days / total_working_days * 100) if total_working_days > 0 else 0

        print(f"[DEBUG] Stats - Full days: {full_days}, Half days: {half_days}, Absent: {absent_days}, Total working: {total_working_days}, Percentage: {attendance_percentage:.1f}%")

        # Add session details to attendance_dict for calendar display
        attendance_with_sessions = {}
        for date_str, status in attendance_dict.items():
            attendance_with_sessions[date_str] = {
                'status': status,
                'morning': slot_summary.get(date_str, {}).get('morning'),
                'afternoon': slot_summary.get(date_str, {}).get('afternoon')
            }

        return {
            'success': True,
            'attendance': attendance_with_sessions,
            'stats': {
                'full_days': full_days,
                'half_days': half_days,
                'absent_days': absent_days,
                'holidays': len(holiday_dates),
                'percentage': round(attendance_percentage, 1),
                'total_working_days': total_working_days
            }
        }



    def get_today_attendance(self):
        """Get today's session-based attendance with proper timezone handling"""
        timezone = pytz.timezone('Asia/Kolkata')  # Ensure to use your desired timezone
        today = datetime.now(timezone).date()  # Localize to the right timezone
        cursor = self.conn.cursor()

        cursor.execute('''
            SELECT s.name, s.student_id, s.email, 
                sa_morning.arrival_time as morning_time,
                sa_afternoon.arrival_time as afternoon_time
            FROM students s
            LEFT JOIN session_attendance sa_morning ON s.id = sa_morning.student_id 
                AND sa_morning.date = ? AND sa_morning.session_type = 'morning'
            LEFT JOIN session_attendance sa_afternoon ON s.id = sa_afternoon.student_id 
                AND sa_afternoon.date = ? AND sa_afternoon.session_type = 'afternoon'
            WHERE s.status = 'active'
            ORDER BY s.name
        ''', (today, today))

        return cursor.fetchall()

    
    def mark_manual_session_attendance(self, student_id: int, date_str: str, session_type: str, reason: str = None):
        """Mark session attendance manually - FIXED to use slot_attendance"""
        cursor = self.conn.cursor()
        
        # Check if already marked for this session in slot_attendance
        cursor.execute('''
            SELECT id FROM slot_attendance 
            WHERE student_id = ? AND date = ? AND slot_id = ?
        ''', (student_id, date_str, session_type))
        
        if cursor.fetchone():
            return False, f"{session_type.title()} session attendance already marked for this date"
        
        # Check if holiday
        cursor.execute('SELECT id FROM holidays WHERE date = ?', (date_str,))
        if cursor.fetchone():
            return False, "Cannot mark attendance on a holiday"
        
        # Mark session attendance in slot_attendance table (NOT session_attendance)
        current_time = datetime.now().time().strftime('%H:%M:%S')
        cursor.execute('''
            INSERT INTO slot_attendance 
            (student_id, date, slot_id, time_marked, is_manual, manual_reason)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (student_id, date_str, session_type, current_time, True, reason))
        
        self.conn.commit()
        return True, f"{session_type.title()} session attendance marked successfully"
        
    def get_student_count(self):
        """Get total number of active students"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM students WHERE status = "active"')
        return cursor.fetchone()[0]
           


    def add_holiday(self, date_str: str, name: str, holiday_type: str):
        """Add a holiday"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('INSERT INTO holidays (date, name, type) VALUES (?, ?, ?)',
                          (date_str, name, holiday_type))
            self.conn.commit()
            return True, "Holiday added successfully"
        except Exception as e:
            return False, f"Holiday already exists: {str(e)}"

    def get_holidays(self):
        """Get all holidays"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT id, date, name, type FROM holidays ORDER BY date DESC')
        holidays = cursor.fetchall()
        
        return {
            'success': True,
            'holidays': [
                {'id': h[0], 'date': h[1], 'name': h[2], 'type': h[3]}
                for h in holidays
            ]
        }
        
    def get_student_attendance_data(self, student_id: int):
        """Get comprehensive session-based attendance data for a specific student"""
        from datetime import date, timedelta, datetime
        cursor = self.conn.cursor()

        print(f"🚨 DEBUG: get_student_attendance_data() - session-based version")
        print(f"[DEBUG] Getting attendance for student_id: {student_id}")

        # Get student joining date
        cursor.execute("SELECT joining_date FROM students WHERE id = ?", (student_id,))
        joining_row = cursor.fetchone()
        
        if joining_row and joining_row[0]:
            try:
                start_date = datetime.strptime(joining_row[0], '%Y-%m-%d').date() + timedelta(days=1)
            except:
                start_date = date.today()
        else:
            start_date = date.today()

        # FIX: Define end_date properly
        end_date = date.today()  # Only process up to today, not future dates
        print(f"[DEBUG] Date range: {start_date} to {end_date}")

        # Get session attendance records
        cursor.execute("""
            SELECT date, session_type, arrival_time, is_manual, manual_reason
            FROM session_attendance 
            WHERE student_id = ?
            ORDER BY date, session_type
        """, (student_id,))
        session_records = cursor.fetchall()
        print(f"[DEBUG] Found {len(session_records)} session records")

        # Get holidays
        cursor.execute("SELECT date, name, type FROM holidays ORDER BY date")
        holidays = cursor.fetchall()
        holiday_dates = []
        for h in holidays:
            try:
                holiday_dates.append(datetime.strptime(h[0], '%Y-%m-%d').date())
            except:
                continue

        # Process session data
        attendance_dict = {}
        session_summary = {}
        
        for record in session_records:
            date_str, session_type, arrival_time, is_manual, manual_reason = record
            
            if date_str not in session_summary:
                session_summary[date_str] = {}
            
            session_summary[date_str][session_type] = arrival_time

        # Calculate attendance for each day
        full_days = 0
        half_days = 0
        total_working_days = 0
        
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            
            # Skip only Sunday (weekday() == 6) and holidays - Saturday is a working day
            if current_date.weekday() == 6 or current_date in holiday_dates:
                current_date += timedelta(days=1)
                continue
                
            total_working_days += 1
            
            if date_str in session_summary:
                sessions = session_summary[date_str]
                has_morning = 'morning' in sessions
                has_afternoon = 'afternoon' in sessions
                
                if has_morning and has_afternoon:
                    attendance_dict[date_str] = 'present'  # Full day
                    full_days += 1
                elif has_morning or has_afternoon:
                    attendance_dict[date_str] = 'partial'  # Half day
                    half_days += 1
                else:
                    attendance_dict[date_str] = 'absent'
            else:
                attendance_dict[date_str] = 'absent'
                
            current_date += timedelta(days=1)

        absent_days = total_working_days - full_days - half_days
        
        # Calculate percentage (full days + half days * 0.5)
        effective_present_days = full_days + (half_days * 0.5)
        attendance_percentage = (effective_present_days / total_working_days * 100) if total_working_days > 0 else 0

        print(f"[DEBUG] Stats - Full days: {full_days}, Half days: {half_days}, Absent: {absent_days}, Total working: {total_working_days}, Percentage: {attendance_percentage:.1f}%")

        # Add session details to attendance_dict for calendar display
        attendance_with_sessions = {}
        for date_str, status in attendance_dict.items():
            attendance_with_sessions[date_str] = {
                'status': status,
                'morning': session_summary.get(date_str, {}).get('morning'),
                'afternoon': session_summary.get(date_str, {}).get('afternoon')
            }

        return {
            'success': True,
            'attendance': attendance_with_sessions,
            'stats': {
                'full_days': full_days,
                'half_days': half_days,
                'absent_days': absent_days,
                'holidays': len(holiday_dates),
                'percentage': round(attendance_percentage, 1),
                'total_working_days': total_working_days
            }
        }

   


    def delete_holiday(self, holiday_id: int):
        """Delete a holiday"""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM holidays WHERE id = ?', (holiday_id,))
        
        if cursor.rowcount > 0:
            self.conn.commit()
            return True, "Holiday deleted successfully"
        else:
            return False, "Holiday not found"

# Initialize attendance system
attendance_system = AttendanceSystem()

# Session management helpers
def get_user_from_session(request: Request) -> Optional[dict]:
    """Simple session check - you can enhance this later"""
    # For now, we'll use a simple referer-based check
    referer = request.headers.get("referer", "")
    
    if any(path in referer for path in ["/dashboard", "/admin", "/students", "/attendance"]):
        return {"authenticated": True, "type": "admin"}
    
    return None

def is_authenticated_request(request: Request) -> bool:
    """Check if request comes from authenticated session"""
    return get_user_from_session(request) is not None

# Simple models for login
class SimpleAdminLogin(BaseModel):
    username: str
    password: str

class SimpleFaceLogin(BaseModel):
    image_data: str

# Simple admin credentials (you can change these)
ADMIN_CREDENTIALS = {
    "admin": "admin123",
    "teacher": "teacher123"
}

# User credentials for attendance system access only
USER_CREDENTIALS = {
    "user": "user123",
    "student": "student123",
    "operator": "operator123"
}

# API Routes
@app.get("/")
async def root(request: Request, session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Smart root route with session checking"""
    from fastapi.responses import RedirectResponse
    
    # Clean up expired sessions
    SessionManager.cleanup_expired_sessions()
    
    # If user has valid session, redirect based on type
    if session:
        user_type = session.get("user_type", "")
        if user_type == "admin":
            return RedirectResponse(url="/dashboard")
        elif user_type == "user":
            return RedirectResponse(url="/attendance")
        else:
            return RedirectResponse(url="/dashboard")
    
    # No session, go to login
    return RedirectResponse(url="/login")

@app.get("/login", response_class=HTMLResponse)
async def simple_login_page(request: Request):
    """Simple dual login page"""
    return templates.TemplateResponse("simple_login.html", {"request": request})

@app.post("/api/admin-login")
async def simple_admin_login(login_data: SimpleAdminLogin, response: Response):
    """Admin login with session management"""
    try:
        username = login_data.username
        password = login_data.password
        
        # Check credentials
        if username in ADMIN_CREDENTIALS and ADMIN_CREDENTIALS[username] == password:
            # Create session
            user_info = {
                "username": username,
                "name": f"Administrator ({username})",
                "role": "admin"
            }
            
            session_token = SessionManager.create_session("admin", user_info)
            
            # Set secure cookie
            response.set_cookie(
                key="session_token",
                value=session_token,
                max_age=SESSION_TIMEOUT_HOURS * 3600,
                httponly=True,
                secure=False,  # Set to True in production with HTTPS
                samesite="lax"
            )
            
            return {
                "success": True,
                "message": "Admin login successful",
                "user_type": "admin",
                "username": username,
                "redirect_url": "/dashboard"
            }
        else:
            return {
                "success": False,
                "message": "Invalid username or password"
            }
            
    except Exception as e:
        return {
            "success": False,
            "message": f"Login failed: {str(e)}"
        }

@app.post("/api/user-login")
async def user_login(login_data: SimpleAdminLogin, response: Response):
    """User login with session management - only attendance access"""
    try:
        username = login_data.username
        password = login_data.password
        
        # Validate credentials
        if not username or not password:
            return {
                "success": False,
                "message": "Username and password are required"
            }
        
        # Check user credentials
        if username in USER_CREDENTIALS and USER_CREDENTIALS[username] == password:
            # Create session for user
            user_info = {
                "username": username,
                "name": f"User ({username})",
                "role": "user",
                "permissions": ["attendance"],  # Only attendance access
                "login_time": datetime.now().isoformat()
            }
            
            session_token = SessionManager.create_session("user", user_info)
            
            # Set secure cookie with proper security settings
            response.set_cookie(
                key="session_token",
                value=session_token,
                max_age=SESSION_TIMEOUT_HOURS * 3600,
                httponly=True,  # Prevent XSS attacks
                secure=False,   # Set to True in production with HTTPS
                samesite="lax"  # CSRF protection
            )
            
            print(f"🔑 User session created for: {username}")
            
            return {
                "success": True,
                "message": "User login successful",
                "user_type": "user",
                "username": username,
                "redirect_url": "/attendance"  # Direct to attendance page
            }
        else:
            print(f"❌ Failed login attempt for user: {username}")
            return {
                "success": False,
                "message": "Invalid username or password"
            }
            
    except Exception as e:
        print(f"❌ User login error: {str(e)}")
        return {
            "success": False,
            "message": f"Login failed: {str(e)}"
        }

@app.post("/api/face-login")
async def simple_face_login(login_data: SimpleFaceLogin, response: Response):
    """Simple face login - uses existing face recognition"""
    try:
        # Use your existing face recognition code
        if not FACE_RECOGNITION_AVAILABLE:
            return {
                "success": False,
                "message": "Face recognition not available",
                "faces_detected": 0
            }
        
        # Convert base64 to image
        if login_data.image_data.startswith('data:image'):
            image_data_clean = login_data.image_data.split(',')[1]
        else:
            image_data_clean = login_data.image_data
        
        image_bytes = base64.b64decode(image_data_clean)
        image = Image.open(io.BytesIO(image_bytes))
        
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        image_array = np.array(image)
        
        # Use your existing face detection
        detected_faces = asian_face_recognizer.detect_faces_optimized(image_array)
        
        if len(detected_faces) == 0:
            return {
                "success": False,
                "message": "No face detected",
                "faces_detected": 0
            }
        
        if len(detected_faces) > 1:
            return {
                "success": False,
                "message": "Multiple faces detected",
                "faces_detected": len(detected_faces)
            }
        
        face_encoding = detected_faces[0]['embedding']
        
        # Find best match using your existing system
        if len(attendance_system.known_face_encodings) > 0:
            similarities = []
            for known_encoding in attendance_system.known_face_encodings:
                face_norm = face_encoding / np.linalg.norm(face_encoding)
                known_norm = known_encoding / np.linalg.norm(known_encoding)
                similarity = np.dot(face_norm, known_norm)
                similarities.append(similarity)
            
            best_match_index = np.argmax(similarities)
            best_similarity = similarities[best_match_index]
            
            RECOGNITION_THRESHOLD = 0.60
            
            if best_similarity > RECOGNITION_THRESHOLD:
                student_id = attendance_system.known_face_ids[best_match_index]
                student_name = attendance_system.known_face_names[best_match_index]
                
                # Create session for face login
                user_info = {
                    "id": student_id,
                    "name": student_name,
                    "student_id": student_id,
                    "role": "student"
                }
                
                session_token = SessionManager.create_session("student", user_info)
                
                # Set secure cookie
                response.set_cookie(
                    key="session_token",
                    value=session_token,
                    max_age=SESSION_TIMEOUT_HOURS * 3600,
                    httponly=True,
                    secure=False,  # Set to True in production with HTTPS
                    samesite="lax"
                )
                
                return {
                    "success": True,
                    "message": "Face login successful",
                    "student": {
                        "id": student_id,
                        "name": student_name,
                        "confidence": float(best_similarity)
                    },
                    "faces_detected": 1
                }
            else:
                return {
                    "success": False,
                    "message": "Face not recognized",
                    "faces_detected": 1
                }
        else:
            return {
                "success": False,
                "message": "No students registered",
                "faces_detected": 1
            }
            
    except Exception as e:
        return {
            "success": False,
            "message": f"Face login failed: {str(e)}",
            "faces_detected": 0
        }

# Dashboard routes
@app.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, session: Dict[str, Any] = Depends(require_admin_access)):
    """Full admin dashboard - your existing dashboard"""
    # Redirect to login if no session
    if not session:
        return RedirectResponse(url="/login")
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "face_recognition_available": FACE_RECOGNITION_AVAILABLE,
        "opencv_available": OPENCV_AVAILABLE
    })


# Add these routes to your Flask app file

# Add these routes to your FastAPI app (replace the incomplete Flask ones)

@app.get("/about", response_class=HTMLResponse)
async def about_page():
    """About Us page"""
    try:
        # Fixed path: files are in templates/ folder
        with open('templates/about.html', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="About page not found in templates folder")

@app.get("/contact", response_class=HTMLResponse) 
async def contact_page():
    """Contact Us page"""
    try:
        # Fixed path: files are in templates/ folder
        with open('templates/contact.html', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Contact page not found in templates folder")
    

@app.get("/register", response_class=HTMLResponse)
async def registration_page(request: Request, session: Dict[str, Any] = Depends(require_admin_access)):
    """Student registration page"""
    return templates.TemplateResponse("register.html", {
        "request": request,
        "face_recognition_available": FACE_RECOGNITION_AVAILABLE
    })

@app.get("/attendance", response_class=HTMLResponse)
async def attendance_page(request: Request, session: Dict[str, Any] = Depends(require_user_or_admin_access)):
    """Live attendance page"""
    return templates.TemplateResponse("attendance.html", {"request": request})

@app.get("/students", response_class=HTMLResponse)
async def students_page(request: Request, session: Dict[str, Any] = Depends(require_admin_access)):
    return templates.TemplateResponse("students.html", {
        "request": request,
        "face_recognition_available": FACE_RECOGNITION_AVAILABLE
    })

@app.get("/api/attendance/student/{student_id}/slots")
async def get_student_slot_attendance(student_id: int):
    """Get detailed slot-based attendance data for a specific student"""
    try:
        data = attendance_system.get_student_slot_attendance_data(student_id)
        return data
    except Exception as e:
        return {"success": False, "message": str(e)}
    


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, session: Dict[str, Any] = Depends(require_admin_access)):
    """Admin dashboard page"""
    return templates.TemplateResponse("admin.html", {"request": request})

@app.get("/attendance-management", response_class=HTMLResponse)
async def attendance_management_page(request: Request, session: Dict[str, Any] = Depends(require_admin_access)):
    """Enhanced attendance management page"""
    return templates.TemplateResponse("attendance_management.html", {"request": request})

@app.get("/advanced-attendance", response_class=HTMLResponse)
async def advanced_attendance_page(request: Request, session: Dict[str, Any] = Depends(require_admin_access)):
    """Advanced attendance management page"""
    return templates.TemplateResponse("advanced_attendance.html", {"request": request})

# API endpoints
@app.post("/api/detect_attendance")
async def detect_attendance(image_data: DetectionImage):
    """Detect faces in image and mark attendance"""
    if not FACE_RECOGNITION_AVAILABLE:
        return {"success": False, "message": "Face recognition not available"}
    
    try:
        # Convert base64 to image
        if image_data.image_data.startswith('data:image'):
            image_data_clean = image_data.image_data.split(',')[1]
        else:
            image_data_clean = image_data.image_data
        
        image_bytes = base64.b64decode(image_data_clean)
        image = Image.open(io.BytesIO(image_bytes))
        
        # Convert to RGB
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        image_array = np.array(image)
        
        # Use buffalo_l for detection (same as registration)
        detected_faces = asian_face_recognizer.detect_faces_optimized(image_array)
        
        if len(detected_faces) == 0:
            return {
                "success": False, 
                "message": "No faces detected",
                "faces_detected": 0
            }
        
        recognized_students = []
        unknown_faces = 0
        
        for face_data in detected_faces:
            face_encoding = face_data['embedding']
            
            # Find best match
            if len(attendance_system.known_face_encodings) > 0:
                # Calculate similarities using dot product (for InsightFace embeddings)
                similarities = []
                for known_encoding in attendance_system.known_face_encodings:
                    # Normalize embeddings
                    face_norm = face_encoding / np.linalg.norm(face_encoding)
                    known_norm = known_encoding / np.linalg.norm(known_encoding)
                    similarity = np.dot(face_norm, known_norm)
                    similarities.append(similarity)
                
                best_match_index = np.argmax(similarities)
                best_similarity = similarities[best_match_index]
                
                # Threshold for recognition (adjust as needed)
                RECOGNITION_THRESHOLD = 0.60  
                
                if best_similarity > RECOGNITION_THRESHOLD:
                    student_id = attendance_system.known_face_ids[best_match_index]
                    student_name = attendance_system.known_face_names[best_match_index]
                    
                    # Check if already marked today
                    today = datetime.now().date()
                    cursor = attendance_system.conn.cursor()
                    cursor.execute('SELECT id FROM attendance WHERE student_id = ? AND date = ?', 
                                 (student_id, today))
                    
                    if cursor.fetchone():
                        status = "already_marked"
                        message = f"{student_name} already marked present today"
                    else:
                        # Mark attendance
                        cursor.execute('''
                            INSERT INTO attendance (student_id, date, time_in, is_manual)
                            VALUES (?, ?, ?, ?)
                        ''', (student_id, today, datetime.now().time().strftime('%H:%M:%S'), False))
                        
                        attendance_system.conn.commit()
                        status = "marked"
                        message = f"Attendance marked for {student_name}"
                    
                    # Define face_location from face_data['location'] before using it
                    face_location = face_data['location']
                    recognized_students.append({
                        "student_id": student_id,
                        "name": student_name,
                        "confidence": float(best_similarity),
                        "status": status,
                        "message": message,
                        "location": {
                            "top": int(face_location[0]),
                            "right": int(face_location[1]),
                            "bottom": int(face_location[2]),
                            "left": int(face_location[3])
                        }
                    })
                else:
                    unknown_faces += 1
            else:
                unknown_faces += 1
        
        return {
            "success": True,
            "faces_detected": len(detected_faces),
            "recognized_students": recognized_students,
            "unknown_faces": unknown_faces,
            "message": f"Processed {len(detected_faces)} faces, recognized {len(recognized_students)} students"
        }
        
    except Exception as e:
        print(f"[ERROR] Detection failed: {str(e)}")
        return {
            "success": False,
            "message": f"Detection failed: {str(e)}",
            "faces_detected": 0
        }

@app.post("/api/start_registration")
async def start_registration(student_info: StudentInfo):
    """Start registration session"""
    session_id, message = attendance_system.start_registration_session(
        student_info.name, student_info.email, student_info.student_id
    )
    
    if session_id:
        return {"success": True, "session_id": session_id, "message": message}
    else:
        raise HTTPException(status_code=400, detail=message)

@app.post("/api/upload_face_photo")
async def upload_face_photo(photo_data: FacePhotoData):
    """Upload and process face photo"""
    result, message = attendance_system.process_face_photo(
        photo_data.image_data, photo_data.session_id
    )
    
    if result:
        success, add_message = attendance_system.add_face_encoding(
            photo_data.session_id, result
        )
        
        if success:
            return {"success": True, "message": add_message, "quality_score": result['quality_score']}
        else:
            raise HTTPException(status_code=400, detail=add_message)
    else:
        raise HTTPException(status_code=400, detail=message)

@app.post("/api/complete_registration")
async def complete_registration(reg_data: RegistrationComplete):
    """Complete registration"""
    success, message = attendance_system.complete_registration(reg_data.session_id)
    
    if success:
        return {"success": True, "message": message}
    else:
        raise HTTPException(status_code=400, detail=message)

@app.get("/api/attendance/today")
async def get_today_attendance():
    """Get today's attendance"""
    return attendance_system.get_today_attendance()

@app.get("/api/students/count")
async def get_student_count():
    """Get total number of students"""
    count = attendance_system.get_student_count()
    return {"total_students": count}

@app.get("/api/system/status")
async def get_system_status():
    """Get system status"""
    return {
        "face_recognition_available": FACE_RECOGNITION_AVAILABLE,
        "opencv_available": OPENCV_AVAILABLE,
        "database_connected": True,
        "students_loaded": len(attendance_system.known_face_encodings)
    }

@app.get("/api/students/list")
async def list_students():
    try:
        cursor = attendance_system.conn.cursor()
        cursor.execute('''
            SELECT s.id, s.student_id, s.name, s.email, s.photo_count, s.verification_score,
                   COUNT(a.id) as attendance_count,
                   MAX(a.date) as last_attendance,
                   s.joining_date
            FROM students s
            LEFT JOIN attendance a ON s.id = a.student_id  
            WHERE s.status = "active"
            GROUP BY s.id, s.student_id, s.name, s.email, s.photo_count, s.verification_score, s.joining_date
            ORDER BY s.name
        ''')
        
        students = []
        for row in cursor.fetchall():
            students.append({
                "id": row[0],
                "student_id": row[1], 
                "name": row[2],
                "email": row[3],
                "photo_count": row[4] or 0,
                "verification_score": round(row[5] or 0, 3),
                "attendance_count": row[6] or 0,
                "last_attendance": row[7] or "Never",
                "joining_date": row[8] or "Not set",
                "model": "buffalo_l_w600k_512D"
            })
        
        return {"success": True, "students": students}
    
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/dashboard/stats")
async def get_dashboard_stats():
    """Get dashboard statistics"""
    try:
        today = datetime.now().date().strftime('%Y-%m-%d')
        cursor = attendance_system.conn.cursor()
        
        # Get total students
        cursor.execute('SELECT COUNT(*) FROM students WHERE status = "active"')
        total_students = cursor.fetchone()[0]
        
        # Get today's attendance count
        cursor.execute('SELECT COUNT(*) FROM attendance WHERE date = ?', (today,))
        present_today = cursor.fetchone()[0]
        
        # Calculate stats
        absent_today = total_students - present_today
        attendance_rate = (present_today / total_students * 100) if total_students > 0 else 0
        
        print(f"Dashboard stats: Total={total_students}, Present={present_today}, Absent={absent_today}, Rate={attendance_rate}%")
        
        return {
            "success": True,
            "stats": {
                "total_students": total_students,
                "present_today": present_today,
                "absent_today": absent_today,
                "attendance_rate": round(attendance_rate, 1)
            }
        }
        
    except Exception as e:
        print(f"Dashboard stats error: {str(e)}")
        return {
            "success": False,
            "message": str(e),
            "stats": {
                "total_students": 0,
                "present_today": 0,
                "absent_today": 0,
                "attendance_rate": 0
            }
        }

@app.get("/api/attendance/student/{student_id}")
async def get_student_attendance(student_id: int):
    """Get detailed attendance data for a specific student"""
    try:
        data = attendance_system.get_student_attendance_data(student_id)
        return data
    except Exception as e:
        return {"success": False, "message": str(e)}



@app.get("/api/holidays")
async def get_holidays_api():
    """Get all holidays"""
    try:
        return attendance_system.get_holidays()
    except Exception as e:
        return {"success": False, "message": str(e)}
    
@app.get("/api/admin/session-config")
async def get_session_configuration(session: Dict[str, Any] = Depends(require_admin_access)):
    """Get current session configuration"""
    try:
        manager = create_slot_manager_instance()
        config = manager.get_session_configs()
        return {"success": True, "config": config}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.put("/api/admin/session-config/{session_type}")
async def update_session_configuration(
    session_type: str, 
    data: dict = Body(...),
    session: Dict[str, Any] = Depends(require_admin_access)
):
    """Update session timing configuration"""
    try:
        manager = create_slot_manager_instance()
        success, message = manager.update_session_timing(
            session_type=session_type,
            start_time=data['start_time'],
            end_time=data['end_time']
        )
        return {"success": success, "message": message}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/admin/current-slots")
async def get_current_slot_info(session: Dict[str, Any] = Depends(require_admin_access)):
    """Get current active slot and next slot information"""
    try:
        manager = create_slot_manager_instance()
        current_slot = manager.get_current_slot()
        next_slot = manager.get_next_slot()
        
        return {
            "success": True,
            "current_slot": current_slot,
            "next_slot": next_slot,
            "all_slots": manager.attendance_slots
        }
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/admin/reload-slot-config")
async def reload_slot_configuration(session: Dict[str, Any] = Depends(require_admin_access)):
    """Reload slot configuration from database"""
    try:
        manager = create_slot_manager_instance()
        manager.reload_config()
        return {"success": True, "message": "Slot configuration reloaded successfully"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/holidays")
async def add_holiday_api(holiday_data: Holiday):
    """Add a new holiday"""
    try:
        success, message = attendance_system.add_holiday(
            holiday_data.date,
            holiday_data.name,
            holiday_data.type
        )
        return {"success": success, "message": message}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.delete("/api/holidays/{holiday_id}")
async def delete_holiday_api(holiday_id: int):
    """Delete a holiday"""
    try:
        success, message = attendance_system.delete_holiday(holiday_id)
        return {"success": success, "message": message}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.put("/api/students/{student_id}")
async def update_student(student_id: int, data: dict = Body(...)):
    """Update student details including joining date"""
    try:
        cursor = attendance_system.conn.cursor()
        # Only update fields that are present
        fields = []
        values = []
        for key in ["name", "email", "student_id", "joining_date"]:
            if key in data:
                fields.append(f"{key} = ?")
                values.append(data[key])
        if not fields:
            return {"success": False, "message": "No fields to update"}
        values.append(student_id)   
        sql = f"UPDATE students SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(sql, values)
        attendance_system.conn.commit()
        return {"success": True, "message": "Student updated successfully"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.delete("/api/students/{student_id}")
async def delete_student(student_id: int):
    """Delete a student and all related data"""
    try:
        cursor = attendance_system.conn.cursor()
        
        # Check if student exists
        cursor.execute('SELECT name FROM students WHERE id = ?', (student_id,))
        student = cursor.fetchone()
        
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")
        
        # Delete student's attendance records
        cursor.execute('DELETE FROM attendance WHERE student_id = ?', (student_id,))
        
        # Delete student's face encodings  
        cursor.execute('DELETE FROM face_encodings WHERE student_id = ?', (student_id,))
        
        # Delete the student
        cursor.execute('DELETE FROM students WHERE id = ?', (student_id,))
        
        attendance_system.conn.commit()
        
        # Reload face encodings after deletion
        attendance_system.load_student_faces()
        
        return {"success": True, "message": f"Student {student[0]} deleted successfully"}
        
    except Exception as e:
        return {"success": False, "message": f"Failed to delete student: {str(e)}"}

@app.post("/api/logout")
async def logout(response: Response, session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Secure logout with session cleanup"""
    try:
        if session:
            # Get session token from cookie
            session_token = None
            # In a real scenario, you'd extract this from the request
            # For now, we'll destroy all expired sessions
            SessionManager.cleanup_expired_sessions()
        
        # Clear the session cookie
        response.delete_cookie(
            key="session_token",
            httponly=True,
            secure=False,  # Set to True in production
            samesite="lax"
        )
        
        return {
            "success": True,
            "message": "Logged out successfully",
            "redirect_url": "/login"
        }
        
    except Exception as e:
        # Even if there's an error, clear the cookie
        response.delete_cookie(key="session_token")
        return {
            "success": True,
            "message": "Logged out successfully", 
            "redirect_url": "/login"
        }


@app.get("/logout")
async def logout_redirect(response: Response):
    """GET logout route for direct access"""
    response.delete_cookie(key="session_token")
    return RedirectResponse(url="/login")

@app.get("/api/session/status")
async def session_status(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Check session status with comprehensive user information"""
    if session:
        user_info = session["user_info"]
        return {
            "authenticated": True,
            "user_type": session["user_type"],
            "username": user_info.get("username", ""),
            "name": user_info.get("name", ""),
            "role": user_info.get("role", ""),
            "permissions": user_info.get("permissions", []),
            "login_time": user_info.get("login_time", ""),
            "expires_at": session["expires_at"].isoformat(),
            "last_activity": session["last_activity"].isoformat(),
            "active_sessions": SessionManager.get_active_sessions_count(),
            "session_valid": True
        }
    else:
        return {
            "authenticated": False,
            "session_valid": False,
            "message": "No active session",
            "redirect_required": True,
            "redirect_url": "/login"
        }



@app.get("/api/navigation/home")
async def navigate_home(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Smart home navigation based on user type"""
    from fastapi.responses import RedirectResponse
    
    if not session:
        return {"success": False, "redirect_url": "/login"}
    
    user_type = session.get("user_type", "")
    
    if user_type == "admin":
        return {"success": True, "redirect_url": "/dashboard", "message": "Redirecting to admin dashboard"}
    elif user_type == "user":
        return {"success": True, "redirect_url": "/attendance", "message": "You are already on your home page"}
    else:
        return {"success": False, "redirect_url": "/login", "message": "Invalid session"}
    

@app.post("/api/attendance/manual/session")
async def mark_manual_session_attendance_api(data: dict = Body(...)):
    """Mark session attendance manually"""
    try:
        success, message = attendance_system.mark_manual_session_attendance(
            data['student_id'],
            data['date'],
            data['session_type'],
            data.get('reason')
        )
        return {"success": success, "message": message}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/attendance/bulk-export")
async def bulk_export_attendance(
    start_date: str,
    end_date: str,
    format: str,
    include_weekends: bool = False,
    include_holidays: bool = False
):
    """Export bulk slot-based attendance data as CSV"""
    try:
        from fastapi.responses import StreamingResponse
        import csv
        
        start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        cursor = attendance_system.conn.cursor()
        
        # Get all students
        cursor.execute('SELECT id, name, student_id, email FROM students WHERE status = "active" ORDER BY name')
        students = cursor.fetchall()
        
        # Get holidays if not including them
        holiday_dates = []
        if not include_holidays:
            cursor.execute('SELECT date FROM holidays')
            holiday_dates = [datetime.strptime(row[0], '%Y-%m-%d').date() for row in cursor.fetchall()]
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        if format == 'daily_summary':  # FIXED: was 'daily'
            writer.writerow(['Date', 'Day', 'Total Students', 'Full Day Present', 'Half Day Present', 'Absent', 'Morning Sessions', 'Afternoon Sessions'])
            
            current_date = start_date_obj  # FIXED: use _obj version
            while current_date <= end_date_obj:  # FIXED: use _obj version
                if not include_weekends and current_date.weekday() == 6:
                    current_date += timedelta(days=1)
                    continue
                
                if current_date in holiday_dates:
                    current_date += timedelta(days=1)
                    continue
                
                date_str = current_date.strftime('%Y-%m-%d')
                day_name = current_date.strftime('%A')
                
                # Count morning sessions (FROM SLOT_ATTENDANCE)
                cursor.execute('''
                    SELECT COUNT(DISTINCT student_id) FROM slot_attendance 
                    WHERE date = ? AND slot_id = 'morning'
                ''', (date_str,))
                morning_count = cursor.fetchone()[0]
                
                # Count afternoon sessions (FROM SLOT_ATTENDANCE)
                cursor.execute('''
                    SELECT COUNT(DISTINCT student_id) FROM slot_attendance 
                    WHERE date = ? AND slot_id = 'afternoon'
                ''', (date_str,))
                afternoon_count = cursor.fetchone()[0]
                
                # Count students with both sessions
                cursor.execute('''
                    SELECT student_id FROM slot_attendance 
                    WHERE date = ? 
                    GROUP BY student_id 
                    HAVING COUNT(DISTINCT slot_id) = 2
                ''', (date_str,))
                full_day_records = cursor.fetchall()
                full_day_count = len(full_day_records)
                
                # Total unique students
                cursor.execute('''
                    SELECT COUNT(DISTINCT student_id) FROM slot_attendance 
                    WHERE date = ?
                ''', (date_str,))
                total_present_students = cursor.fetchone()[0]
                half_day_count = total_present_students - full_day_count
                
                absent_count = len(students) - total_present_students
                
                writer.writerow([
                    date_str, day_name, len(students),
                    full_day_count, half_day_count, absent_count,
                    morning_count, afternoon_count
                ])
                
                current_date += timedelta(days=1)
                
        elif format == 'student_summary':  # FIXED: was 'student'
            writer.writerow(['Student Name', 'Student ID', 'Email', 'Full Days', 'Half Days', 'Absent Days', 'Total Sessions', 'Attendance %'])
            
            for student in students:
                student_id, name, student_id_str, email = student
                
                # Get slot data for this student (FROM SLOT_ATTENDANCE)
                cursor.execute('''
                    SELECT date, COUNT(DISTINCT slot_id) as session_count
                    FROM slot_attendance 
                    WHERE student_id = ? AND date BETWEEN ? AND ?
                    GROUP BY date
                ''', (student_id, start_date, end_date))  # FIXED: use string versions
                
                daily_sessions = cursor.fetchall()
                
                full_days = sum(1 for _, count in daily_sessions if count == 2)
                half_days = sum(1 for _, count in daily_sessions if count == 1)
                
                # Get total session count
                cursor.execute('''
                    SELECT COUNT(*) FROM slot_attendance 
                    WHERE student_id = ? AND date BETWEEN ? AND ?
                ''', (student_id, start_date, end_date))  # FIXED: use string versions
                total_sessions = cursor.fetchone()[0]
                
                # Calculate working days
                total_working_days = 0
                current_date = start_date_obj  # FIXED: use _obj version
                while current_date <= end_date_obj:  # FIXED: use _obj version
                    if not include_weekends and current_date.weekday() == 6:
                        current_date += timedelta(days=1)
                        continue
                    if current_date in holiday_dates:
                        current_date += timedelta(days=1)
                        continue
                    total_working_days += 1
                    current_date += timedelta(days=1)
                
                absent_days = total_working_days - full_days - half_days
                effective_present_days = full_days + (half_days * 0.5)
                percentage = (effective_present_days / total_working_days * 100) if total_working_days > 0 else 0
                
                writer.writerow([
                    name, student_id_str, email,
                    full_days, half_days, absent_days, total_sessions,
                    f"{percentage:.1f}%"
                ])
                
        else:  # 'session_detailed' format
            writer.writerow(['Slot-Based Attendance Summary Report'])
            writer.writerow(['Date Range', f"{start_date} to {end_date}"])
            writer.writerow(['Total Students', len(students)])
            writer.writerow([])
            writer.writerow(['Date', 'Day', 'Full Day', 'Half Day', 'Absent', 'Morning', 'Afternoon', 'Attendance %'])
            
            current_date = start_date_obj  # FIXED: use _obj version
            while current_date <= end_date_obj:  # FIXED: use _obj version
                if not include_weekends and current_date.weekday() == 6:
                    current_date += timedelta(days=1)
                    continue
                if current_date in holiday_dates:
                    current_date += timedelta(days=1)
                    continue
                
                date_str = current_date.strftime('%Y-%m-%d')
                day_name = current_date.strftime('%A')
                
                # Same calculations using slot_attendance
                cursor.execute('SELECT COUNT(DISTINCT student_id) FROM slot_attendance WHERE date = ? AND slot_id = "morning"', (date_str,))
                morning_count = cursor.fetchone()[0]
                
                cursor.execute('SELECT COUNT(DISTINCT student_id) FROM slot_attendance WHERE date = ? AND slot_id = "afternoon"', (date_str,))
                afternoon_count = cursor.fetchone()[0]
                
                cursor.execute('SELECT student_id FROM slot_attendance WHERE date = ? GROUP BY student_id HAVING COUNT(DISTINCT slot_id) = 2', (date_str,))
                full_day_count = len(cursor.fetchall())
                
                cursor.execute('SELECT COUNT(DISTINCT student_id) FROM slot_attendance WHERE date = ?', (date_str,))
                total_present_students = cursor.fetchone()[0]
                half_day_count = total_present_students - full_day_count
                
                absent_count = len(students) - total_present_students
                effective_present = full_day_count + (half_day_count * 0.5)
                percentage = (effective_present / len(students) * 100) if len(students) > 0 else 0
                
                writer.writerow([
                    date_str, day_name, full_day_count, half_day_count, absent_count,
                    morning_count, afternoon_count, f"{percentage:.1f}%"
                ])
                
                current_date += timedelta(days=1)
        
        output.seek(0)
        filename = f"slot_attendance_bulk_{format}_{start_date}_{end_date}.csv"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")
    
    
@app.get("/api/students/{student_id}")
async def get_student_details(student_id: int):
    """Get individual student details including joining date"""
    try:
        cursor = attendance_system.conn.cursor()
        cursor.execute('''
            SELECT id, student_id, name, email, photo_count, verification_score, 
                   joining_date, created_at, status
            FROM students 
            WHERE id = ? AND status = "active"
        ''', (student_id,))
        
        student = cursor.fetchone()
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")
        
        return {
            "success": True,
            "student": {
                "id": student[0],
                "student_id": student[1],
                "name": student[2],
                "email": student[3],
                "photo_count": student[4] or 0,
                "verification_score": round(student[5] or 0, 3),
                "joining_date": student[6],
                "created_at": student[7],
                "status": student[8]
            }
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/attendance/export/{student_id}")
async def export_student_attendance(student_id: int):
    """Export individual student slot attendance as CSV"""
    try:
        from fastapi.responses import StreamingResponse
        import csv
        import io
        
        cursor = attendance_system.conn.cursor()
        
        # Get student info
        cursor.execute('SELECT name, student_id, email FROM students WHERE id = ?', (student_id,))
        student = cursor.fetchone()
        
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")
        
        student_name, student_id_str, email = student
        print(f"[DEBUG] Exporting for student: {student_name} (ID: {student_id})")
        
        # Debug: Check table structure
        cursor.execute("PRAGMA table_info(slot_attendance)")
        columns = cursor.fetchall()
        print(f"[DEBUG] slot_attendance columns: {[col[1] for col in columns]}")
        
        # Get slot attendance records
        cursor.execute('''
            SELECT date, slot_id, created_at
            FROM slot_attendance 
            WHERE student_id = ?
            ORDER BY date DESC, slot_id
        ''', (student_id,))
        
        slot_records = cursor.fetchall()
        print(f"[DEBUG] Found {len(slot_records)} slot records for export")
        
        # Create CSV content
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Headers
        writer.writerow([
            'Student Name', 'Student ID', 'Email', 'Date', 'Day', 
            'Session Type', 'Arrival Time', 'Status', 'Type', 'Reason'
        ])
        
        # Add slot records
        if slot_records:
            for record in slot_records:
                date_str, slot_id, created_at = record
                print(f"[DEBUG] Processing record: {date_str}, {slot_id}, {created_at}")
                
                try:
                    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                    day_name = date_obj.strftime('%A')
                except:
                    day_name = 'Unknown'
                
                # Convert slot_id to session_type
                session_type = 'Morning' if slot_id == 'morning' else 'Afternoon'
                
                writer.writerow([
                    student_name,
                    student_id_str,
                    email,
                    date_str,
                    day_name,
                    session_type,
                    created_at or '-',
                    'Present',
                    'Face Recognition',
                    '-'
                ])
        else:
            # Add a row indicating no data found
            writer.writerow([
                student_name, student_id_str, email, 
                'No Data', 'No attendance records found', 
                '-', '-', '-', '-', '-'
            ])
        
        output.seek(0)
        filename = f"slot_attendance_{student_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        print(f"[ERROR] Export failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")
    





# ADD THIS LINE HERE:
add_phase1_api_endpoints(app, attendance_system)




@app.get("/api/attendance/student/{student_id}/sessions")
async def get_student_session_attendance(student_id: int):
    """Get detailed session-based attendance data for a specific student"""
    try:
        data = attendance_system.get_student_attendance_data(student_id)
        return data
    except Exception as e:
        return {"success": False, "message": str(e)}
    


@app.get("/api/attendance/today/slots")
async def get_today_slot_attendance():
    """Get today's slot-based attendance (the working system)"""
    try:
        today = datetime.now().date()
        cursor = attendance_system.conn.cursor()
        
        cursor.execute('''
            SELECT s.name, s.student_id, s.email, 
                   sa_morning.created_at as morning_time,
                   sa_afternoon.created_at as afternoon_time
            FROM students s
            LEFT JOIN slot_attendance sa_morning ON s.id = sa_morning.student_id 
                AND sa_morning.date = ? AND sa_morning.slot_id = 'morning'
            LEFT JOIN slot_attendance sa_afternoon ON s.id = sa_afternoon.student_id 
                AND sa_afternoon.date = ? AND sa_afternoon.slot_id = 'afternoon'  
            WHERE s.status = 'active'
            ORDER BY s.name
        ''', (today, today))
        
        return cursor.fetchall()
        
    except Exception as e:
        print(f"Error loading slot attendance: {e}")
        return []

@app.get("/api/attendance/live-count")
async def get_live_attendance_count():
    """Get live student count with slot information"""
    try:
        manager = create_slot_manager_instance()
        count_data = manager.get_live_student_count()
        return count_data
    except Exception as e:
        print(f"Error in live count: {e}")
        return {
            "success": False,
            "message": str(e),
            "total_students": 0,
            "total_present": 0,
            "total_absent": 0,
            "current_slot": None,
            "next_slot": None,
            "last_updated": datetime.now().strftime('%H:%M:%S')
        }

@app.post("/api/detect_attendance_slots")
async def detect_attendance_with_slots(image_data: DetectionImage):
    """Enhanced detection with slot-based attendance marking"""
    if not FACE_RECOGNITION_AVAILABLE:
        return {"success": False, "message": "Face recognition not available"}
    
    try:
        # Convert base64 to image (same as existing detect_attendance)
        if image_data.image_data.startswith('data:image'):
            image_data_clean = image_data.image_data.split(',')[1]
        else:
            image_data_clean = image_data.image_data
        
        image_bytes = base64.b64decode(image_data_clean)
        image = Image.open(io.BytesIO(image_bytes))
        
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        image_array = np.array(image)
        
        # Use existing face detection
        detected_faces = asian_face_recognizer.detect_faces_optimized(image_array)
        
        if len(detected_faces) == 0:
            return {
                "success": False, 
                "message": "No faces detected",
                "faces_detected": 0
            }
        
        # Initialize slot manager
        manager = create_slot_manager_instance()
        recognized_students = []
        unknown_faces = 0
        
        for face_data in detected_faces:
            face_encoding = face_data['embedding']
            
            # Find best match (same logic as existing)
            if len(attendance_system.known_face_encodings) > 0:
                similarities = []
                for known_encoding in attendance_system.known_face_encodings:
                    face_norm = face_encoding / np.linalg.norm(face_encoding)
                    known_norm = known_encoding / np.linalg.norm(known_encoding)
                    similarity = np.dot(face_norm, known_norm)
                    similarities.append(similarity)
                
                best_match_index = np.argmax(similarities)
                best_similarity = similarities[best_match_index]
                
                RECOGNITION_THRESHOLD = 0.60
                
                if best_similarity > RECOGNITION_THRESHOLD:
                    student_id = attendance_system.known_face_ids[best_match_index]
                    student_name = attendance_system.known_face_names[best_match_index]
                    
                    # Use slot manager for attendance marking
                    attendance_result = manager.mark_attendance_with_slot(
                        student_id=student_id,
                        detection_confidence=best_similarity
                    )
                    
                    face_location = face_data['location']
                    
                    if attendance_result['success']:
                        # Successfully marked
                        recognized_students.append({
                            "student_id": student_id,
                            "name": student_name,
                            "confidence": float(best_similarity),
                            "status": "marked",
                            "message": attendance_result['message'],
                            "slot_name": attendance_result.get('slot_name', ''),
                            "location": {
                                "top": int(face_location[0]),
                                "right": int(face_location[1]),
                                "bottom": int(face_location[2]),
                                "left": int(face_location[3])
                            }
                        })
                    elif attendance_result.get('already_marked'):
                        # Already marked
                        recognized_students.append({
                            "student_id": student_id,
                            "name": student_name,
                            "confidence": float(best_similarity),
                            "status": "already_marked",
                            "message": attendance_result['message'],
                            "slot_name": attendance_result.get('slot_name', ''),
                            "location": {
                                "top": int(face_location[0]),
                                "right": int(face_location[1]),
                                "bottom": int(face_location[2]),
                                "left": int(face_location[3])
                            }
                        })
                    elif attendance_result.get('outside_slot'):
                        # Outside slot hours - return special response
                        return {
                            "success": False,
                            "faces_detected": len(detected_faces),
                            "recognized_students": [],
                            "unknown_faces": 0,
                            "outside_slot": True,
                            "face_detected": True,
                            "student_name": student_name,
                            "confidence": float(best_similarity),
                            "message": attendance_result['message'],
                            "next_slot": attendance_result.get('next_slot')
                        }
                else:
                    unknown_faces += 1
            else:
                unknown_faces += 1
        
        success = len(recognized_students) > 0
        message = f"Processed {len(detected_faces)} faces, recognized {len(recognized_students)} students"
        
        return {
            "success": success,
            "faces_detected": len(detected_faces),
            "recognized_students": recognized_students,
            "unknown_faces": unknown_faces,
            "message": message
        }
        
    except Exception as e:
        print(f"[ERROR] Slot detection failed: {str(e)}")
        return {
            "success": False,
            "message": f"Detection failed: {str(e)}",
            "faces_detected": 0
        }





if __name__ == "__main__":
    import uvicorn
    import os
    import subprocess
    import socket
    
    def get_host():
        """Automatically detect the appropriate host"""
        # Check if we can bind to the VM IP (means we're on VM)
        try:
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_socket.bind(("10.212.13.129", 0))  # Try to bind to VM IP
            test_socket.close()
            return "10.212.13.129"  # We're on the VM
        except:
            pass
        
        # Check for environment variable override
        if os.getenv("HOST"):
            return os.getenv("HOST")
        
        # Default to 0.0.0.0 (works everywhere)
        return "0.0.0.0"
    
    def get_display_host(actual_host):
        """Get the host to display in URLs"""
        if actual_host == "0.0.0.0":
            try:
                # Get local IP for display
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                return local_ip
            except:
                return "localhost"
        return actual_host
    
    # Determine host automatically
    host = get_host()
    display_host = get_display_host(host)
    port = int(os.getenv("PORT", 8000))
    
    # SSL certificate files
    cert_file = "cert.pem"
    key_file = "key.pem"
    
    # Generate self-signed certificate if it doesn't exist
    if not os.path.exists(cert_file) or not os.path.exists(key_file):
        print("🔧 Generating SSL certificates...")
        try:
            # Create self-signed certificate with dynamic host
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:4096", 
                "-keyout", key_file, "-out", cert_file, "-days", "365", "-nodes",
                "-subj", f"/C=IN/ST=Maharashtra/L=Mumbai/O=CDAC/CN={display_host}"
            ], check=True)
            print("✅ SSL certificates generated!")
        except subprocess.CalledProcessError:
            print("❌ Failed to generate SSL certificates. Install OpenSSL first.")
            print(f"📊 Running on HTTP: http://{display_host}:{port}/")
            uvicorn.run("main_with_face_recognition:app", host=host, port=port)
            exit()
    
    # Run with HTTPS
    print(f"🔒 HTTPS Dashboard: https://{display_host}:{port}/")
    print("⚠️  You may see a security warning - click 'Advanced' → 'Proceed to site (unsafe)'")
    print("💡 Tip: Bookmark the HTTPS URL to avoid the warning next time")
    
    try:
        uvicorn.run(
            "main_with_face_recognition:app", 
            host=host, 
            port=port,
            ssl_keyfile=key_file,
            ssl_certfile=cert_file
        )
    except Exception as e:
        print(f"❌ HTTPS failed: {e}")
        print(f"📊 Falling back to HTTP: http://{display_host}:{port}/")
        uvicorn.run("main_with_face_recognition:app", host=host, port=port)