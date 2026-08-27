from fastapi import FastAPI, HTTPException, Request, Body, Depends, Cookie, Response, UploadFile, File
from typing import Optional, Dict, Any, List
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr
import sys
from fastapi.responses import HTMLResponse
from photo_utils import create_student_photo_directory, get_student_photo_path
import os
from db import get_connection, is_postgres
import base64
import json
import uuid
from datetime import datetime, timedelta, date
from datetime import time
from typing import Optional, List
from enum import Enum
import numpy as np
from PIL import Image
import io
from camera_manager import camera_manager
from asian_face_model import asian_face_recognizer
import secrets
import hashlib
from phase1_integration import enhance_existing_attendance_system, add_phase1_api_endpoints
from attendance_manager import create_slot_manager_instance
import pytz
import csv
from io import StringIO
from analytics_manager import AnalyticsManager
from anti_spoofing import anti_spoof_checker

# Initialize managers.
# NOTE: the slot manager reads base tables (courses, session_configs) that are
# created by AttendanceSystem() below, so it is initialized AFTER that (see the
# `attendance_manager = create_slot_manager_instance()` line following
# `attendance_system = AttendanceSystem()`). A placeholder is set here so any
# import-time references resolve; it is replaced before any request is served.
attendance_manager = None
analytics_manager = AnalyticsManager()

# Convert to a specific timezone (e.g., Asia/Kolkata)
timezone = pytz.timezone('Asia/Kolkata')
localized_time = timezone.localize(datetime(2025, 8, 1))


# Add system path for OpenCV
sys.path.insert(0, '/usr/lib/python3/dist-packages')

# Import libraries with fallbacks
try:
    import cv2
    OPENCV_AVAILABLE = True
    print("[OK] OpenCV available")
except ImportError:
    OPENCV_AVAILABLE = False
    print("[ERROR] OpenCV not available")

from asian_face_model import INSIGHTFACE_AVAILABLE as FACE_RECOGNITION_AVAILABLE
import numpy as np

if FACE_RECOGNITION_AVAILABLE:
    print("[OK] Face recognition available (InsightFace)")
else:
    print("[ERROR] Face recognition not available - using basic mode")

class ManualAttendance(BaseModel):
    student_id: int
    date: str
    reason: Optional[str] = None

class Holiday(BaseModel):
    date: str
    name: str
    type: str
    course_id: Optional[int] = None  # None = applies to all batches

class SessionType(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"

class CourseCreate(BaseModel):
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None
    teacher_ids: Optional[List[int]] = None
    terminal_pin: Optional[str] = None

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
SESSION_TIMEOUT_HOURS = 24

# Dedicated connection for session storage (persistent across restarts).
_session_conn = get_connection(dict_rows=True)
_session_conn.execute(
    """CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_type TEXT NOT NULL,
            user_info TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
)
_session_conn.commit()


class SessionManager:
    """DB-backed sessions so a server restart no longer logs everyone out."""

    @staticmethod
    def create_session(user_type: str, user_info: dict) -> str:
        """Create a new session and return session token"""
        session_token = secrets.token_urlsafe(32)
        now = datetime.now()
        expires_at = now + timedelta(hours=SESSION_TIMEOUT_HOURS)

        _session_conn.execute(
            """INSERT INTO sessions (token, user_type, user_info, created_at, expires_at, last_activity)
                   VALUES (?, ?, ?, ?, ?, ?)""",
            (session_token, user_type, json.dumps(user_info),
             now.isoformat(), expires_at.isoformat(), now.isoformat()),
        )
        _session_conn.commit()

        print(f"[AUTH] Session created for {user_type}: {user_info.get('name', user_info.get('username', 'Unknown'))}")
        return session_token

    @staticmethod
    def validate_session(session_token: str) -> Optional[Dict[str, Any]]:
        """Validate session token and return session data if valid"""
        if not session_token:
            return None

        row = _session_conn.execute(
            "SELECT * FROM sessions WHERE token = ?", (session_token,)
        ).fetchone()
        if not row:
            return None

        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now() > expires_at:
            _session_conn.execute("DELETE FROM sessions WHERE token = ?", (session_token,))
            _session_conn.commit()
            return None

        # Update last activity
        now = datetime.now()
        _session_conn.execute(
            "UPDATE sessions SET last_activity = ? WHERE token = ?",
            (now.isoformat(), session_token),
        )
        _session_conn.commit()

        return {
            "user_type": row["user_type"],
            "user_info": json.loads(row["user_info"]),
            "created_at": row["created_at"],
            "expires_at": expires_at,       # datetime
            "last_activity": now,           # datetime (consumers call .isoformat())
        }

    @staticmethod
    def destroy_session(session_token: str) -> bool:
        """Destroy a session"""
        cur = _session_conn.execute(
            "DELETE FROM sessions WHERE token = ?", (session_token,)
        )
        _session_conn.commit()
        if cur.rowcount:
            print("[AUTH] Session destroyed")
            return True
        return False

    @staticmethod
    def get_active_sessions_count() -> int:
        """Get count of active (non-expired) sessions"""
        SessionManager.cleanup_expired_sessions()
        row = _session_conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return row["n"] if row else 0

    @staticmethod
    def cleanup_expired_sessions():
        """Remove expired sessions"""
        cur = _session_conn.execute(
            "DELETE FROM sessions WHERE expires_at < ?", (datetime.now().isoformat(),)
        )
        _session_conn.commit()
        if cur.rowcount:
            print(f"[AUTH] Cleaned up {cur.rowcount} expired session(s)")

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
    """Allow both user and admin access (legacy 'user' role + admin + teacher)."""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_type = session.get("user_type")
    if user_type not in ["admin", "user", "teacher"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return session


def require_teacher_or_admin(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Allow teachers and admins (attendance/analytics/holiday/bulk-mark actions)."""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    if session.get("user_type") not in ["admin", "teacher"]:
        raise HTTPException(status_code=403, detail="Teacher or admin access required")
    return session


def require_student(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Require a logged-in student (own-stats portal)."""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    if session.get("user_type") != "student":
        raise HTTPException(status_code=403, detail="Student access required")
    return session


def require_terminal(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Require an attendance-terminal session (kiosk for one batch)."""
    if not session:
        raise HTTPException(status_code=401, detail="Terminal not signed in")
    if session.get("user_type") != "terminal":
        raise HTTPException(status_code=403, detail="Terminal access required")
    return session


def teacher_allowed_course_ids(session: Dict[str, Any]) -> Optional[List[int]]:
    """Course ids a session may act on.

    Returns None for admins (meaning 'all batches'); for a teacher returns the
    list of assigned course ids (possibly empty).
    """
    if session.get("user_type") == "admin":
        return None  # all batches
    user_id = session.get("user_info", {}).get("id")
    if not user_id:
        return []
    rows = attendance_system.conn.execute(
        "SELECT course_id FROM teacher_batches WHERE user_id = ?", (user_id,)
    ).fetchall()
    return [r[0] for r in rows]


def assert_course_allowed(session: Dict[str, Any], course_id: int):
    """Raise 403 if a teacher tries to act on a batch they aren't assigned to."""
    allowed = teacher_allowed_course_ids(session)
    if allowed is None:
        return
    if int(course_id) not in allowed:
        raise HTTPException(status_code=403, detail="Not assigned to this batch")



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
        self.conn = get_connection()
        self.load_student_faces()
        self.init_extended_tables()
        self.init_advanced_tables()
    
    def load_student_faces(self):
        """Load all student face encodings from database with dimension detection"""
        if not hasattr(asian_face_recognizer, 'use_insightface') or not asian_face_recognizer.use_insightface:
            print("[WARN]  buffalo_l model not available")
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
                print(f"[STATS] Loaded {len(self.known_face_encodings)} student faces (InsightFace 512D)")
            elif most_common_dim == 128:
                self.embedding_method = "face_recognition"
                print(f"[STATS] Loaded {len(self.known_face_encodings)} student faces (face_recognition 128D)")
            else:
                print(f"[WARN]  Unknown embedding dimension: {most_common_dim}")
                self.embedding_method = "unknown"
        else:
            print("[STATS] No student faces loaded")
    
    def start_registration_session(self, name: str, email: str, student_id: str):
        """Start a new registration session"""
        session_id = str(uuid.uuid4())
        
        # Check if student already exists. A pre-loaded (CSV) student with no
        # face yet is allowed to register — we UPDATE that row on completion.
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT id, face_encoding, name, email FROM students WHERE student_id = ? OR email = ?',
            (student_id, email),
        )
        existing = cursor.fetchone()
        if existing and existing[1] is not None:
            return None, "Student already registered with this ID or email"

        # If a pending student exists, reuse their stored name/email
        if existing:
            name = name or existing[2]
            email = email or existing[3]

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
            
            # --- ANTI-SPOOFING GATE (Registration) ---
            # Registration is done in person by staff, and the liveness model is
            # erratic on real faces, so liveness is SKIPPED here by default.
            # (Attendance detection still enforces liveness.) To enforce it during
            # registration too, set ANTISPOOF_ON_REGISTRATION=1.
            if os.getenv("ANTISPOOF_ON_REGISTRATION", "").strip() in ("1", "true", "True", "yes"):
                liveness = anti_spoof_checker.check(image_array, face_data['location'])
                if not liveness['is_real']:
                    return None, f"Liveness check failed — live face required (score: {liveness['score']:.2f}). Photos and screens are not accepted."
            # --- END ANTI-SPOOFING GATE ---
            
            face_encoding = face_data['embedding']
            face_locations = [face_data['location']]  # For compatibility

            print(f"[DEBUG] 🎯 REGISTRATION: Generated {len(face_encoding)}D embedding")
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
                # Use cosine distance for InsightFace
                distances = []
                for encoding in encodings:
                    face_norm = encoding / np.linalg.norm(encoding)
                    avg_norm = average_encoding / np.linalg.norm(average_encoding)
                    similarity = np.dot(face_norm, avg_norm)
                    distances.append(1.0 - similarity)
                verification_score = 1.0 - np.mean(distances)
            else:
                verification_score = 0.8  # Default score
            
            # If the student was pre-loaded (CSV onboarding), UPDATE that row so
            # we don't create a duplicate; otherwise INSERT a new student.
            cursor.execute(
                'SELECT id FROM students WHERE student_id = ?',
                (student_data['student_id'],),
            )
            existing = cursor.fetchone()

            if existing:
                new_student_id = existing[0]
                cursor.execute('''
                    UPDATE students
                    SET face_encoding = ?, photo_count = ?, verification_score = ?,
                        status = 'active', registration_date = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (
                    average_encoding.tobytes(),
                    photos_uploaded,
                    verification_score,
                    new_student_id,
                ))
                # Clear any stale per-photo encodings for this student
                cursor.execute('DELETE FROM face_encodings WHERE student_id = ?', (new_student_id,))
            else:
                cursor.execute('''
                    INSERT INTO students
                    (student_id, name, email, face_encoding, photo_count, verification_score, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'active')
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
        SERIAL = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        
        # Create holidays table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS holidays (
                id {SERIAL},
                date DATE NOT NULL UNIQUE,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Create course_settings table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS course_settings (
                id {SERIAL},
                academic_year TEXT NOT NULL,
                semester TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Add new columns to attendance table if they don't exist
        from db import column_exists
        if not column_exists(self.conn, 'attendance', 'manual_reason'):
            cursor.execute('ALTER TABLE attendance ADD COLUMN manual_reason TEXT')
        if not column_exists(self.conn, 'attendance', 'is_manual'):
            cursor.execute('ALTER TABLE attendance ADD COLUMN is_manual BOOLEAN DEFAULT FALSE')
        self.conn.commit()
    
    def init_advanced_tables(self):
        """Initialize advanced tables for course and session management"""
        cursor = self.conn.cursor()
        SERIAL = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        
        # Courses table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS courses (
                id {SERIAL},
                name TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                description TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Session configurations table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS session_configs (
                id {SERIAL},
                course_id INTEGER,
                session_type TEXT NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                FOREIGN KEY (course_id) REFERENCES courses (id)
            )
        ''')
        
        # Session attendance table
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS session_attendance (
                id {SERIAL},
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
        
        # Add columns to existing attendance table if they don't exist
        from db import column_exists
        if not column_exists(self.conn, 'attendance', 'session_type'):
            cursor.execute("ALTER TABLE attendance ADD COLUMN session_type TEXT DEFAULT 'morning'")
        if not column_exists(self.conn, 'attendance', 'is_late'):
            cursor.execute('ALTER TABLE attendance ADD COLUMN is_late BOOLEAN DEFAULT FALSE')
        
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
                VALUES (?, ?, ?, ?), (?, ?, ?, ?), (?, ?, ?, ?), (?, ?, ?, ?)
            ''', (
                    course_id, 'morning_1', '08:30:00', '09:30:00',
                    course_id, 'morning_2', '11:00:00', '11:15:00',
                    course_id, 'afternoon_1', '13:45:00', '14:00:00',
                    course_id, 'afternoon_2', '16:15:00', '16:45:00'
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
            FROM session_configs
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
                VALUES (?, ?, ?, ?), (?, ?, ?, ?), (?, ?, ?, ?), (?, ?, ?, ?)
            ''', (
                course_id, 'morning_1', '08:30:00', '09:30:00',
                course_id, 'morning_2', '11:00:00', '11:15:00',
                course_id, 'afternoon_1', '13:45:00', '14:00:00',
                course_id, 'afternoon_2', '16:15:00', '16:45:00'
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
            
            slot_summary[date_str][slot_id] = time_marked

        # Calculate attendance for each day
        full_days = 0
        partial_days = 0
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
                slot_count = len([s for s in sessions.keys() if s.startswith('morning') or s.startswith('afternoon')])
                
                if slot_count == 4:
                    attendance_dict[date_str] = 'present'  # Full day
                    full_days += 1
                elif slot_count > 0:
                    attendance_dict[date_str] = 'partial'  # Partial day
                    partial_days += 1
                else:
                    attendance_dict[date_str] = 'absent'
            else:
                attendance_dict[date_str] = 'absent'
                
            current_date += timedelta(days=1)

        absent_days = total_working_days - full_days - partial_days
        
        # Calculate percentage based on total slots attended
        total_slots_attended = sum(
            len([s for s in slot_summary.get(d, {}).keys() if s.startswith('morning') or s.startswith('afternoon')]) 
            for d in attendance_dict.keys()
        )
        expected_slots = total_working_days * 4
        attendance_percentage = (total_slots_attended / expected_slots * 100) if expected_slots > 0 else 0

        print(f"[DEBUG] Stats - Full days: {full_days}, Partial days: {partial_days}, Absent: {absent_days}, Total working: {total_working_days}, Percentage: {attendance_percentage:.1f}%")

        # Add session details to attendance_dict for calendar display
        attendance_with_sessions = {}
        for date_str, status in attendance_dict.items():
            sess = slot_summary.get(date_str, {})
            slot_count = len([s for s in sess.keys() if s.startswith('morning') or s.startswith('afternoon')])
            
            attendance_with_sessions[date_str] = {
                'status': status,
                'count': slot_count,
                'm1': sess.get('morning_1'),
                'm2': sess.get('morning_2'),
                'a1': sess.get('afternoon_1'),
                'a2': sess.get('afternoon_2')
            }

        return {
            'success': True,
            'attendance': attendance_with_sessions,
            'stats': {
                'full_days': full_days,
                'half_days': partial_days,  # Kept as half_days to not break frontend var name blindly
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
        """Mark session attendance manually - FIXED to use slot_attendance and handle full day"""
        cursor = self.conn.cursor()
        
        # Check if holiday
        cursor.execute('SELECT id FROM holidays WHERE date = ?', (date_str,))
        if cursor.fetchone():
            return False, "Cannot mark attendance on a holiday"
        
        timezone = pytz.timezone('Asia/Kolkata')
        now = datetime.now(timezone)
        current_time = now.strftime('%H:%M:%S')
        current_timestamp = now.strftime('%Y-%m-%d %H:%M:%S')

        slots_to_mark = []
        if session_type == 'full_day':
            slots_to_mark = ['morning_1', 'morning_2', 'afternoon_1', 'afternoon_2']
        else:
            slots_to_mark = [session_type]
            
        marked_count = 0
        already_marked = 0
        
        for slot in slots_to_mark:
            # Check if already marked for this session in slot_attendance
            cursor.execute('''
                SELECT id FROM slot_attendance 
                WHERE student_id = ? AND date = ? AND slot_id = ?
            ''', (student_id, date_str, slot))
            
            if cursor.fetchone():
                already_marked += 1
                continue
            
            # Mark session attendance in slot_attendance table
            cursor.execute('''
                INSERT INTO slot_attendance 
                (student_id, date, slot_id, time_marked, is_manual, manual_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (student_id, date_str, slot, current_time, True, reason, current_timestamp))
            marked_count += 1
        
        self.conn.commit()
        
        if marked_count > 0:
            msg = f"Successfully marked {marked_count} session(s)"
            if already_marked > 0:
                msg += f" ({already_marked} were already marked)"
            return True, msg
        else:
            return False, "All selected sessions were already marked for this date"
        
    def get_student_count(self):
        """Get total number of active students"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM students WHERE status = "active"')
        return cursor.fetchone()[0]
           


    def add_holiday(self, date_str: str, name: str, holiday_type: str, course_id: int = None):
        """Add a holiday (course_id None = applies to all batches)."""
        cursor = self.conn.cursor()
        try:
            cursor.execute('INSERT INTO holidays (date, name, type, course_id) VALUES (?, ?, ?, ?)',
                          (date_str, name, holiday_type, course_id))
            self.conn.commit()
            return True, "Holiday added successfully"
        except Exception as e:
            return False, f"Holiday already exists: {str(e)}"

    def get_holidays(self, course_ids=None):
        """Get holidays. If course_ids is a list, return global holidays plus those batches'."""
        cursor = self.conn.cursor()
        cursor.execute('''SELECT h.id, h.date, h.name, h.type, h.course_id, c.name
                          FROM holidays h LEFT JOIN courses c ON c.id = h.course_id
                          ORDER BY h.date DESC''')
        rows = cursor.fetchall()
        holidays = []
        for h in rows:
            if course_ids is not None and h[4] is not None and h[4] not in course_ids:
                continue
            holidays.append({
                'id': h[0], 'date': h[1], 'name': h[2], 'type': h[3],
                'course_id': h[4], 'batch': h[5] or 'All batches',
            })
        return {'success': True, 'holidays': holidays}
        
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

        # Get attendance records from the primary `attendance` table
        cursor.execute("""
            SELECT date, session_type, time_in
            FROM attendance
            WHERE student_id = ?
            ORDER BY date
        """, (student_id,))
        session_records = cursor.fetchall()
        print(f"[DEBUG] Found {len(session_records)} attendance records")

        # Get holidays
        cursor.execute("SELECT date, name, type FROM holidays ORDER BY date")
        holidays = cursor.fetchall()
        holiday_dates = []
        for h in holidays:
            try:
                holiday_dates.append(datetime.strptime(h[0], '%Y-%m-%d').date())
            except:
                continue

        # Process attendance into morning/afternoon buckets per date
        attendance_dict = {}
        session_summary = {}

        for record in session_records:
            date_str, session_type, arrival_time = str(record[0])[:10], record[1], record[2]
            if date_str not in session_summary:
                session_summary[date_str] = {}
            st = (session_type or "").lower()
            if "morning" in st:
                session_summary[date_str]["morning"] = arrival_time
            elif "afternoon" in st:
                session_summary[date_str]["afternoon"] = arrival_time
            else:
                # whole-day / face mark counts for both halves
                session_summary[date_str].setdefault("morning", arrival_time)
                session_summary[date_str].setdefault("afternoon", arrival_time)

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

# Now that base tables (courses, session_configs, students, …) exist, build the
# slot manager (it reads those tables at construction time).
attendance_manager = create_slot_manager_instance()

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

from auth_utils import verify_password, hash_password, default_student_password

class StudentLogin(BaseModel):
    student_id: str
    password: str


def authenticate_user(username: str, password: str, roles: List[str]) -> Optional[dict]:
    """Authenticate an admin/teacher against the users table.

    Returns a user_info dict on success, else None.
    """
    row = attendance_system.conn.execute(
        "SELECT id, username, name, password_hash, role, is_active, must_change_password "
        "FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if not row:
        return None
    uid, uname, name, pw_hash, role, is_active, must_change = row
    if not is_active or role not in roles:
        return None
    if not verify_password(password, pw_hash):
        return None
    return {
        "id": uid,
        "username": uname,
        "name": name or uname,
        "role": role,
        "must_change_password": bool(must_change),
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
        elif user_type == "teacher":
            return RedirectResponse(url="/teacher")
        elif user_type == "student":
            return RedirectResponse(url="/student")
        elif user_type == "terminal":
            return RedirectResponse(url="/attendance")
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

def _set_session_cookie(response: Response, session_token: str):
    response.set_cookie(
        key="session_token",
        value=session_token,
        max_age=SESSION_TIMEOUT_HOURS * 3600,
        httponly=True,
        secure=False,  # Set to True in production behind HTTPS with a trusted cert
        samesite="lax",
    )


@app.post("/api/admin-login")
async def simple_admin_login(login_data: SimpleAdminLogin, response: Response):
    """Staff login (admin or teacher), authenticated against the users table."""
    try:
        username = (login_data.username or "").strip()
        password = login_data.password or ""

        user_info = authenticate_user(username, password, roles=["admin", "teacher"])
        if not user_info:
            return {"success": False, "message": "Invalid username or password"}

        role = user_info["role"]
        session_token = SessionManager.create_session(role, user_info)
        _set_session_cookie(response, session_token)

        redirect = "/dashboard" if role == "admin" else "/teacher"
        if user_info.get("must_change_password"):
            redirect = "/change-password"

        return {
            "success": True,
            "message": f"{role.title()} login successful",
            "user_type": role,
            "username": username,
            "must_change_password": user_info.get("must_change_password", False),
            "redirect_url": redirect,
        }
    except Exception as e:
        return {"success": False, "message": f"Login failed: {str(e)}"}


@app.post("/api/user-login")
async def user_login(login_data: SimpleAdminLogin, response: Response):
    """Student login: roll number (student_id) + password, against students table."""
    try:
        student_id = (login_data.username or "").strip()
        password = login_data.password or ""

        if not student_id or not password:
            return {"success": False, "message": "Roll number and password are required"}

        row = attendance_system.conn.execute(
            "SELECT id, student_id, name, password_hash, status, course_id, "
            "must_change_password, face_encoding IS NOT NULL AS has_face "
            "FROM students WHERE student_id = ?",
            (student_id,),
        ).fetchone()

        if not row or not row[3] or not verify_password(password, row[3]):
            print(f"[ERROR] Failed student login attempt: {student_id}")
            return {"success": False, "message": "Invalid roll number or password"}

        if row[4] not in ("active", "pending_registration"):
            return {"success": False, "message": "Account is inactive. Contact admin."}

        user_info = {
            "id": row[0],
            "student_id": row[1],
            "name": row[2],
            "role": "student",
            "course_id": row[5],
            "must_change_password": bool(row[6]),
            "has_face": bool(row[7]),
        }
        session_token = SessionManager.create_session("student", user_info)
        _set_session_cookie(response, session_token)

        redirect = "/change-password" if user_info["must_change_password"] else "/student"
        print(f"[AUTH] Student session created for: {student_id}")

        return {
            "success": True,
            "message": "Student login successful",
            "user_type": "student",
            "username": student_id,
            "must_change_password": user_info["must_change_password"],
            "redirect_url": redirect,
        }
    except Exception as e:
        print(f"[ERROR] Student login error: {str(e)}")
        return {"success": False, "message": f"Login failed: {str(e)}"}

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
                db_id = attendance_system.known_face_ids[best_match_index]
                student_name = attendance_system.known_face_names[best_match_index]

                # Look up roll number / batch for a consistent student session
                srow = attendance_system.conn.execute(
                    "SELECT student_id, course_id, must_change_password FROM students WHERE id = ?",
                    (db_id,),
                ).fetchone()
                roll_no = srow[0] if srow else db_id
                course_id = srow[1] if srow else None
                must_change = bool(srow[2]) if srow else False

                user_info = {
                    "id": db_id,
                    "name": student_name,
                    "student_id": roll_no,
                    "role": "student",
                    "course_id": course_id,
                    "must_change_password": must_change,
                    "has_face": True,
                }

                session_token = SessionManager.create_session("student", user_info)
                _set_session_cookie(response, session_token)

                return {
                    "success": True,
                    "message": "Face login successful",
                    "student": {
                        "id": db_id,
                        "name": student_name,
                        "confidence": float(best_similarity)
                    },
                    "redirect_url": "/change-password" if must_change else "/student",
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


# ==================================================================
# Attendance Terminal (kiosk) — per-batch PIN (Phase 8)
# ==================================================================

class TerminalLogin(BaseModel):
    course_id: int
    pin: str


@app.get("/api/terminal/batches")
async def terminal_batches():
    """Public: list active batches that have a terminal PIN set (for the login picker)."""
    try:
        rows = attendance_system.conn.execute(
            "SELECT id, name FROM courses WHERE is_active = 1 AND terminal_pin_hash IS NOT NULL ORDER BY name"
        ).fetchall()
        return {"success": True, "batches": [{"id": r[0], "name": r[1]} for r in rows]}
    except Exception as e:
        return {"success": False, "message": str(e), "batches": []}


@app.post("/api/terminal-login")
async def terminal_login(data: TerminalLogin, response: Response):
    """Open the attendance terminal for a batch after verifying its PIN."""
    try:
        row = attendance_system.conn.execute(
            "SELECT name, terminal_pin_hash FROM courses WHERE id = ? AND is_active = 1",
            (data.course_id,),
        ).fetchone()
        if not row or not row[1]:
            return {"success": False, "message": "No terminal is set up for that batch"}
        if not verify_password((data.pin or "").strip(), row[1]):
            return {"success": False, "message": "Incorrect PIN"}

        user_info = {"role": "terminal", "course_id": data.course_id,
                     "name": f"Terminal · {row[0]}", "batch_name": row[0]}
        token = SessionManager.create_session("terminal", user_info)
        _set_session_cookie(response, token)
        return {"success": True, "message": "Terminal ready", "redirect_url": "/attendance"}
    except Exception as e:
        return {"success": False, "message": str(e)}


# Note: the batch terminal reuses the existing Live Attendance page (/attendance),
# scoped to the terminal session's batch — there is no separate terminal page.




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
async def registration_page(request: Request, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Student registration page"""
    return templates.TemplateResponse("register.html", {
        "request": request,
        "face_recognition_available": FACE_RECOGNITION_AVAILABLE
    })

@app.get("/attendance", response_class=HTMLResponse)
async def attendance_page(request: Request, session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Live attendance page (admins, teachers, operators, and batch terminals)."""
    if not session:
        return RedirectResponse(url="/login")
    if session.get("user_type") not in ("admin", "user", "teacher", "terminal"):
        raise HTTPException(status_code=403, detail="Access denied")
    info = session.get("user_info", {})
    return templates.TemplateResponse("attendance.html", {
        "request": request,
        "is_terminal": session.get("user_type") == "terminal",
        "terminal_batch": info.get("batch_name", ""),
    })

@app.get("/students", response_class=HTMLResponse)
async def students_page(request: Request, session: Dict[str, Any] = Depends(require_admin_access)):
    return templates.TemplateResponse("students.html", {
        "request": request,
        "face_recognition_available": FACE_RECOGNITION_AVAILABLE
    })

@app.get("/teacher", response_class=HTMLResponse)
async def teacher_page(request: Request, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Teacher portal (full build in Phase 5)."""
    return templates.TemplateResponse("teacher.html", {
        "request": request,
        "user_name": session.get("user_info", {}).get("name", "Teacher"),
    })

@app.get("/student", response_class=HTMLResponse)
async def student_page(request: Request, session: Dict[str, Any] = Depends(require_student)):
    """Student portal (full build in Phase 4)."""
    return templates.TemplateResponse("student.html", {
        "request": request,
        "user_name": session.get("user_info", {}).get("name", "Student"),
    })

@app.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request, session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    if not session:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("change_password.html", {
        "request": request,
        "user_name": session.get("user_info", {}).get("name", ""),
    })

@app.post("/api/change-password")
async def change_password_api(
    data: dict = Body(...),
    session: Optional[Dict[str, Any]] = Depends(get_current_session),
):
    """Change the current principal's password (student or staff)."""
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")

    current = (data.get("current_password") or "").strip()
    new = (data.get("new_password") or "").strip()
    if len(new) < 6:
        return {"success": False, "message": "New password must be at least 6 characters"}

    user_type = session.get("user_type")
    info = session.get("user_info", {})
    cur = attendance_system.conn.cursor()

    if user_type == "student":
        row = cur.execute("SELECT password_hash FROM students WHERE id = ?", (info.get("id"),)).fetchone()
        if not row or not verify_password(current, row[0]):
            return {"success": False, "message": "Current password is incorrect"}
        cur.execute(
            "UPDATE students SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(new), info.get("id")),
        )
    elif user_type in ("admin", "teacher"):
        row = cur.execute("SELECT password_hash FROM users WHERE id = ?", (info.get("id"),)).fetchone()
        if not row or not verify_password(current, row[0]):
            return {"success": False, "message": "Current password is incorrect"}
        cur.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(new), info.get("id")),
        )
    else:
        return {"success": False, "message": "Password change not supported for this account"}

    attendance_system.conn.commit()

    redirect = {"admin": "/dashboard", "teacher": "/teacher", "student": "/student"}.get(user_type, "/")
    return {"success": True, "message": "Password updated", "redirect_url": redirect}

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
        spoofed_faces = 0
        
        for face_data in detected_faces:
            # --- ANTI-SPOOFING GATE ---
            liveness = anti_spoof_checker.check(image_array, face_data['location'])
            if not liveness['is_real']:
                spoofed_faces += 1
                face_location = face_data['location']
                recognized_students.append({
                    "student_id": None,
                    "name": "SPOOF DETECTED",
                    "confidence": 0.0,
                    "status": "spoof_detected",
                    "message": f"Liveness check failed (score: {liveness['score']:.2f})",
                    "liveness_score": liveness['score'],
                    "location": {
                        "top": int(face_location[0]),
                        "right": int(face_location[1]),
                        "bottom": int(face_location[2]),
                        "left": int(face_location[3])
                    }
                })
                continue
            # --- END ANTI-SPOOFING GATE ---
            
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
                    timezone = pytz.timezone('Asia/Kolkata')
                    today = datetime.now(timezone).date()
                    current_time = datetime.now(timezone).strftime('%H:%M:%S')
                    cursor = attendance_system.conn.cursor()
                    cursor.execute('SELECT id FROM attendance WHERE student_id = ? AND date = ?', 
                                 (student_id, today))
                    
                    if cursor.fetchone():
                        status = "already_marked"
                        message = f"{student_name} already marked present today"
                    else:
                        # Mark attendance (carry the student's batch for per-batch views)
                        cursor.execute('''
                            INSERT INTO attendance (student_id, date, time_in, is_manual, course_id)
                            VALUES (?, ?, ?, ?, (SELECT course_id FROM students WHERE id = ?))
                        ''', (student_id, today, datetime.now().time().strftime('%H:%M:%S'), False, student_id))
                        
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
                   s.joining_date, s.status, s.course_id, c.name
            FROM students s
            LEFT JOIN attendance a ON s.id = a.student_id
            LEFT JOIN courses c ON c.id = s.course_id
            WHERE s.status IN ('active', 'pending_registration')
            GROUP BY s.id, s.student_id, s.name, s.email, s.photo_count, s.verification_score,
                     s.joining_date, s.status, s.course_id, c.name
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
                "status": row[9] or "active",
                "course_id": row[10],
                "batch": row[11] or "—",
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


@app.get("/api/student/me")
async def student_me(session: Dict[str, Any] = Depends(require_student)):
    """Self-scoped stats for the logged-in student (own data only)."""
    from datetime import date, timedelta
    try:
        sid = session.get("user_info", {}).get("id")
        cur = attendance_system.conn.cursor()

        srow = cur.execute(
            "SELECT s.id, s.student_id, s.name, s.email, s.status, s.joining_date, "
            "s.course_id, c.name, (s.face_encoding IS NOT NULL) "
            "FROM students s LEFT JOIN courses c ON c.id = s.course_id WHERE s.id = ?",
            (sid,),
        ).fetchone()
        if not srow:
            raise HTTPException(status_code=404, detail="Student not found")

        profile = {
            "id": srow[0], "student_id": srow[1], "name": srow[2], "email": srow[3],
            "status": srow[4], "batch": srow[7] or "—", "has_face": bool(srow[8]),
        }

        # Distinct present dates from the primary attendance table
        present_rows = cur.execute(
            "SELECT DISTINCT date FROM attendance WHERE student_id = ? ORDER BY date", (sid,)
        ).fetchall()
        present_dates = set()
        for r in present_rows:
            try:
                present_dates.add(datetime.strptime(str(r[0])[:10], "%Y-%m-%d").date())
            except (ValueError, TypeError):
                continue

        # Holidays (global + this student's batch)
        holiday_dates = set()
        for r in cur.execute(
            "SELECT date FROM holidays WHERE course_id IS NULL OR course_id = ?", (srow[6],)
        ).fetchall():
            try:
                holiday_dates.add(datetime.strptime(str(r[0])[:10], "%Y-%m-%d").date())
            except (ValueError, TypeError):
                continue

        # Determine the attendance window
        today = date.today()
        start_date = None
        if srow[5]:
            try:
                start_date = datetime.strptime(str(srow[5])[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                start_date = None
        if not start_date:
            start_date = min(present_dates) if present_dates else today - timedelta(days=29)

        # Count working days (Mon–Sat, excluding holidays) in [start_date, today]
        working_days = 0
        d = start_date
        while d <= today:
            if d.weekday() != 6 and d not in holiday_dates:  # 6 = Sunday
                working_days += 1
            d += timedelta(days=1)

        present_count = len([d for d in present_dates if start_date <= d <= today])
        rate = round(present_count / working_days * 100, 1) if working_days else 0.0
        absent_count = max(working_days - present_count, 0)

        # Recent history (latest 15 records)
        recent = []
        for r in cur.execute(
            "SELECT date, time_in, status, session_type, is_late, is_manual "
            "FROM attendance WHERE student_id = ? ORDER BY date DESC, time_in DESC LIMIT 15",
            (sid,),
        ).fetchall():
            recent.append({
                "date": str(r[0])[:10], "time_in": r[1], "status": r[2] or "present",
                "session_type": r[3], "is_late": bool(r[4]), "is_manual": bool(r[5]),
            })

        # 14-day sparkline: present(1)/holiday(-1)/absent(0) per day, oldest first
        sparkline = []
        for i in range(13, -1, -1):
            d = today - timedelta(days=i)
            if d.weekday() == 6 or d in holiday_dates:
                val = -1
            else:
                val = 1 if d in present_dates else 0
            sparkline.append({"date": d.strftime("%Y-%m-%d"), "value": val})

        return {
            "success": True,
            "profile": profile,
            "summary": {
                "attendance_rate": rate,
                "present_days": present_count,
                "absent_days": absent_count,
                "working_days": working_days,
                "since": start_date.strftime("%Y-%m-%d"),
            },
            "recent": recent,
            "sparkline": sparkline,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}



@app.get("/api/holidays")
async def get_holidays_api(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Get holidays (teachers see global + their assigned batches)."""
    try:
        course_ids = None
        if session and session.get("user_type") == "teacher":
            course_ids = teacher_allowed_course_ids(session)
        return attendance_system.get_holidays(course_ids)
    except Exception as e:
        return {"success": False, "message": str(e)}
    
@app.get("/api/admin/session-config")
async def get_session_configuration(course_id: Optional[int] = None,
                                    session: Dict[str, Any] = Depends(require_admin_access)):
    """Get session configuration for a batch (defaults to the first active batch)."""
    try:
        manager = create_slot_manager_instance()
        if course_id is None:
            row = attendance_system.conn.execute(
                "SELECT id FROM courses WHERE is_active = 1 ORDER BY id LIMIT 1"
            ).fetchone()
            course_id = row[0] if row else 1
        config = manager.get_session_configs(course_id)
        return {"success": True, "config": config, "course_id": course_id}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.put("/api/admin/session-config/{session_type}")
async def update_session_configuration(
    session_type: str,
    data: dict = Body(...),
    session: Dict[str, Any] = Depends(require_admin_access)
):
    """Update session timing configuration (scoped to a batch when course_id given)."""
    try:
        manager = create_slot_manager_instance()
        success, message = manager.update_session_timing(
            session_type=session_type,
            start_time=data['start_time'],
            end_time=data['end_time'],
            course_id=data.get('course_id')
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

@app.post("/api/admin/clear_all_data")
async def clear_all_data(session: Dict[str, Any] = Depends(require_admin_access)):
    """Clear all student data, attendance records, and face encodings"""
    try:
        cursor = attendance_system.conn.cursor()
        
        # Delete all records from tables in order to avoid foreign key issues
        cursor.execute('DELETE FROM slot_attendance')
        cursor.execute('DELETE FROM session_attendance')
        cursor.execute('DELETE FROM attendance')
        cursor.execute('DELETE FROM face_encodings')
        cursor.execute('DELETE FROM students')
        cursor.execute('DELETE FROM registration_sessions')
        cursor.execute('DELETE FROM daily_attendance_summary')
        
        attendance_system.conn.commit()
        
        # Reload face encodings (will be empty)
        attendance_system.load_student_faces()
        
        return {"success": True, "message": "All student data cleared successfully"}
    except Exception as e:
        attendance_system.conn.rollback()
        return {"success": False, "message": f"Failed to clear data: {str(e)}"}

# ==================================================================
# Batch / Course management (Phase 2)
# ==================================================================

DEFAULT_COURSE_SLOTS = [
    ("morning_1", "08:30:00", "09:30:00"),
    ("morning_2", "11:00:00", "11:15:00"),
    ("afternoon_1", "13:45:00", "14:00:00"),
    ("afternoon_2", "16:15:00", "16:45:00"),
]


class CourseUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    teacher_ids: Optional[List[int]] = None
    terminal_pin: Optional[str] = None  # "" clears the PIN; None leaves unchanged


class TeacherCreate(BaseModel):
    username: str
    name: Optional[str] = None
    password: str
    batch_ids: Optional[List[int]] = None


class TeacherUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None
    batch_ids: Optional[List[int]] = None


@app.get("/api/courses")
async def list_courses(session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """List batches. Admin sees all; teachers see only their assigned batches."""
    try:
        allowed = teacher_allowed_course_ids(session)  # None = all (admin)
        cur = attendance_system.conn.cursor()
        rows = cur.execute(
            "SELECT id, name, start_date, end_date, description, is_active, terminal_pin_hash "
            "FROM courses ORDER BY is_active DESC, id"
        ).fetchall()
        courses = []
        for r in rows:
            if allowed is not None and r[0] not in allowed:
                continue
            count = cur.execute(
                "SELECT COUNT(*) FROM students WHERE course_id = ?", (r[0],)
            ).fetchone()[0]
            teachers = cur.execute(
                "SELECT u.id, u.name, u.username FROM teacher_batches tb "
                "JOIN users u ON u.id = tb.user_id WHERE tb.course_id = ?",
                (r[0],),
            ).fetchall()
            courses.append({
                "id": r[0], "name": r[1], "start_date": r[2], "end_date": r[3],
                "description": r[4], "is_active": bool(r[5]), "student_count": count,
                "has_pin": bool(r[6]),
                "teachers": [{"id": t[0], "name": t[1] or t[2], "username": t[2]} for t in teachers],
            })
        return {"success": True, "courses": courses}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/courses")
async def create_course(data: CourseCreate, session: Dict[str, Any] = Depends(require_admin_access)):
    """Create a new batch and seed its default session slots."""
    try:
        name = (data.name or "").strip()
        if not name:
            return {"success": False, "message": "Batch name is required"}
        cur = attendance_system.conn.cursor()
        cur.execute(
            "SELECT id FROM courses WHERE LOWER(name) = LOWER(?)", (name,)
        )
        if cur.fetchone():
            return {"success": False, "message": f"A batch named '{name}' already exists"}

        # courses.start_date / end_date are NOT NULL — default when not supplied
        start_date = data.start_date or date.today().strftime("%Y-%m-%d")
        end_date = data.end_date or (date.today() + timedelta(days=365)).strftime("%Y-%m-%d")

        cur.execute(
            "INSERT INTO courses (name, start_date, end_date, description, is_active, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (name, start_date, end_date, data.description, datetime.now().isoformat()),
        )
        course_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO session_configs (course_id, session_type, start_time, end_time, is_active) "
            "VALUES (?, ?, ?, ?, 1)",
            [(course_id, st, s, e) for st, s, e in DEFAULT_COURSE_SLOTS],
        )
        # Assign teachers to the new batch
        for tid in (data.teacher_ids or []):
            if is_postgres():
                cur.execute(
                    "INSERT INTO teacher_batches (user_id, course_id) VALUES (?, ?) ON CONFLICT (user_id, course_id) DO NOTHING",
                    (tid, course_id),
                )
            else:
                cur.execute(
                    "INSERT OR IGNORE INTO teacher_batches (user_id, course_id) VALUES (?, ?)",
                    (tid, course_id),
                )
        # Set the terminal PIN if provided
        if data.terminal_pin:
            cur.execute(
                "UPDATE courses SET terminal_pin_hash = ? WHERE id = ?",
                (hash_password(data.terminal_pin.strip()), course_id),
            )
        attendance_system.conn.commit()
        return {"success": True, "message": f"Batch '{name}' created", "course_id": course_id}
    except Exception as e:
        attendance_system.conn.rollback()
        return {"success": False, "message": str(e)}


@app.put("/api/courses/{course_id}")
async def update_course(course_id: int, data: CourseUpdate, session: Dict[str, Any] = Depends(require_admin_access)):
    """Update a batch's details or active status."""
    try:
        cur = attendance_system.conn.cursor()
        if not cur.execute("SELECT id FROM courses WHERE id = ?", (course_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Batch not found")
        fields, values = [], []
        for key in ["name", "start_date", "end_date", "description"]:
            val = getattr(data, key)
            if val is not None:
                fields.append(f"{key} = ?")
                values.append(val)
        if data.is_active is not None:
            fields.append("is_active = ?")
            values.append(1 if data.is_active else 0)
        if data.terminal_pin is not None:
            # empty string clears the PIN; otherwise store a hash
            fields.append("terminal_pin_hash = ?")
            values.append(hash_password(data.terminal_pin.strip()) if data.terminal_pin.strip() else None)
        if fields:
            vals = values + [course_id]
            cur.execute(f"UPDATE courses SET {', '.join(fields)} WHERE id = ?", vals)

        # Replace teacher assignments if provided
        if data.teacher_ids is not None:
            cur.execute("DELETE FROM teacher_batches WHERE course_id = ?", (course_id,))
            for tid in data.teacher_ids:
                if is_postgres():
                    cur.execute(
                        "INSERT INTO teacher_batches (user_id, course_id) VALUES (?, ?) ON CONFLICT (user_id, course_id) DO NOTHING",
                        (tid, course_id),
                    )
                else:
                    cur.execute(
                        "INSERT OR IGNORE INTO teacher_batches (user_id, course_id) VALUES (?, ?)",
                        (tid, course_id),
                    )
        if not fields and data.teacher_ids is None:
            return {"success": False, "message": "No fields to update"}
        attendance_system.conn.commit()
        return {"success": True, "message": "Batch updated"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.delete("/api/courses/{course_id}")
async def delete_course(course_id: int, session: Dict[str, Any] = Depends(require_admin_access)):
    """Delete a batch (only if it has no students; otherwise deactivate instead)."""
    try:
        cur = attendance_system.conn.cursor()
        if not cur.execute("SELECT id FROM courses WHERE id = ?", (course_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Batch not found")
        count = cur.execute(
            "SELECT COUNT(*) FROM students WHERE course_id = ?", (course_id,)
        ).fetchone()[0]
        if count > 0:
            return {
                "success": False,
                "message": f"Batch has {count} student(s). Move or remove them first, or deactivate the batch instead.",
            }
        cur.execute("DELETE FROM session_configs WHERE course_id = ?", (course_id,))
        cur.execute("DELETE FROM teacher_batches WHERE course_id = ?", (course_id,))
        cur.execute("DELETE FROM courses WHERE id = ?", (course_id,))
        attendance_system.conn.commit()
        return {"success": True, "message": "Batch deleted"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


# ==================================================================
# Teacher account management (admin only)
# ==================================================================

@app.get("/api/teachers")
async def list_teachers(session: Dict[str, Any] = Depends(require_admin_access)):
    """List teacher accounts with their assigned batches."""
    try:
        cur = attendance_system.conn.cursor()
        teachers = []
        for r in cur.execute(
            "SELECT id, username, name, is_active FROM users WHERE role = 'teacher' ORDER BY username"
        ).fetchall():
            batches = cur.execute(
                "SELECT c.id, c.name FROM teacher_batches tb "
                "JOIN courses c ON c.id = tb.course_id WHERE tb.user_id = ?",
                (r[0],),
            ).fetchall()
            teachers.append({
                "id": r[0], "username": r[1], "name": r[2], "is_active": bool(r[3]),
                "batches": [{"id": b[0], "name": b[1]} for b in batches],
            })
        return {"success": True, "teachers": teachers}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/teachers")
async def create_teacher(data: TeacherCreate, session: Dict[str, Any] = Depends(require_admin_access)):
    """Create a teacher account and assign batches."""
    try:
        username = (data.username or "").strip()
        if not username or not data.password:
            return {"success": False, "message": "Username and password are required"}
        cur = attendance_system.conn.cursor()
        if cur.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            return {"success": False, "message": f"Username '{username}' already exists"}
        cur.execute(
            "INSERT INTO users (username, name, password_hash, role, is_active, must_change_password) "
            "VALUES (?, ?, ?, 'teacher', 1, 1)",
            (username, (data.name or username).strip(), hash_password(data.password)),
        )
        user_id = cur.lastrowid
        for cid in (data.batch_ids or []):
            if is_postgres():
                cur.execute(
                    "INSERT INTO teacher_batches (user_id, course_id) VALUES (?, ?) ON CONFLICT (user_id, course_id) DO NOTHING",
                    (user_id, cid),
                )
            else:
                cur.execute(
                    "INSERT OR IGNORE INTO teacher_batches (user_id, course_id) VALUES (?, ?)",
                    (user_id, cid),
                )
        attendance_system.conn.commit()
        return {"success": True, "message": f"Teacher '{username}' created", "user_id": user_id}
    except Exception as e:
        attendance_system.conn.rollback()
        return {"success": False, "message": str(e)}


@app.put("/api/teachers/{user_id}")
async def update_teacher(user_id: int, data: TeacherUpdate, session: Dict[str, Any] = Depends(require_admin_access)):
    """Update a teacher: name, active status, password reset, and batch assignments."""
    try:
        cur = attendance_system.conn.cursor()
        row = cur.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Teacher not found")

        fields, values = [], []
        if data.name is not None:
            fields.append("name = ?"); values.append(data.name.strip())
        if data.is_active is not None:
            fields.append("is_active = ?"); values.append(1 if data.is_active else 0)
        if data.password:
            fields.append("password_hash = ?"); values.append(hash_password(data.password))
            fields.append("must_change_password = ?"); values.append(1)
        if fields:
            values.append(user_id)
            cur.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)

        if data.batch_ids is not None:
            cur.execute("DELETE FROM teacher_batches WHERE user_id = ?", (user_id,))
            for cid in data.batch_ids:
                if is_postgres():
                    cur.execute(
                        "INSERT INTO teacher_batches (user_id, course_id) VALUES (?, ?) ON CONFLICT (user_id, course_id) DO NOTHING",
                        (user_id, cid),
                    )
                else:
                    cur.execute(
                        "INSERT OR IGNORE INTO teacher_batches (user_id, course_id) VALUES (?, ?)",
                        (user_id, cid),
                    )
        attendance_system.conn.commit()
        return {"success": True, "message": "Teacher updated"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.delete("/api/teachers/{user_id}")
async def delete_teacher(user_id: int, session: Dict[str, Any] = Depends(require_admin_access)):
    """Delete a teacher account and its batch assignments."""
    try:
        cur = attendance_system.conn.cursor()
        if not cur.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (user_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found")
        cur.execute("DELETE FROM teacher_batches WHERE user_id = ?", (user_id,))
        cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
        attendance_system.conn.commit()
        return {"success": True, "message": "Teacher deleted"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/admin/batches", response_class=HTMLResponse)
async def batches_page(request: Request, session: Dict[str, Any] = Depends(require_admin_access)):
    """Admin page for managing batches and teacher accounts."""
    return templates.TemplateResponse("batches.html", {"request": request})


@app.post("/api/holidays")
async def add_holiday_api(holiday_data: Holiday, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Add a new holiday (optionally scoped to a batch)."""
    try:
        # Teachers may only add holidays for their assigned batches (or global if allowed).
        if holiday_data.course_id is not None:
            assert_course_allowed(session, holiday_data.course_id)
        success, message = attendance_system.add_holiday(
            holiday_data.date,
            holiday_data.name,
            holiday_data.type,
            holiday_data.course_id,
        )
        return {"success": success, "message": message}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.delete("/api/holidays/{holiday_id}")
async def delete_holiday_api(holiday_id: int, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Delete a holiday (teachers only for their batches or global)."""
    try:
        allowed = teacher_allowed_course_ids(session)
        if allowed is not None:
            row = attendance_system.conn.execute(
                "SELECT course_id FROM holidays WHERE id = ?", (holiday_id,)
            ).fetchone()
            if row and row[0] is not None and row[0] not in allowed:
                raise HTTPException(status_code=403, detail="Not assigned to this batch")
        success, message = attendance_system.delete_holiday(holiday_id)
        return {"success": success, "message": message}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.put("/api/students/{student_id}")
async def update_student(student_id: int, data: dict = Body(...), session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Update student details including joining date"""
    try:
        cursor = attendance_system.conn.cursor()
        # Batch scope for teachers
        allowed = teacher_allowed_course_ids(session)
        if allowed is not None:
            row = cursor.execute("SELECT course_id FROM students WHERE id = ?", (student_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Student not found")
            if row[0] not in allowed:
                raise HTTPException(status_code=403, detail="Not assigned to this student's batch")
            # A teacher can't move a student into a batch they don't own
            if "course_id" in data and data["course_id"] not in allowed:
                raise HTTPException(status_code=403, detail="Cannot move student to a batch you aren't assigned to")
        # Only update fields that are present
        fields = []
        values = []
        for key in ["name", "email", "student_id", "joining_date", "course_id", "dob"]:
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
async def delete_student(student_id: int, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Delete a student and all related data (teachers limited to their batches)."""
    try:
        cursor = attendance_system.conn.cursor()

        # Check if student exists (+ batch scope for teachers)
        cursor.execute('SELECT name, course_id FROM students WHERE id = ?', (student_id,))
        student = cursor.fetchone()

        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        allowed = teacher_allowed_course_ids(session)
        if allowed is not None and student[1] not in allowed:
            raise HTTPException(status_code=403, detail="Not assigned to this student's batch")
        
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


@app.post("/api/students/{student_id}/reset-password")
async def reset_student_password(student_id: int, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Reset a student's password to their DOB default; they must change it next login."""
    try:
        cur = attendance_system.conn.cursor()
        row = cur.execute("SELECT course_id, dob FROM students WHERE id = ?", (student_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Student not found")
        allowed = teacher_allowed_course_ids(session)
        if allowed is not None and row[0] not in allowed:
            raise HTTPException(status_code=403, detail="Not assigned to this student's batch")
        new_pw = default_student_password(row[1])
        cur.execute(
            "UPDATE students SET password_hash = ?, must_change_password = 1 WHERE id = ?",
            (hash_password(new_pw), student_id),
        )
        attendance_system.conn.commit()
        return {"success": True, "message": f"Password reset to: {new_pw}", "password": new_pw}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/teachers/{user_id}/reset-password")
async def reset_teacher_password(user_id: int, data: dict = Body(default={}),
                                 session: Dict[str, Any] = Depends(require_admin_access)):
    """Admin resets a teacher's password (to a provided value or 'teacher@123')."""
    try:
        cur = attendance_system.conn.cursor()
        if not cur.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (user_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Teacher not found")
        new_pw = (data.get("password") or "").strip() or "teacher@123"
        cur.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
            (hash_password(new_pw), user_id),
        )
        attendance_system.conn.commit()
        return {"success": True, "message": f"Password reset to: {new_pw}", "password": new_pw}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/students/add")
async def add_single_student(data: dict = Body(...), session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Add one student (pending face registration). Teachers limited to their batches."""
    try:
        student_id = (data.get("student_id") or "").strip()
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip()
        dob = (data.get("dob") or "").strip()
        course_id = data.get("course_id")

        if not student_id or not name or not email:
            return {"success": False, "message": "Roll number, name and email are required"}
        if not course_id:
            return {"success": False, "message": "Batch is required"}

        assert_course_allowed(session, course_id)

        cur = attendance_system.conn.cursor()
        if cur.execute("SELECT id FROM students WHERE student_id = ? OR email = ?",
                       (student_id, email)).fetchone():
            return {"success": False, "message": "A student with this roll number or email already exists"}

        cur.execute('''
            INSERT INTO students
            (student_id, name, email, dob, course_id, password_hash, must_change_password, status)
            VALUES (?, ?, ?, ?, ?, ?, 1, 'pending_registration')
        ''', (student_id, name, email, dob or None, course_id,
              hash_password(default_student_password(dob))))
        attendance_system.conn.commit()
        return {"success": True, "message": f"Added {name} (pending face registration)"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/students/bulk-upload")
async def bulk_upload_students(file: UploadFile = File(...), session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Bulk upload student details from CSV (faces are registered later).

    CSV columns (header row required):
        student_id, name, email        -> required
        dob                            -> optional, becomes the default password (digits of DOB)
        batch                          -> optional, matched to a course/batch name (defaults to the default batch)
        joining_date                   -> optional
    Students are created with status 'pending_registration' and no face.
    """
    try:
        content = await file.read()
        text = content.decode('utf-8-sig')  # tolerate a BOM from Excel
        reader = csv.DictReader(StringIO(text))

        if not reader.fieldnames:
            return {"success": False, "message": "CSV file is empty"}

        required_fields = {'student_id', 'name', 'email'}
        csv_fields = {f.strip().lower() for f in reader.fieldnames}
        if not required_fields.issubset(csv_fields):
            missing = required_fields - csv_fields
            return {"success": False, "message": f"Missing required columns: {', '.join(missing)}"}

        cursor = attendance_system.conn.cursor()

        # Build a name -> id map of batches for resolving the 'batch' column.
        batch_by_name = {
            r[1].strip().lower(): r[0]
            for r in cursor.execute("SELECT id, name FROM courses").fetchall()
        }
        allowed = teacher_allowed_course_ids(session)  # None = admin (all)
        default_course_id = 1

        def get(row, key):
            # case-insensitive column access
            for k, v in row.items():
                if k and k.strip().lower() == key:
                    return (v or '').strip()
            return ''

        added_count = 0
        skipped_count = 0
        errors = []

        for row_num, row in enumerate(reader, start=2):
            try:
                student_id = get(row, 'student_id')
                name = get(row, 'name')
                email = get(row, 'email')
                dob = get(row, 'dob')
                joining_date = get(row, 'joining_date') or None
                batch_name = get(row, 'batch')

                if not student_id or not name or not email:
                    errors.append(f"Row {row_num}: Missing required fields (student_id, name, or email)")
                    skipped_count += 1
                    continue

                # Resolve batch
                if batch_name:
                    course_id = batch_by_name.get(batch_name.lower())
                    if not course_id:
                        errors.append(f"Row {row_num}: Unknown batch '{batch_name}'")
                        skipped_count += 1
                        continue
                else:
                    course_id = default_course_id

                # Teachers may only onboard into their assigned batches
                if allowed is not None and course_id not in allowed:
                    errors.append(f"Row {row_num}: Not assigned to batch '{batch_name or 'default'}'")
                    skipped_count += 1
                    continue

                # Duplicate check (student_id or email must be unique)
                cursor.execute(
                    'SELECT id FROM students WHERE student_id = ? OR email = ?',
                    (student_id, email),
                )
                if cursor.fetchone():
                    errors.append(f"Row {row_num}: Student ID '{student_id}' or email already exists")
                    skipped_count += 1
                    continue

                cursor.execute('''
                    INSERT INTO students
                    (student_id, name, email, joining_date, dob, course_id,
                     password_hash, must_change_password, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'pending_registration')
                ''', (
                    student_id, name, email, joining_date, dob or None, course_id,
                    hash_password(default_student_password(dob)),
                ))
                added_count += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
                skipped_count += 1

        attendance_system.conn.commit()

        message = f"[OK] Added {added_count} student(s) (pending face registration)"
        if skipped_count > 0:
            message += f", [WARN] Skipped {skipped_count}"

        return {
            "success": True,
            "message": message,
            "added": added_count,
            "skipped": skipped_count,
            "errors": errors[:10],
        }

    except Exception as e:
        return {"success": False, "message": f"Failed to upload students: {str(e)}", "added": 0, "skipped": 0, "errors": []}

@app.get("/api/students/bulk-upload/template")
async def get_bulk_upload_template(session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Download CSV template for bulk student upload"""
    try:
        csv_content = (
            "student_id,name,email,dob,batch,joining_date\n"
            "250840325001,John Doe,john.doe@email.com,2001-05-14,PGCP-BDA,2026-08-01\n"
            "250840325002,Jane Smith,jane.smith@email.com,2002-11-02,PGCP-AI,2026-08-01\n"
            "250840325003,Alex Johnson,alex.johnson@email.com,2000-03-28,PGCP-BDA,2026-08-01"
        )
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=student_template.csv"}
        )
    except Exception as e:
        return {"success": False, "message": f"Failed to generate template: {str(e)}"}


@app.get("/api/students/pending")
async def list_pending_students(session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Students onboarded via CSV who still need their face registered."""
    try:
        allowed = teacher_allowed_course_ids(session)  # None = admin (all)
        cur = attendance_system.conn.cursor()
        rows = cur.execute('''
            SELECT s.id, s.student_id, s.name, s.email, s.dob, s.course_id, c.name
            FROM students s LEFT JOIN courses c ON c.id = s.course_id
            WHERE s.status = 'pending_registration' AND s.face_encoding IS NULL
            ORDER BY c.name, s.name
        ''').fetchall()
        pending = []
        for r in rows:
            if allowed is not None and r[5] not in allowed:
                continue
            pending.append({
                "id": r[0], "student_id": r[1], "name": r[2], "email": r[3],
                "dob": r[4], "course_id": r[5], "batch": r[6] or "—",
            })
        return {"success": True, "pending": pending, "count": len(pending)}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/logout")
async def logout(response: Response, session_token: str = Cookie(None, alias="session_token")):
    """Secure logout — destroys the server-side session and clears the cookie."""
    try:
        if session_token:
            SessionManager.destroy_session(session_token)
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
async def logout_redirect(session_token: str = Cookie(None, alias="session_token")):
    """GET logout route for direct access — destroys the session and redirects."""
    if session_token:
        SessionManager.destroy_session(session_token)
    resp = RedirectResponse(url="/login")
    resp.delete_cookie(key="session_token")
    return resp

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


# ==================================================================
# Teacher portal + bulk attendance actions (Phase 5)
# ==================================================================

SESSION_SLOT_TYPES = ["morning_1", "morning_2", "afternoon_1", "afternoon_2"]


@app.get("/api/teacher/batch/{course_id}/students")
async def teacher_batch_students(course_id: int, date: Optional[str] = None,
                                 session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Students in a batch with their present-status for a given date (default today)."""
    try:
        assert_course_allowed(session, course_id)
        the_date = date or datetime.now(timezone).date().strftime("%Y-%m-%d")
        cur = attendance_system.conn.cursor()
        students = []
        for r in cur.execute(
            "SELECT id, student_id, name, (face_encoding IS NOT NULL), email, dob FROM students "
            "WHERE course_id = ? AND status IN ('active','pending_registration') ORDER BY name",
            (course_id,),
        ).fetchall():
            marks = cur.execute(
                "SELECT session_type, time_in, is_manual FROM attendance WHERE student_id = ? AND date = ?",
                (r[0], the_date),
            ).fetchall()
            present = len(marks) > 0
            sessions_present = [m[0] for m in marks if m[0]]
            students.append({
                "id": r[0], "student_id": r[1], "name": r[2], "has_face": bool(r[3]),
                "email": r[4], "dob": r[5],
                "present": present, "sessions": sessions_present,
            })
        present_count = sum(1 for s in students if s["present"])
        return {
            "success": True, "date": the_date, "students": students,
            "summary": {
                "total": len(students), "present": present_count,
                "absent": len(students) - present_count,
                "rate": round(present_count / len(students) * 100, 1) if students else 0.0,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/attendance/bulk-mark")
async def bulk_mark_attendance_api(data: dict = Body(...),
                                   session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Bulk mark attendance for a batch.

    Body:
        course_id     (required) batch to act on
        date          (required) YYYY-MM-DD
        status        'present' | 'absent'   (default 'present')
        session_type  optional; a slot name, or null/omitted for the whole day
        student_ids   optional list; omitted/empty = all active students in the batch
        reason        optional note
    'present' inserts a manual attendance record (idempotent); 'absent' clears any
    existing records for that date (and session, if given).
    """
    try:
        course_id = data.get("course_id")
        the_date = (data.get("date") or "").strip()
        status = (data.get("status") or "present").strip().lower()
        session_type = data.get("session_type") or None
        student_ids = data.get("student_ids") or None
        reason = (data.get("reason") or "Bulk marked").strip()

        if not course_id or not the_date:
            return {"success": False, "message": "course_id and date are required"}
        if status not in ("present", "absent"):
            return {"success": False, "message": "status must be 'present' or 'absent'"}
        if session_type and session_type not in SESSION_SLOT_TYPES:
            return {"success": False, "message": f"Unknown session '{session_type}'"}

        assert_course_allowed(session, course_id)
        cur = attendance_system.conn.cursor()

        # Resolve target students (validate they belong to this batch)
        if student_ids:
            placeholders = ",".join("?" * len(student_ids))
            rows = cur.execute(
                f"SELECT id FROM students WHERE course_id = ? AND id IN ({placeholders}) "
                f"AND status IN ('active','pending_registration')",
                [course_id, *student_ids],
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT id FROM students WHERE course_id = ? AND status IN ('active','pending_registration')",
                (course_id,),
            ).fetchall()
        target_ids = [r[0] for r in rows]
        if not target_ids:
            return {"success": False, "message": "No matching students in this batch"}

        now_time = datetime.now(timezone).strftime("%H:%M:%S")
        affected = 0

        for sid in target_ids:
            if status == "present":
                # Skip if already marked for this date (+ session if given)
                if session_type:
                    exists = cur.execute(
                        "SELECT 1 FROM attendance WHERE student_id = ? AND date = ? AND session_type = ?",
                        (sid, the_date, session_type),
                    ).fetchone()
                else:
                    exists = cur.execute(
                        "SELECT 1 FROM attendance WHERE student_id = ? AND date = ?",
                        (sid, the_date),
                    ).fetchone()
                if exists:
                    continue
                cur.execute(
                    "INSERT INTO attendance (student_id, date, time_in, status, is_manual, "
                    "manual_reason, session_type, course_id) VALUES (?, ?, ?, 'present', 1, ?, ?, ?)",
                    (sid, the_date, now_time, reason, session_type, course_id),
                )
                affected += 1
            else:  # absent -> remove present records
                if session_type:
                    cur.execute(
                        "DELETE FROM attendance WHERE student_id = ? AND date = ? AND session_type = ?",
                        (sid, the_date, session_type),
                    )
                else:
                    cur.execute(
                        "DELETE FROM attendance WHERE student_id = ? AND date = ?",
                        (sid, the_date),
                    )
                affected += cur.rowcount

        attendance_system.conn.commit()
        scope = f"session '{session_type}'" if session_type else "the whole day"
        verb = "marked present" if status == "present" else "cleared"
        return {
            "success": True,
            "message": f"{verb.title()} for {len(target_ids)} student(s) on {the_date} ({scope}).",
            "affected": affected,
            "students": len(target_ids),
        }
    except HTTPException:
        raise
    except Exception as e:
        attendance_system.conn.rollback()
        return {"success": False, "message": str(e)}


# ==================================================================
# Grievances (attendance disputes) — Phase 9
# ==================================================================

GRIEVANCE_WINDOW_DAYS = 30


@app.post("/api/student/grievance")
async def raise_grievance(data: dict = Body(...), session: Dict[str, Any] = Depends(require_student)):
    """A student disputes being marked absent for a date/session (last 30 days)."""
    try:
        info = session.get("user_info", {})
        sid = info.get("id")
        the_date = (data.get("date") or "").strip()
        session_type = data.get("session_type") or None
        reason = (data.get("reason") or "").strip()

        if not the_date or not reason:
            return {"success": False, "message": "Date and reason are required"}
        if session_type and session_type not in SESSION_SLOT_TYPES:
            return {"success": False, "message": "Invalid session"}

        try:
            d = datetime.strptime(the_date, "%Y-%m-%d").date()
        except ValueError:
            return {"success": False, "message": "Invalid date"}
        today = date.today()
        if d > today:
            return {"success": False, "message": "Cannot dispute a future date"}
        if (today - d).days > GRIEVANCE_WINDOW_DAYS:
            return {"success": False, "message": f"You can only dispute the last {GRIEVANCE_WINDOW_DAYS} days"}

        cur = attendance_system.conn.cursor()
        # Block a duplicate pending grievance for the same date/session
        dup = cur.execute(
            "SELECT 1 FROM grievances WHERE student_id = ? AND date = ? AND "
            "IFNULL(session_type,'') = IFNULL(?, '') AND status = 'pending'",
            (sid, the_date, session_type),
        ).fetchone()
        if dup:
            return {"success": False, "message": "You already have a pending request for this date/session"}

        cur.execute(
            "INSERT INTO grievances (student_id, course_id, date, session_type, reason, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (sid, info.get("course_id"), the_date, session_type, reason),
        )
        attendance_system.conn.commit()
        return {"success": True, "message": "Your request has been submitted for review"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/student/grievances")
async def my_grievances(session: Dict[str, Any] = Depends(require_student)):
    """A student's own grievance history."""
    try:
        sid = session.get("user_info", {}).get("id")
        rows = attendance_system.conn.execute(
            "SELECT id, date, session_type, reason, status, created_at, review_note "
            "FROM grievances WHERE student_id = ? ORDER BY created_at DESC",
            (sid,),
        ).fetchall()
        return {"success": True, "grievances": [{
            "id": r[0], "date": r[1], "session_type": r[2] or "Whole day", "reason": r[3],
            "status": r[4], "created_at": r[5], "review_note": r[6],
        } for r in rows]}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/teacher/grievances")
async def teacher_grievances(status: str = "pending",
                             session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Grievances for the teacher's assigned batches (default: pending)."""
    try:
        allowed = teacher_allowed_course_ids(session)  # None = admin (all)
        rows = attendance_system.conn.execute(
            "SELECT g.id, g.student_id, s.student_id, s.name, g.course_id, c.name, "
            "g.date, g.session_type, g.reason, g.status, g.created_at "
            "FROM grievances g JOIN students s ON s.id = g.student_id "
            "LEFT JOIN courses c ON c.id = g.course_id "
            "WHERE g.status = ? ORDER BY g.created_at DESC",
            (status,),
        ).fetchall()
        out = []
        for r in rows:
            if allowed is not None and r[4] not in allowed:
                continue
            out.append({
                "id": r[0], "student_db_id": r[1], "roll_no": r[2], "student_name": r[3],
                "course_id": r[4], "batch": r[5] or "—", "date": r[6],
                "session_type": r[7] or "Whole day", "reason": r[8], "status": r[9], "created_at": r[10],
            })
        return {"success": True, "grievances": out, "count": len(out)}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/teacher/grievances/action")
async def act_on_grievances(data: dict = Body(...),
                            session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Approve (marks present) or reject grievances — single or in bulk."""
    try:
        ids = data.get("ids") or []
        action = (data.get("action") or "").strip().lower()
        note = (data.get("note") or "").strip() or None
        if not ids or action not in ("approve", "reject"):
            return {"success": False, "message": "Provide ids and action ('approve' or 'reject')"}

        allowed = teacher_allowed_course_ids(session)
        reviewer = session.get("user_info", {}).get("id")
        new_status = "approved" if action == "approve" else "rejected"
        cur = attendance_system.conn.cursor()
        processed = 0
        marked = 0

        for gid in ids:
            row = cur.execute(
                "SELECT student_id, course_id, date, session_type, status FROM grievances WHERE id = ?",
                (gid,),
            ).fetchone()
            if not row or row[4] != "pending":
                continue
            student_db_id, course_id, the_date, session_type, _ = row
            if allowed is not None and course_id not in allowed:
                continue  # not this teacher's batch

            # On approve, mark the student present (idempotent) for that date/session
            if action == "approve":
                if session_type:
                    exists = cur.execute(
                        "SELECT 1 FROM attendance WHERE student_id = ? AND date = ? AND session_type = ?",
                        (student_db_id, the_date, session_type),
                    ).fetchone()
                else:
                    exists = cur.execute(
                        "SELECT 1 FROM attendance WHERE student_id = ? AND date = ?",
                        (student_db_id, the_date),
                    ).fetchone()
                if not exists:
                    cur.execute(
                        "INSERT INTO attendance (student_id, date, time_in, status, is_manual, "
                        "manual_reason, session_type, course_id) VALUES (?, ?, ?, 'present', 1, ?, ?, ?)",
                        (student_db_id, the_date,
                         datetime.now(timezone).strftime("%H:%M:%S"),
                         "Grievance approved", session_type, course_id),
                    )
                    marked += 1

            cur.execute(
                "UPDATE grievances SET status = ?, reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP, "
                "review_note = ? WHERE id = ?",
                (new_status, reviewer, note, gid),
            )
            processed += 1

        attendance_system.conn.commit()
        msg = f"{new_status.title()} {processed} request(s)"
        if action == "approve":
            msg += f" — {marked} student(s) marked present"
        return {"success": True, "message": msg, "processed": processed, "marked": marked}
    except Exception as e:
        attendance_system.conn.rollback()
        return {"success": False, "message": str(e)}


@app.get("/api/teacher/batch/{course_id}/analytics")
async def teacher_batch_analytics(course_id: int, at_risk_threshold: float = 75.0,
                                  session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Batch analytics: per-student %, at-risk, attendance trend, day-of-week."""
    from datetime import date as _date, timedelta as _td
    try:
        assert_course_allowed(session, course_id)
        cur = attendance_system.conn.cursor()

        crow = cur.execute("SELECT start_date FROM courses WHERE id = ?", (course_id,)).fetchone()
        students = cur.execute(
            "SELECT id, student_id, name FROM students "
            "WHERE course_id = ? AND status IN ('active','pending_registration') ORDER BY name",
            (course_id,),
        ).fetchall()

        # Holidays affecting this batch
        holiday_dates = set()
        for r in cur.execute(
            "SELECT date FROM holidays WHERE course_id IS NULL OR course_id = ?", (course_id,)
        ).fetchall():
            try:
                holiday_dates.add(datetime.strptime(str(r[0])[:10], "%Y-%m-%d").date())
            except (ValueError, TypeError):
                continue

        today = _date.today()
        # window start = batch start date, capped to 120 days back to keep it light
        start = today - _td(days=120)
        if crow and crow[0]:
            try:
                cs = datetime.strptime(str(crow[0])[:10], "%Y-%m-%d").date()
                start = max(cs, start)
            except (ValueError, TypeError):
                pass
        if start > today:
            start = today

        # Working days in window (Mon–Sat, excl holidays), and per-weekday counts
        working_days = 0
        weekday_working = [0] * 7
        d = start
        while d <= today:
            if d.weekday() != 6 and d not in holiday_dates:
                working_days += 1
                weekday_working[d.weekday()] += 1
            d += _td(days=1)

        # Present marks in window for this batch's students
        sid_list = [s[0] for s in students]
        present_by_student = {sid: set() for sid in sid_list}
        weekday_present = [0] * 7
        trend = {}  # date -> present count (last 14 days)
        if sid_list:
            placeholders = ",".join("?" * len(sid_list))
            rows = cur.execute(
                f"SELECT student_id, date FROM attendance WHERE student_id IN ({placeholders}) AND date >= ?",
                [*sid_list, start.strftime("%Y-%m-%d")],
            ).fetchall()
            for stu_id, dt in rows:
                try:
                    dd = datetime.strptime(str(dt)[:10], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue
                if dd > today:
                    continue
                if dd not in present_by_student.get(stu_id, set()):
                    present_by_student.setdefault(stu_id, set()).add(dd)

        # Per-student rates
        per_student, at_risk = [], []
        rate_sum = 0.0
        for s in students:
            pres = len(present_by_student.get(s[0], set()))
            rate = round(pres / working_days * 100, 1) if working_days else 0.0
            rate_sum += rate
            entry = {"student_id": s[1], "name": s[2], "present_days": pres,
                     "working_days": working_days, "rate": rate}
            per_student.append(entry)
            if rate < at_risk_threshold:
                at_risk.append(entry)

        # Weekday present counts (from distinct present dates)
        for sid, dates in present_by_student.items():
            for dd in dates:
                if dd.weekday() != 6:
                    weekday_present[dd.weekday()] += 1

        # Trend: last 14 calendar days, present count across the batch
        trend_list = []
        for i in range(13, -1, -1):
            dd = today - _td(days=i)
            cnt = sum(1 for dates in present_by_student.values() if dd in dates)
            trend_list.append({"date": dd.strftime("%Y-%m-%d"), "present": cnt,
                               "is_off": dd.weekday() == 6 or dd in holiday_dates})

        # Day-of-week average attendance rate (Mon–Sat)
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        dow = []
        total_students = len(students)
        for wd in range(6):
            denom = weekday_working[wd] * total_students
            pct = round(weekday_present[wd] / denom * 100, 1) if denom else 0.0
            dow.append({"day": names[wd], "rate": pct})

        per_student.sort(key=lambda x: x["rate"])
        return {
            "success": True,
            "summary": {
                "total_students": total_students,
                "working_days": working_days,
                "avg_rate": round(rate_sum / total_students, 1) if total_students else 0.0,
                "at_risk_count": len(at_risk),
                "since": start.strftime("%Y-%m-%d"),
            },
            "per_student": per_student,
            "at_risk": at_risk,
            "trend": trend_list,
            "day_of_week": dow,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/teacher/batch/{course_id}/export")
async def export_batch_attendance(course_id: int, start: Optional[str] = None, end: Optional[str] = None,
                                  session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """CSV: daily present/absent register for a batch over a date range + per-student %."""
    from datetime import date as _date, timedelta as _td
    try:
        assert_course_allowed(session, course_id)
        cur = attendance_system.conn.cursor()

        today = _date.today()
        try:
            end_d = datetime.strptime(end, "%Y-%m-%d").date() if end else today
        except (ValueError, TypeError):
            end_d = today
        try:
            start_d = datetime.strptime(start, "%Y-%m-%d").date() if start else end_d.replace(day=1)
        except (ValueError, TypeError):
            start_d = end_d.replace(day=1)
        if start_d > end_d:
            start_d, end_d = end_d, start_d
        # cap the grid to ~100 days to keep the file sane
        if (end_d - start_d).days > 100:
            start_d = end_d - _td(days=100)

        batch = cur.execute("SELECT name FROM courses WHERE id = ?", (course_id,)).fetchone()
        batch_name = batch[0] if batch else f"batch-{course_id}"

        students = cur.execute(
            "SELECT id, student_id, name FROM students "
            "WHERE course_id = ? AND status IN ('active','pending_registration') ORDER BY name",
            (course_id,),
        ).fetchall()

        holidays = set()
        for r in cur.execute("SELECT date FROM holidays WHERE course_id IS NULL OR course_id = ?", (course_id,)):
            try:
                holidays.add(datetime.strptime(str(r[0])[:10], "%Y-%m-%d").date())
            except (ValueError, TypeError):
                continue

        # working dates (Mon–Sat, excl holidays)
        work_dates = []
        d = start_d
        while d <= end_d:
            if d.weekday() != 6 and d not in holidays:
                work_dates.append(d)
            d += _td(days=1)

        # present map
        present = {s[0]: set() for s in students}
        if students:
            ph = ",".join("?" * len(students))
            for sid, dt in cur.execute(
                f"SELECT student_id, date FROM attendance WHERE student_id IN ({ph}) AND date >= ? AND date <= ?",
                [*[s[0] for s in students], start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d")],
            ).fetchall():
                try:
                    present[sid].add(datetime.strptime(str(dt)[:10], "%Y-%m-%d").date())
                except (ValueError, TypeError):
                    continue

        out = StringIO()
        w = csv.writer(out)
        w.writerow(["Roll No", "Name"] + [d.strftime("%d-%b") for d in work_dates]
                   + ["Present", "Working Days", "Rate %"])
        wd = len(work_dates)
        for sid, roll, name in students:
            pres = present.get(sid, set())
            row = [roll, name] + ["P" if d in pres else "A" for d in work_dates]
            pcount = sum(1 for d in work_dates if d in pres)
            row += [pcount, wd, round(pcount / wd * 100, 1) if wd else 0]
            w.writerow(row)

        filename = f"attendance_{batch_name.replace(' ', '_')}_{start_d}_{end_d}.csv"
        return Response(content=out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except HTTPException:
        raise
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
                    WHERE date = ? AND slot_id LIKE 'morning%'
                ''', (date_str,))
                morning_count = cursor.fetchone()[0]
                
                # Count afternoon sessions (FROM SLOT_ATTENDANCE)
                cursor.execute('''
                    SELECT COUNT(DISTINCT student_id) FROM slot_attendance 
                    WHERE date = ? AND slot_id LIKE 'afternoon%'
                ''', (date_str,))
                afternoon_count = cursor.fetchone()[0]
                
                # Count students with both sessions equivalent
                cursor.execute('''
                    SELECT student_id FROM slot_attendance 
                    WHERE date = ? 
                    GROUP BY student_id 
                    HAVING SUM(CASE WHEN slot_id LIKE 'morning%' THEN 1 ELSE 0 END) > 0 
                       AND SUM(CASE WHEN slot_id LIKE 'afternoon%' THEN 1 ELSE 0 END) > 0
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
                    SELECT date, 
                           SUM(CASE WHEN slot_id LIKE 'morning%' THEN 1 ELSE 0 END) as morn_count,
                           SUM(CASE WHEN slot_id LIKE 'afternoon%' THEN 1 ELSE 0 END) as aft_count
                    FROM slot_attendance 
                    WHERE student_id = ? AND date BETWEEN ? AND ?
                    GROUP BY date
                ''', (student_id, start_date, end_date))  # FIXED: use string versions
                
                daily_sessions = cursor.fetchall()
                
                full_days = 0
                half_days = 0
                for _, morn, aft in daily_sessions:
                    if morn > 0 and aft > 0:
                        full_days += 1
                    elif morn > 0 or aft > 0:
                        half_days += 1
                
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
                cursor.execute('SELECT COUNT(DISTINCT student_id) FROM slot_attendance WHERE date = ? AND slot_id LIKE "morning%"', (date_str,))
                morning_count = cursor.fetchone()[0]
                
                cursor.execute('SELECT COUNT(DISTINCT student_id) FROM slot_attendance WHERE date = ? AND slot_id LIKE "afternoon%"', (date_str,))
                afternoon_count = cursor.fetchone()[0]
                
                cursor.execute('''SELECT student_id FROM slot_attendance WHERE date = ? GROUP BY student_id HAVING SUM(CASE WHEN slot_id LIKE 'morning%' THEN 1 ELSE 0 END) > 0 AND SUM(CASE WHEN slot_id LIKE 'afternoon%' THEN 1 ELSE 0 END) > 0''', (date_str,))
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
                session_type = slot_id.replace('_', ' ').title()
                
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
        timezone = pytz.timezone('Asia/Kolkata')
        today = datetime.now(timezone).date()
        cursor = attendance_system.conn.cursor()


        
        cursor.execute('''
            SELECT s.name, s.student_id, s.email, 
                   sa_m1.created_at as morning_1_time,
                   sa_m2.created_at as morning_2_time,
                   sa_a1.created_at as afternoon_1_time,
                   sa_a2.created_at as afternoon_2_time,
                   s.id
            FROM students s
            LEFT JOIN slot_attendance sa_m1 ON s.id = sa_m1.student_id 
                AND sa_m1.date = ? AND sa_m1.slot_id = 'morning_1'
            LEFT JOIN slot_attendance sa_m2 ON s.id = sa_m2.student_id 
                AND sa_m2.date = ? AND sa_m2.slot_id = 'morning_2'
            LEFT JOIN slot_attendance sa_a1 ON s.id = sa_a1.student_id 
                AND sa_a1.date = ? AND sa_a1.slot_id = 'afternoon_1'
            LEFT JOIN slot_attendance sa_a2 ON s.id = sa_a2.student_id 
                AND sa_a2.date = ? AND sa_a2.slot_id = 'afternoon_2'
            WHERE s.status = 'active'
            ORDER BY s.name
        ''', (today, today, today, today))
        
        return cursor.fetchall()
        
    except Exception as e:
        print(f"Error loading slot attendance: {e}")
        return []

@app.get("/api/attendance/analytics/class")
async def get_class_analytics_data(days: int = 14, course_id: Optional[int] = None):
    """Endpoint for comprehensive class analytics (optionally scoped to a batch)."""
    try:
        return analytics_manager.get_class_analytics(days=days, course_id=course_id)
    except Exception as e:
        print(f"Error in class analytics API: {e}")
        return {"success": False, "message": str(e)}

@app.get("/api/analytics/heatmap")
async def get_heatmap_data(days: int = 90, course_id: Optional[int] = None):
    """Per-day attendance % for calendar heatmap"""
    try:
        return analytics_manager.get_heatmap_data(days=days, course_id=course_id)
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/analytics/day-of-week")
async def get_day_of_week_data(days: int = 60, course_id: Optional[int] = None):
    """Average attendance % per weekday"""
    try:
        return analytics_manager.get_day_of_week_stats(days=days, course_id=course_id)
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/analytics/at-risk")
async def get_at_risk_data(threshold: int = 75, course_id: Optional[int] = None):
    """All students below attendance threshold with streak info"""
    try:
        return analytics_manager.get_at_risk_students(threshold=threshold, course_id=course_id)
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/analytics/student/{student_id}/sparkline")
async def get_student_sparkline(student_id: int, days: int = 14):
    """14-day per-day attendance sparkline for a single student"""
    try:
        return analytics_manager.get_student_sparkline(student_id=student_id, days=days)
    except Exception as e:
        return {"success": False, "message": str(e)}

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
async def detect_attendance_with_slots(image_data: DetectionImage,
                                       session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Enhanced detection with slot-based attendance marking.

    When opened as a batch terminal (terminal session), only students of that
    batch are marked; others are reported as not-in-batch.
    """
    if not FACE_RECOGNITION_AVAILABLE:
        return {"success": False, "message": "Face recognition not available"}

    # Terminal sessions restrict marking to their batch
    terminal_batch_ids = None
    if session and session.get("user_type") == "terminal":
        tcid = session.get("user_info", {}).get("course_id")
        terminal_batch_ids = {
            r[0] for r in attendance_system.conn.execute(
                "SELECT id FROM students WHERE course_id = ?", (tcid,)
            ).fetchall()
        }

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
        spoofed_faces = 0
        
        for face_data in detected_faces:
            # --- ANTI-SPOOFING GATE ---
            liveness = anti_spoof_checker.check(image_array, face_data['location'])
            if not liveness['is_real']:
                spoofed_faces += 1
                face_location = face_data['location']
                recognized_students.append({
                    "student_id": None,
                    "name": "SPOOF DETECTED",
                    "confidence": 0.0,
                    "status": "spoof_detected",
                    "message": f"Liveness check failed (score: {liveness['score']:.2f})",
                    "liveness_score": liveness['score'],
                    "location": {
                        "top": int(face_location[0]),
                        "right": int(face_location[1]),
                        "bottom": int(face_location[2]),
                        "left": int(face_location[3])
                    }
                })
                continue
            # --- END ANTI-SPOOFING GATE ---

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

                    # Terminal: skip students who aren't in this batch
                    if terminal_batch_ids is not None and student_id not in terminal_batch_ids:
                        face_location = face_data['location']
                        recognized_students.append({
                            "student_id": student_id,
                            "name": student_name,
                            "confidence": float(best_similarity),
                            "status": "wrong_batch",
                            "message": f"{student_name} is not in this batch",
                            "location": {
                                "top": int(face_location[0]),
                                "right": int(face_location[1]),
                                "bottom": int(face_location[2]),
                                "left": int(face_location[3])
                            }
                        })
                        continue

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
                            "liveness_score": liveness['score'],
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
                            "liveness_score": liveness['score'],
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
    
    def get_display_host():
        """Get the host to display in URLs"""
        try:
            # Get local IP for display
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except:
            return "localhost"

    # Host settings
    host = "0.0.0.0"  # Listen on ALL interfaces
    display_host = get_display_host()  # For display in URLs
    port = int(os.getenv("PORT", 8000))
    
    # SSL certificate files
    cert_file = "cert.pem"
    key_file = "key.pem"
    
    # Generate self-signed certificate if it doesn't exist
    if not os.path.exists(cert_file) or not os.path.exists(key_file):
        print("[SETUP] Generating SSL certificates...")
        try:
            # Create self-signed certificate with dynamic host
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:4096", 
                "-keyout", key_file, "-out", cert_file, "-days", "365", "-nodes",
                "-subj", f"/C=IN/ST=Maharashtra/L=Mumbai/O=CDAC/CN={display_host}"
            ], check=True)
            print("[OK] SSL certificates generated!")
        except subprocess.CalledProcessError:
            print("[ERROR] Failed to generate SSL certificates. Install OpenSSL first.")
            print(f"[STATS] Running on HTTP: http://{display_host}:{port}/")
            uvicorn.run("main_with_face_recognition:app", host=host, port=port)
            exit()
    
    # Run with HTTPS
    print(f"[INFO] HTTPS Dashboard: https://{display_host}:{port}/")
    print("[WARN]  You may see a security warning - click 'Advanced' -> 'Proceed to site (unsafe)'")
    print("[TIP] Tip: Bookmark the HTTPS URL to avoid the warning next time")
    
    try:
        uvicorn.run(
            "main_with_face_recognition:app", 
            host=host, 
            port=port,
            ssl_keyfile=key_file,
            ssl_certfile=cert_file
        )
    except Exception as e:
        print(f"[ERROR] HTTPS failed: {e}")
        print(f"[STATS] Falling back to HTTP: http://{display_host}:{port}/")
        uvicorn.run("main_with_face_recognition:app", host=host, port=port)
