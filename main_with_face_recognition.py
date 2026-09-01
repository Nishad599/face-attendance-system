from fastapi import FastAPI, HTTPException, Request, Body, Depends, Cookie, Response, UploadFile, File, BackgroundTasks
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
import re
from phase1_integration import enhance_existing_attendance_system, add_phase1_api_endpoints
from attendance_manager import create_slot_manager_instance
import pytz
import csv
from io import StringIO
from analytics_manager import AnalyticsManager
from anti_spoofing import anti_spoof_checker
import timetable
import working_days


def sweep_stale_registration_files(max_age_hours: int = 6) -> int:
    """Delete abandoned temp_encodings_*.npy scratch files.

    Face registration writes one per session and removes it on completion —
    but a student who starts and walks away leaves it behind forever. The
    registration session itself expires after 30 minutes, so anything older
    than a few hours is certainly dead. Best-effort; never raises.
    """
    import glob
    # NB: `from datetime import time` at the top of this module shadows the
    # stdlib `time` module, so use datetime for the clock here.
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    removed = 0
    for path in glob.glob("temp_encodings_*.npy"):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue        # in use, or already gone
    if removed:
        print(f"[OK] Cleaned up {removed} abandoned registration file(s)")
    return removed

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
    half_day_enabled: Optional[int] = None

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
# Login throttling lives in the same store. Created here as well as in
# migrate_phase5.py so a fresh database is never briefly un-throttled.
_session_conn.execute(
    """CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier TEXT NOT NULL,
            attempted_at TIMESTAMP NOT NULL
    )""" if not is_postgres() else
    """CREATE TABLE IF NOT EXISTS login_attempts (
            id SERIAL PRIMARY KEY,
            identifier TEXT NOT NULL,
            attempted_at TIMESTAMP NOT NULL
    )"""
)
_session_conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_login_attempts_identifier "
    "ON login_attempts (identifier, attempted_at)"
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


def require_staff_or_terminal(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Allow staff AND batch terminals.

    Used by the live-attendance surface, which the kiosk reuses: a terminal
    session must still be able to detect faces and read the live count.
    """
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    if session.get("user_type") not in ["admin", "user", "teacher", "terminal"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return session


def require_any_authenticated(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Any signed-in principal (staff, student or terminal).

    For endpoints students legitimately use during their own self-registration.
    """
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
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
    
    DUPLICATE_FACE_THRESHOLD = 0.60

    def find_matching_student(self, encoding, exclude_student_code=None, exclude_db_id=None):
        """Return (student_name, similarity) if `encoding` already belongs to a
        registered student, else (None, 0.0).

        Used to stop the same person registering under two roll numbers.
        """
        try:
            if encoding is None or not len(self.known_face_encodings):
                return None, 0.0

            exclude_ids = set()
            cur = self.conn.cursor()
            if exclude_student_code is not None:
                row = cur.execute(
                    "SELECT id FROM students WHERE student_id = ?", (exclude_student_code,)
                ).fetchone()
                if row:
                    exclude_ids.add(row[0])
            if exclude_db_id is not None:
                exclude_ids.add(exclude_db_id)

            enc = np.asarray(encoding, dtype=np.float64)
            enc_norm = enc / np.linalg.norm(enc)

            best_name, best_sim = None, 0.0
            for known, db_id, name in zip(self.known_face_encodings,
                                          self.known_face_ids,
                                          self.known_face_names):
                if db_id in exclude_ids:
                    continue
                k = np.asarray(known, dtype=np.float64)
                if k.shape != enc_norm.shape:
                    continue
                sim = float(np.dot(enc_norm, k / np.linalg.norm(k)))
                if sim > best_sim:
                    best_sim, best_name = sim, name

            if best_sim >= self.DUPLICATE_FACE_THRESHOLD:
                return best_name, best_sim
            return None, best_sim
        except Exception as e:
            print(f"[WARN] duplicate-face check skipped: {e}")
            return None, 0.0

    def build_session_encoding(self, session_id: str):
        """Average the captured photos of a registration session.

        Returns (average_encoding, photos_uploaded, verification_score) or
        (None, 0, 0.0) when the session has no usable photos.
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT photos_uploaded FROM registration_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            photos_uploaded = row[0] if row else 0

            temp_file = f"temp_encodings_{session_id}.npy"
            if not os.path.exists(temp_file):
                return None, photos_uploaded, 0.0

            encodings_data = np.load(temp_file, allow_pickle=True).tolist()
            encodings = [np.array(item['encoding']) for item in encodings_data]
            if not encodings:
                return None, photos_uploaded, 0.0

            average_encoding = np.mean(encodings, axis=0)
            avg_norm = average_encoding / np.linalg.norm(average_encoding)
            sims = [float(np.dot(e / np.linalg.norm(e), avg_norm)) for e in encodings]
            verification_score = float(np.mean(sims)) if sims else 0.8
            return average_encoding, len(encodings), verification_score
        except Exception as e:
            print(f"[ERROR] build_session_encoding: {e}")
            return None, 0, 0.0

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
            
            # --- DUPLICATE FACE CHECK -------------------------------------
            # Reject if this face is already registered to a DIFFERENT student,
            # which would otherwise allow one person to hold two roll numbers
            # (or a student to register a friend's face on their own account).
            dup_name, dup_sim = self.find_matching_student(
                average_encoding, exclude_student_code=student_data['student_id']
            )
            if dup_name:
                return False, (f"This face is already registered to {dup_name} "
                               f"(match {dup_sim:.0%}). A person can only be registered once. "
                               f"If this is wrong, ask an admin to remove the other record.")
            # --- END DUPLICATE FACE CHECK ---------------------------------

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

        # Reject Sundays and holidays. The previous check here looked only at
        # holidays, ignored Sundays entirely, and matched any batch's holiday
        # rather than this student's — see working_days.check.
        row = cursor.execute(
            "SELECT course_id FROM students WHERE id = ?", (student_id,)
        ).fetchone()
        ok_day, why = working_days.check(self.conn, row[0] if row else None, date_str)
        if not ok_day:
            return False, why


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

# Clear scratch files left by registrations that were started and abandoned.
sweep_stale_registration_files()

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


# --- Login rate limiting --------------------------------------------------
# The implementation lives in login_throttle.py (DB-backed, so lockouts now
# survive a restart). These thin wrappers keep the existing call sites and
# bind the module to the session connection.
import login_throttle

LOGIN_MAX_ATTEMPTS = login_throttle.MAX_ATTEMPTS
LOGIN_LOCKOUT_MINUTES = login_throttle.LOCKOUT_MINUTES


def login_blocked(identifier: str):
    """(blocked, seconds_remaining) for this identifier."""
    return login_throttle.is_blocked(_session_conn, identifier)


def record_login_failure(identifier: str):
    login_throttle.record_failure(_session_conn, identifier)


def clear_login_failures(identifier: str):
    login_throttle.clear(_session_conn, identifier)


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

def audit(session, action, target=None, details=None, course_id=None):
    """Record a sensitive action (who did what). Best-effort: never raises."""
    try:
        info = (session or {}).get("user_info", {}) or {}
        attendance_system.conn.execute(
            "INSERT INTO audit_log (actor_type, actor_id, actor_name, action, target, details, course_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ((session or {}).get("user_type"), info.get("id"),
             info.get("name") or info.get("username"),
             action, str(target) if target is not None else None,
             str(details) if details is not None else None, course_id),
        )
        attendance_system.conn.commit()
    except Exception as e:
        print(f"[WARN] audit log skipped ({action}): {e}")


LATE_GRACE_MINUTES = int(os.getenv("LATE_GRACE_MINUTES", "10") or 10)


def compute_is_late(slot_key: str = None, when: datetime = None) -> bool:
    """True when the arrival is more than LATE_GRACE_MINUTES after the slot start.

    Falls back to False when no slot is active or slot timings can't be read,
    so a failure here can never wrongly flag a student.
    """
    try:
        if attendance_manager is None:
            return False
        now = when or datetime.now(timezone)
        slots = getattr(attendance_manager, "attendance_slots", {}) or {}

        if slot_key and slot_key in slots:
            slot = slots[slot_key]
        else:
            current = attendance_manager.get_current_slot(now)
            if not current:
                return False
            slot = current.get("slot_info") or {}

        start = slot.get("start_time")
        if start is None:
            return False

        now_t = now.time()
        start_minutes = start.hour * 60 + start.minute
        now_minutes = now_t.hour * 60 + now_t.minute
        return (now_minutes - start_minutes) > LATE_GRACE_MINUTES
    except Exception as e:
        print(f"[WARN] late check skipped: {e}")
        return False


# Session cookies are marked Secure by default because this app serves HTTPS
# (both the VM and the local dev server). Set COOKIE_SECURE=0 in .env only if you
# deliberately run it over plain HTTP — without Secure, the session cookie can be
# sent over an unencrypted request and stolen.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no")


def _set_session_cookie(response: Response, session_token: str):
    response.set_cookie(
        key="session_token",
        value=session_token,
        max_age=SESSION_TIMEOUT_HOURS * 3600,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )


@app.post("/api/admin-login")
async def simple_admin_login(login_data: SimpleAdminLogin, response: Response):
    """Staff login (admin or teacher), authenticated against the users table."""
    try:
        username = (login_data.username or "").strip()
        password = login_data.password or ""

        blocked, wait = login_blocked(username)
        if blocked:
            return {"success": False,
                    "message": f"Too many failed attempts. Try again in {max(1, wait // 60)} minute(s)."}

        user_info = authenticate_user(username, password, roles=["admin", "teacher"])
        if not user_info:
            record_login_failure(username)
            return {"success": False, "message": "Invalid username or password"}
        clear_login_failures(username)

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

        blocked, wait = login_blocked(student_id)
        if blocked:
            return {"success": False,
                    "message": f"Too many failed attempts. Try again in {max(1, wait // 60)} minute(s)."}

        row = attendance_system.conn.execute(
            "SELECT id, student_id, name, password_hash, status, course_id, "
            "must_change_password, face_encoding IS NOT NULL AS has_face "
            "FROM students WHERE student_id = ?",
            (student_id,),
        ).fetchone()

        if not row or not row[3] or not verify_password(password, row[3]):
            record_login_failure(student_id)
            print(f"[ERROR] Failed student login attempt: {student_id}")
            return {"success": False, "message": "Invalid roll number or password"}
        clear_login_failures(student_id)

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
        # A 4-6 digit PIN is brute-forceable in seconds without a throttle.
        # Keyed per batch so one kiosk being attacked cannot lock out another.
        throttle_key = login_throttle.terminal_key(data.course_id)
        blocked, wait = login_blocked(throttle_key)
        if blocked:
            return {"success": False,
                    "message": f"Too many incorrect PINs. Try again in {wait} seconds."}

        row = attendance_system.conn.execute(
            "SELECT name, terminal_pin_hash FROM courses WHERE id = ? AND is_active = 1",
            (data.course_id,),
        ).fetchone()
        if not row or not row[1]:
            return {"success": False, "message": "No terminal is set up for that batch"}
        if not verify_password((data.pin or "").strip(), row[1]):
            record_login_failure(throttle_key)
            return {"success": False, "message": "Incorrect PIN"}
        clear_login_failures(throttle_key)

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

def _sw_asset_version() -> str:
    """Short digest of the assets the service worker precaches.

    Changes whenever any of them changes, so the worker installs a fresh cache
    and drops the old one (its activate handler deletes non-matching caches).
    """
    h = hashlib.sha256()
    for rel in ("static/css/app.css", "static/js/theme.js",
                "static/js/pwa.js", "static/js/sw.js"):
        try:
            with open(rel, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(rel.encode())          # missing file still affects the hash
    return "v" + h.hexdigest()[:12]


@app.get("/sw.js")
async def service_worker():
    """Serve the service worker from the root.

    A worker's scope cannot be broader than the path it is served from, so
    /static/js/sw.js would only control /static/js/ — useless. Public by
    necessity: the browser fetches it outside any page session.
    """
    path = os.path.join("static", "js", "sw.js")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    with open(path, "r", encoding="utf-8") as f:
        body = f.read()

    # Stamp the cache version from the actual asset contents. The file ships a
    # hand-written `const VERSION = 'v2'`, which only changes if someone
    # remembers to bump it — so after a deploy that touched app.css or
    # theme.js, browsers kept serving the previously cached copies and users
    # saw a stale UI. Deriving it from the files means every real change
    # invalidates the cache on its own.
    body = re.sub(r"const VERSION = '[^']*';",
                  f"const VERSION = '{_sw_asset_version()}';", body, count=1)
    return Response(
        content=body,
        media_type="application/javascript",
        # Never let a stale worker pin itself: browsers honour this for sw.js.
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.webmanifest")
async def web_manifest():
    """PWA manifest, served from the root so its scope covers the whole app."""
    path = os.path.join("static", "manifest.webmanifest")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    with open(path, "r", encoding="utf-8") as f:
        body = f.read()
    return Response(content=body, media_type="application/manifest+json",
                    headers={"Cache-Control": "public, max-age=3600"})


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


# ==================================================================
# Forgot password — emailed one-time code (OTP)
# ==================================================================

OTP_TTL_MINUTES = 15
OTP_MAX_ATTEMPTS = 5
# Deliberately identical for found/not-found so the endpoint can't be used to
# discover which roll numbers or usernames exist.
_OTP_GENERIC = ("If that account exists and has an email address on file, "
                "a reset code has been sent to it.")


def _find_principal(identifier: str):
    """Look up a student (by roll no or email) or staff (by username or email).

    Returns (kind, id, name, email) or None.
    """
    ident = (identifier or "").strip()
    if not ident:
        return None
    cur = attendance_system.conn.cursor()

    row = cur.execute(
        "SELECT id, name, email FROM students WHERE student_id = ? OR LOWER(email) = LOWER(?)",
        (ident, ident),
    ).fetchone()
    if row:
        return ("student", row[0], row[1], row[2])

    row = cur.execute(
        "SELECT id, name, COALESCE(NULLIF(email,''), username) FROM users "
        "WHERE (username = ? OR LOWER(email) = LOWER(?)) AND is_active = 1",
        (ident, ident),
    ).fetchone()
    if row:
        return ("staff", row[0], row[1], row[2])
    return None


@app.post("/api/forgot-password")
async def forgot_password(background_tasks: BackgroundTasks, data: dict = Body(...)):
    """Email a one-time reset code. Always returns the same generic message."""
    try:
        identifier = (data.get("identifier") or "").strip()
        found = _find_principal(identifier)

        if found:
            kind, pid, name, email = found
            if email and "@" in str(email):
                otp = f"{secrets.randbelow(1000000):06d}"
                expires = datetime.now() + timedelta(minutes=OTP_TTL_MINUTES)
                cur = attendance_system.conn.cursor()
                # Invalidate any earlier unused codes for this principal
                cur.execute(
                    "UPDATE password_resets SET used = 1 WHERE principal_type = ? "
                    "AND principal_id = ? AND used = 0",
                    (kind, pid),
                )
                cur.execute(
                    "INSERT INTO password_resets (principal_type, principal_id, otp_hash, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    (kind, pid, hash_password(otp), expires.isoformat()),
                )
                attendance_system.conn.commit()

                def _send(to_email=email, who=name, code=otp):
                    try:
                        import mailer as _m, reports as _r
                        ok, msg = _m.send_email(to_email, "Your password reset code",
                                                _r.otp_email(who, code, OTP_TTL_MINUTES), kind="otp")
                        if not ok:
                            print(f"[MAIL] OTP -> {to_email} failed: {msg}")
                    except Exception as e:
                        print(f"[MAIL] OTP task error: {e}")

                background_tasks.add_task(_send)

        return {"success": True, "message": _OTP_GENERIC}
    except Exception as e:
        print(f"[ERROR] forgot-password: {e}")
        return {"success": True, "message": _OTP_GENERIC}


@app.post("/api/reset-password-otp")
async def reset_password_with_otp(data: dict = Body(...)):
    """Verify the emailed code and set a new password."""
    try:
        identifier = (data.get("identifier") or "").strip()
        otp = (data.get("otp") or "").strip()
        new_password = (data.get("new_password") or "").strip()

        if not identifier or not otp or not new_password:
            return {"success": False, "message": "Account, code and new password are required"}
        if len(new_password) < 6:
            return {"success": False, "message": "New password must be at least 6 characters"}

        found = _find_principal(identifier)
        if not found:
            return {"success": False, "message": "Invalid or expired code"}
        kind, pid, _name, _email = found

        cur = attendance_system.conn.cursor()
        row = cur.execute(
            "SELECT id, otp_hash, expires_at, attempts FROM password_resets "
            "WHERE principal_type = ? AND principal_id = ? AND used = 0 "
            "ORDER BY id DESC LIMIT 1",
            (kind, pid),
        ).fetchone()
        if not row:
            return {"success": False, "message": "Invalid or expired code"}

        reset_id, otp_hash, expires_at, attempts = row[0], row[1], row[2], row[3] or 0

        if attempts >= OTP_MAX_ATTEMPTS:
            cur.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (reset_id,))
            attendance_system.conn.commit()
            return {"success": False, "message": "Too many attempts. Request a new code."}

        try:
            expired = datetime.now() > datetime.fromisoformat(str(expires_at))
        except (ValueError, TypeError):
            expired = True
        if expired:
            cur.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (reset_id,))
            attendance_system.conn.commit()
            return {"success": False, "message": "That code has expired. Request a new one."}

        if not verify_password(otp, otp_hash):
            cur.execute("UPDATE password_resets SET attempts = attempts + 1 WHERE id = ?", (reset_id,))
            attendance_system.conn.commit()
            return {"success": False, "message": "Invalid or expired code"}

        # Code is good — set the new password and burn the code
        if kind == "student":
            cur.execute(
                "UPDATE students SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                (hash_password(new_password), pid),
            )
        else:
            cur.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                (hash_password(new_password), pid),
            )
        cur.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (reset_id,))
        attendance_system.conn.commit()
        return {"success": True, "message": "Password updated. You can now log in.",
                "redirect_url": "/login"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request})


# ==================================================================
# Email status / test (admin)
# ==================================================================

@app.get("/api/admin/email-status")
async def email_status(session: Dict[str, Any] = Depends(require_admin_access)):
    """Whether SMTP is configured, plus the most recent send attempts."""
    try:
        import mailer as _m
        recent = []
        try:
            rows = attendance_system.conn.execute(
                "SELECT to_email, subject, kind, status, error, sent_at "
                "FROM email_log ORDER BY id DESC LIMIT 20"
            ).fetchall()
            recent = [{"to": r[0], "subject": r[1], "kind": r[2], "status": r[3],
                       "error": r[4], "sent_at": str(r[5])[:19]} for r in rows]
        except Exception:
            pass  # table may not exist until migrate_phase3 runs

        counts = {"sent": 0, "failed": 0, "skipped": 0}
        for r in recent:
            if r["status"] in counts:
                counts[r["status"]] += 1

        return {
            "success": True,
            "configured": _m.is_configured(),
            "from_name": os.getenv("SMTP_FROM_NAME", "CDAC Attendance"),
            "smtp_user": os.getenv("SMTP_USER", ""),
            "base_url": _m.base_url(),
            "recent": recent,
            "recent_counts": counts,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/admin/audit-log")
async def get_audit_log(limit: int = 100, action: Optional[str] = None,
                        session: Dict[str, Any] = Depends(require_admin_access)):
    """Recent sensitive actions: who marked/deleted/approved what."""
    try:
        limit = max(1, min(int(limit or 100), 500))
        cur = attendance_system.conn.cursor()
        if action:
            rows = cur.execute(
                "SELECT actor_type, actor_name, action, target, details, created_at "
                "FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT ?", (action, limit)
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT actor_type, actor_name, action, target, details, created_at "
                "FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return {"success": True, "entries": [{
            "actor_type": r[0], "actor_name": r[1], "action": r[2],
            "target": r[3], "details": r[4], "at": str(r[5])[:19],
        } for r in rows]}
    except Exception as e:
        return {"success": False, "message": str(e), "entries": []}


@app.post("/api/admin/email-test")
async def email_test(data: dict = Body(...), session: Dict[str, Any] = Depends(require_admin_access)):
    """Send a test email to verify SMTP settings."""
    try:
        import mailer as _m
        to = (data.get("to") or "").strip()
        if not to or "@" not in to:
            return {"success": False, "message": "Enter a valid email address"}
        if not _m.is_configured():
            return {"success": False,
                    "message": "SMTP not configured. Set SMTP_USER and SMTP_PASSWORD in .env, then restart."}
        html = _m.render_email(
            "SMTP test successful",
            "If you can read this, the attendance system can send email.",
            _m.stat_table([("Sent at", datetime.now().strftime("%d %b %Y, %H:%M")),
                           ("From", os.getenv("SMTP_USER", ""))]),
        )
        ok, msg = _m.send_email(to, "CDAC Attendance — SMTP test", html, kind="test")
        return {"success": ok, "message": "Test email sent." if ok else msg}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/attendance/student/{student_id}/slots")
async def get_student_slot_attendance(student_id: int, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
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
async def detect_attendance(image_data: DetectionImage, session: Dict[str, Any] = Depends(require_staff_or_terminal)):
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
                        # Mark attendance (carry the student's batch + late flag)
                        late = compute_is_late()
                        s_course = cursor.execute(
                            "SELECT course_id FROM students WHERE id = ?", (student_id,)
                        ).fetchone()
                        s_course_id = s_course[0] if s_course else None

                        # No attendance on a Sunday or a holiday, by any route.
                        ok_day, why = working_days.check(
                            attendance_system.conn, s_course_id, today)
                        if not ok_day:
                            recognized_students.append({
                                "student_id": student_id,
                                "student_name": student_name,
                                "status": "non_working_day",
                                "message": why,
                                "location": face_data['location'],
                            })
                            continue

                        slot = attendance_manager.get_current_slot() if attendance_manager else None
                        slot_key = (slot or {}).get("slot_key")
                        # Record which module this class was, at mark time — a
                        # later timetable change must not rewrite history.
                        subject_id = timetable.subject_for_slot(
                            attendance_system.conn, s_course_id, today, slot_key)
                        cursor.execute('''
                            INSERT INTO attendance (student_id, date, time_in, is_manual, is_late,
                                                    course_id, session_type, subject_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (student_id, today, datetime.now().time().strftime('%H:%M:%S'),
                              False, late, s_course_id, slot_key, subject_id))

                        attendance_system.conn.commit()
                        status = "marked"
                        message = f"Attendance marked for {student_name}"
                        if late:
                            message += " (late)"
                    
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
async def start_registration(student_info: StudentInfo, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Start registration session"""
    session_id, message = attendance_system.start_registration_session(
        student_info.name, student_info.email, student_info.student_id
    )
    
    if session_id:
        return {"success": True, "session_id": session_id, "message": message}
    else:
        raise HTTPException(status_code=400, detail=message)

@app.post("/api/upload_face_photo")
async def upload_face_photo(photo_data: FacePhotoData, session: Dict[str, Any] = Depends(require_any_authenticated)):
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
async def complete_registration(reg_data: RegistrationComplete, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Complete registration"""
    success, message = attendance_system.complete_registration(reg_data.session_id)
    
    if success:
        return {"success": True, "message": message}
    else:
        raise HTTPException(status_code=400, detail=message)

@app.get("/api/attendance/today")
async def get_today_attendance(session: Dict[str, Any] = Depends(require_staff_or_terminal)):
    """Get today's attendance"""
    return attendance_system.get_today_attendance()

@app.get("/api/students/count")
async def get_student_count(session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Get total number of students"""
    count = attendance_system.get_student_count()
    return {"total_students": count}

@app.get("/api/system/status")
async def get_system_status(response: Response):
    """Public health probe. Used by healthcheck.sh from cron, so it must
    actually exercise the database rather than assert it is fine."""
    db_ok = True
    db_error = None
    try:
        attendance_system.conn.execute("SELECT 1 FROM students LIMIT 1").fetchone()
    except Exception as e:
        db_ok = False
        db_error = str(e)

    healthy = db_ok
    # A non-200 is what makes this usable as a cron/monitoring target.
    if not healthy:
        response.status_code = 503

    payload = {
        "status": "ok" if healthy else "degraded",
        "face_recognition_available": FACE_RECOGNITION_AVAILABLE,
        "opencv_available": OPENCV_AVAILABLE,
        "database_connected": db_ok,
        "students_loaded": len(attendance_system.known_face_encodings)
    }
    if db_error:
        payload["error"] = db_error
    return payload

@app.get("/api/students/list")
async def list_students(session: Dict[str, Any] = Depends(require_teacher_or_admin)):
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
async def get_dashboard_stats(session: Dict[str, Any] = Depends(require_teacher_or_admin)):
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
async def get_student_attendance(student_id: int, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Get detailed attendance data for a specific student"""
    try:
        data = attendance_system.get_student_attendance_data(student_id)
        return data
    except Exception as e:
        return {"success": False, "message": str(e)}


# ==================================================================
# Student profile-change requests (teacher-approved)
# ==================================================================

# Fields a student may propose changing. Roll number is included because it is
# their login username and does sometimes need correcting, but it is re-checked
# for uniqueness on approval. Anything not listed here is silently ignored.
EDITABLE_PROFILE_FIELDS = {
    "name": "Full name",
    "email": "Email address",
    "dob": "Date of birth",
    "student_id": "Roll number",
}


@app.get("/api/student/profile")
async def student_profile(session: Dict[str, Any] = Depends(require_student)):
    """The logged-in student's current editable details + any pending request."""
    try:
        sid = session.get("user_info", {}).get("id")
        cur = attendance_system.conn.cursor()
        row = cur.execute(
            "SELECT s.student_id, s.name, s.email, s.dob, c.name "
            "FROM students s LEFT JOIN courses c ON c.id = s.course_id WHERE s.id = ?",
            (sid,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Student not found")

        pending = cur.execute(
            "SELECT id, changes, created_at FROM profile_change_requests "
            "WHERE student_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()

        return {
            "success": True,
            "profile": {"student_id": row[0], "name": row[1], "email": row[2],
                        "dob": row[3], "batch": row[4] or "-"},
            "editable_fields": EDITABLE_PROFILE_FIELDS,
            "pending_request": ({
                "id": pending[0],
                "changes": json.loads(pending[1]) if pending[1] else {},
                "created_at": str(pending[2])[:19],
            } if pending else None),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/student/profile-request")
async def request_profile_change(data: dict = Body(...),
                                 session: Dict[str, Any] = Depends(require_student)):
    """Propose changes to your own details. Applied only once a teacher approves."""
    try:
        sid = session.get("user_info", {}).get("id")
        cur = attendance_system.conn.cursor()

        row = cur.execute(
            "SELECT student_id, name, email, dob, course_id FROM students WHERE id = ?", (sid,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Student not found")
        current = {"student_id": row[0], "name": row[1], "email": row[2], "dob": row[3]}
        course_id = row[4]

        if cur.execute(
            "SELECT 1 FROM profile_change_requests WHERE student_id = ? AND status = 'pending'",
            (sid,),
        ).fetchone():
            return {"success": False,
                    "message": "You already have a change request awaiting approval."}

        # Keep only fields that are editable AND actually different
        incoming = data.get("changes") or {}
        proposed = {}
        for field in EDITABLE_PROFILE_FIELDS:
            if field not in incoming:
                continue
            new_val = str(incoming[field] or "").strip()
            if not new_val or new_val == (current.get(field) or ""):
                continue
            proposed[field] = new_val

        if not proposed:
            return {"success": False, "message": "Nothing changed."}

        # Validate up front so the student gets immediate feedback
        if "email" in proposed and "@" not in proposed["email"]:
            return {"success": False, "message": "That email address does not look valid."}
        for field in ("email", "student_id"):
            if field in proposed and cur.execute(
                "SELECT 1 FROM students WHERE " + field + " = ? AND id != ?",
                (proposed[field], sid),
            ).fetchone():
                label = EDITABLE_PROFILE_FIELDS[field].lower()
                return {"success": False, "message": "That " + label + " is already in use."}

        cur.execute(
            "INSERT INTO profile_change_requests "
            "(student_id, course_id, changes, old_values, reason, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (sid, course_id, json.dumps(proposed),
             json.dumps({k: current.get(k) for k in proposed}),
             (data.get("reason") or "").strip() or None),
        )
        attendance_system.conn.commit()
        return {"success": True,
                "message": "Submitted. Your teacher will review the change.",
                "changes": proposed}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/student/profile-requests")
async def my_profile_requests(session: Dict[str, Any] = Depends(require_student)):
    """The student's own change-request history."""
    try:
        sid = session.get("user_info", {}).get("id")
        rows = attendance_system.conn.execute(
            "SELECT id, changes, status, created_at, review_note "
            "FROM profile_change_requests WHERE student_id = ? ORDER BY id DESC LIMIT 20",
            (sid,),
        ).fetchall()
        return {"success": True, "requests": [{
            "id": r[0],
            "changes": json.loads(r[1]) if r[1] else {},
            "status": r[2], "created_at": str(r[3])[:19], "review_note": r[4],
        } for r in rows]}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/teacher/profile-requests")
async def teacher_profile_requests(status: str = "pending",
                                   session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Profile-change requests for the teacher's assigned batches."""
    try:
        allowed = teacher_allowed_course_ids(session)
        rows = attendance_system.conn.execute(
            "SELECT r.id, s.student_id, s.name, r.course_id, c.name, "
            "r.changes, r.old_values, r.reason, r.status, r.created_at "
            "FROM profile_change_requests r "
            "JOIN students s ON s.id = r.student_id "
            "LEFT JOIN courses c ON c.id = r.course_id "
            "WHERE r.status = ? ORDER BY r.created_at DESC",
            (status,),
        ).fetchall()
        out = []
        for r in rows:
            if allowed is not None and r[3] not in allowed:
                continue
            changes = json.loads(r[5]) if r[5] else {}
            old = json.loads(r[6]) if r[6] else {}
            out.append({
                "id": r[0], "roll_no": r[1], "student_name": r[2],
                "batch": r[4] or "-", "status": r[8], "created_at": str(r[9])[:19],
                "reason": r[7],
                "diff": [{"field": f, "label": EDITABLE_PROFILE_FIELDS.get(f, f),
                          "from": old.get(f) or "(blank)", "to": v}
                         for f, v in changes.items()],
            })
        return {"success": True, "requests": out, "count": len(out)}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/teacher/profile-requests/action")
async def act_on_profile_requests(data: dict = Body(...),
                                  session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Approve (applies the change) or reject profile-change requests."""
    try:
        ids = data.get("ids") or []
        action = (data.get("action") or "").strip().lower()
        note = (data.get("note") or "").strip() or None
        if not ids or action not in ("approve", "reject"):
            return {"success": False, "message": "Provide ids and action ('approve' or 'reject')"}

        allowed = teacher_allowed_course_ids(session)
        reviewer = session.get("user_info", {}).get("id")
        cur = attendance_system.conn.cursor()
        processed, skipped = 0, []

        for rid in ids:
            row = cur.execute(
                "SELECT student_id, course_id, changes, status "
                "FROM profile_change_requests WHERE id = ?", (rid,),
            ).fetchone()
            if not row or row[3] != "pending":
                continue
            student_db_id, course_id, changes_json, _ = row
            if allowed is not None and course_id not in allowed:
                continue

            if action == "approve":
                changes = json.loads(changes_json) if changes_json else {}
                changes = {k: v for k, v in changes.items() if k in EDITABLE_PROFILE_FIELDS}
                if not changes:
                    continue
                # Re-check uniqueness at approval time — the data may have moved
                # since the student submitted the request.
                clash = None
                for field in ("email", "student_id"):
                    if field in changes and cur.execute(
                        "SELECT 1 FROM students WHERE " + field + " = ? AND id != ?",
                        (changes[field], student_db_id),
                    ).fetchone():
                        clash = EDITABLE_PROFILE_FIELDS[field]
                        break
                if clash:
                    skipped.append(clash + " already taken")
                    continue
                assignments = ", ".join(k + " = ?" for k in changes)
                cur.execute(
                    "UPDATE students SET " + assignments + " WHERE id = ?",
                    [*changes.values(), student_db_id],
                )

            cur.execute(
                "UPDATE profile_change_requests SET status = ?, reviewed_by = ?, "
                "reviewed_at = CURRENT_TIMESTAMP, review_note = ? WHERE id = ?",
                ("approved" if action == "approve" else "rejected", reviewer, note, rid),
            )
            processed += 1

        attendance_system.conn.commit()
        audit(session, "profile_request_action", target=str(processed) + " request(s)",
              details="action=" + action)
        msg = ("Approved " if action == "approve" else "Rejected ") + str(processed) + " request(s)"
        if skipped:
            msg += " - " + str(len(skipped)) + " skipped (" + "; ".join(skipped[:3]) + ")"
        return {"success": True, "message": msg, "processed": processed}
    except Exception as e:
        attendance_system.conn.rollback()
        return {"success": False, "message": str(e)}


@app.get("/api/student/calendar")
async def student_calendar(year: Optional[int] = None, month: Optional[int] = None,
                           session: Dict[str, Any] = Depends(require_student)):
    """Per-day attendance for one month, for the logged-in student only.

    Each day is one of: present | absent | holiday | sunday | future |
    before_joining (days before the student enrolled are not counted absent).
    """
    from datetime import date as _date
    from calendar import monthrange
    try:
        sid = session.get("user_info", {}).get("id")
        cur = attendance_system.conn.cursor()
        row = cur.execute(
            "SELECT course_id, joining_date FROM students WHERE id = ?", (sid,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Student not found")
        course_id, joining = row[0], row[1]

        today = _date.today()
        year = int(year or today.year)
        month = int(month or today.month)
        if not 1 <= month <= 12:
            return {"success": False, "message": "Invalid month"}

        first = _date(year, month, 1)
        last = _date(year, month, monthrange(year, month)[1])

        # holidays that apply to this student's batch
        holidays = {}
        for r in cur.execute(
            "SELECT date, name FROM holidays WHERE course_id IS NULL OR course_id = ?",
            (course_id,),
        ).fetchall():
            try:
                holidays[datetime.strptime(str(r[0])[:10], "%Y-%m-%d").date()] = r[1]
            except (ValueError, TypeError):
                continue

        # attendance rows for this month
        marks = {}
        for r in cur.execute(
            "SELECT date, time_in, session_type, is_manual, is_late FROM attendance "
            "WHERE student_id = ? AND date >= ? AND date <= ? ORDER BY time_in",
            (sid, first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")),
        ).fetchall():
            try:
                d = datetime.strptime(str(r[0])[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            entry = marks.setdefault(d, {"time_in": r[1], "sessions": [],
                                         "is_manual": bool(r[3]), "is_late": bool(r[4])})
            if r[2]:
                entry["sessions"].append(r[2])

        join_date = None
        if joining:
            try:
                join_date = datetime.strptime(str(joining)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                join_date = None

        days, present_count, absent_count = [], 0, 0
        d = first
        while d <= last:
            info = {"date": d.strftime("%Y-%m-%d"), "day": d.day,
                    "weekday": d.weekday(), "status": "absent",
                    "time_in": None, "sessions": [], "note": None}
            if d > today:
                info["status"] = "future"
            elif join_date and d < join_date:
                info["status"] = "before_joining"
            elif d.weekday() == 6:
                info["status"] = "sunday"
            elif d in holidays:
                info["status"] = "holiday"
                info["note"] = holidays[d]
            elif d in marks:
                info["status"] = "present"
                info["time_in"] = marks[d]["time_in"]
                info["sessions"] = marks[d]["sessions"]
                if marks[d]["is_late"]:
                    info["note"] = "Late"
                elif marks[d]["is_manual"]:
                    info["note"] = "Marked manually"
                present_count += 1
            else:
                absent_count += 1
            days.append(info)
            d += timedelta(days=1)

        working = present_count + absent_count
        return {
            "success": True,
            "year": year, "month": month,
            "month_label": first.strftime("%B %Y"),
            "first_weekday": first.weekday(),          # 0=Mon .. 6=Sun
            "days": days,
            "summary": {
                "present": present_count, "absent": absent_count,
                "working_days": working,
                "rate": round(present_count / working * 100, 1) if working else 0.0,
            },
            "can_go_next": (year, month) < (today.year, today.month),
        }
    except HTTPException:
        raise
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
        
        audit(session, "clear_all_data", target="ALL student data",
              details="students, attendance, face encodings wiped")
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
    half_day_enabled: Optional[int] = None


class TeacherCreate(BaseModel):
    username: str
    name: Optional[str] = None
    email: Optional[str] = None
    password: str
    batch_ids: Optional[List[int]] = None


class TeacherUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
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
            "SELECT id, name, start_date, end_date, description, is_active, terminal_pin_hash, "
            "COALESCE(half_day_enabled, 0) FROM courses ORDER BY is_active DESC, id"
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
                "half_day_enabled": bool(r[7]),
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
            "INSERT INTO courses (name, start_date, end_date, description, is_active, created_at, "
            "half_day_enabled) VALUES (?, ?, ?, ?, 1, ?, ?)",
            (name, start_date, end_date, data.description, datetime.now().isoformat(),
             1 if data.half_day_enabled else 0),
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
        if data.half_day_enabled is not None:
            fields.append("half_day_enabled = ?")
            values.append(1 if data.half_day_enabled else 0)
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
            "SELECT id, username, name, is_active, email FROM users WHERE role = 'teacher' ORDER BY username"
        ).fetchall():
            batches = cur.execute(
                "SELECT c.id, c.name FROM teacher_batches tb "
                "JOIN courses c ON c.id = tb.course_id WHERE tb.user_id = ?",
                (r[0],),
            ).fetchall()
            teachers.append({
                "id": r[0], "username": r[1], "name": r[2], "is_active": bool(r[3]),
                "email": r[4] or "",
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
            "INSERT INTO users (username, name, email, password_hash, role, is_active, must_change_password) "
            "VALUES (?, ?, ?, ?, 'teacher', 1, 1)",
            (username, (data.name or username).strip(), (data.email or "").strip() or None,
             hash_password(data.password)),
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
        if data.email is not None:
            fields.append("email = ?"); values.append(data.email.strip() or None)
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
        
        audit(session, "delete_student", target=student[0],
              details=f"student_db_id={student_id}", course_id=student[1])
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


def send_welcome_emails(recipients):
    """Background task: email new students their login details.

    recipients: list of (email, name, roll_no, plain_password, batch_name)
    Failures are logged to email_log by mailer; they never break onboarding.
    """
    try:
        import mailer as _mailer
        import reports as _reports
        if not _mailer.is_configured():
            print("[MAIL] Welcome emails skipped: SMTP not configured")
            return
        sent = 0
        for email, name, roll, pw, batch in recipients:
            if not email or "@" not in email:
                continue
            ok, msg = _mailer.send_email(
                email,
                "Your attendance portal account",
                _reports.welcome_email(name, roll, pw, batch),
                kind="welcome",
            )
            if ok:
                sent += 1
            else:
                print(f"[MAIL] welcome -> {email} failed: {msg}")
        print(f"[MAIL] Welcome emails sent: {sent}/{len(recipients)}")
    except Exception as e:
        print(f"[MAIL] Welcome email task error: {e}")


def _batch_name_for(course_id):
    try:
        row = attendance_system.conn.execute(
            "SELECT name FROM courses WHERE id = ?", (course_id,)
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


@app.post("/api/students/{student_id}/send-welcome")
async def send_welcome_to_student(student_id: int, background_tasks: BackgroundTasks,
                                  session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Email a student their login details.

    Passwords are stored hashed and cannot be read back, so this RESETS the
    password to their DOB default and emails that. The student is then asked to
    change it at next login.
    """
    try:
        import mailer as _m
        cur = attendance_system.conn.cursor()
        row = cur.execute(
            "SELECT s.student_id, s.name, s.email, s.dob, s.course_id, c.name "
            "FROM students s LEFT JOIN courses c ON c.id = s.course_id WHERE s.id = ?",
            (student_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Student not found")

        allowed = teacher_allowed_course_ids(session)
        if allowed is not None and row[4] not in allowed:
            raise HTTPException(status_code=403, detail="Not assigned to this student's batch")

        roll, name, email, dob, _cid, batch = row
        if not email or "@" not in str(email):
            return {"success": False, "message": f"{name} has no email address on file"}
        if not _m.is_configured():
            return {"success": False,
                    "message": "Email is not configured. Set SMTP_USER / SMTP_PASSWORD in .env."}

        new_pw = default_student_password(dob)
        cur.execute(
            "UPDATE students SET password_hash = ?, must_change_password = 1 WHERE id = ?",
            (hash_password(new_pw), student_id),
        )
        attendance_system.conn.commit()

        background_tasks.add_task(send_welcome_emails, [(email, name, roll, new_pw, batch)])
        audit(session, "send_welcome", target=roll, details="password reset + login details emailed")
        return {"success": True,
                "message": f"Login details sent to {email} (password reset to their DOB)."}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/students/add")
async def add_single_student(background_tasks: BackgroundTasks, data: dict = Body(...),
                             session: Dict[str, Any] = Depends(require_teacher_or_admin)):
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

        plain_pw = default_student_password(dob)
        cur.execute('''
            INSERT INTO students
            (student_id, name, email, dob, course_id, password_hash, must_change_password, status)
            VALUES (?, ?, ?, ?, ?, ?, 1, 'pending_registration')
        ''', (student_id, name, email, dob or None, course_id, hash_password(plain_pw)))
        attendance_system.conn.commit()

        # Email their login details (background; no-op if SMTP unconfigured)
        if data.get("send_welcome", True):
            background_tasks.add_task(
                send_welcome_emails,
                [(email, name, student_id, plain_pw, _batch_name_for(course_id))],
            )
        return {"success": True, "message": f"Added {name} (pending face registration)"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/students/bulk-upload")
async def bulk_upload_students(background_tasks: BackgroundTasks, file: UploadFile = File(...),
                               session: Dict[str, Any] = Depends(require_teacher_or_admin)):
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
        welcome_queue = []   # (email, name, roll, plain_pw, batch) for welcome emails

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

                plain_pw = default_student_password(dob)
                cursor.execute('''
                    INSERT INTO students
                    (student_id, name, email, joining_date, dob, course_id,
                     password_hash, must_change_password, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'pending_registration')
                ''', (
                    student_id, name, email, joining_date, dob or None, course_id,
                    hash_password(plain_pw),
                ))
                added_count += 1
                welcome_queue.append((email, name, student_id, plain_pw,
                                      batch_name or _batch_name_for(course_id)))

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
                skipped_count += 1

        attendance_system.conn.commit()

        # Email login details to the new students (background task)
        if welcome_queue:
            background_tasks.add_task(send_welcome_emails, welcome_queue)

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
            secure=COOKIE_SECURE,
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
async def mark_manual_session_attendance_api(data: dict = Body(...), session: Dict[str, Any] = Depends(require_teacher_or_admin)):
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

        # A teacher must not be able to mark a Sunday or a holiday present.
        # Clearing records ('absent') stays allowed — that is how you undo a
        # mistake, including one made before this rule existed.
        if status == "present":
            ok_day, why = working_days.check(attendance_system.conn, course_id, the_date)
            if not ok_day:
                return {"success": False, "message": why}

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
                subject_id = timetable.subject_for_slot(
                    attendance_system.conn, course_id, the_date, session_type)
                cur.execute(
                    "INSERT INTO attendance (student_id, date, time_in, status, is_manual, "
                    "manual_reason, session_type, course_id, subject_id) "
                    "VALUES (?, ?, ?, 'present', 1, ?, ?, ?, ?)",
                    (sid, the_date, now_time, reason, session_type, course_id, subject_id),
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
        audit(session, "bulk_mark", target=f"{len(target_ids)} student(s)",
              details=f"date={the_date} status={status} session={session_type or 'whole day'} affected={affected}",
              course_id=course_id)
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

        # Nothing to dispute on a day nobody could have attended.
        ok_day, why = working_days.check(
            attendance_system.conn, info.get("course_id"), d)
        if not ok_day:
            return {"success": False,
                    "message": f"{why.split('—')[0].strip()}, so there is no attendance to dispute."}

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
            "id": r[0], "date": r[1], "day_name": working_days.day_name(r[1]),
            "session_type": r[2] or "Whole day", "reason": r[3],
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
            # Day name is computed here rather than in the browser so the
            # teacher sees the same weekday the server will act on.
            _ok, _why = working_days.check(attendance_system.conn, r[4], r[6])
            out.append({
                "id": r[0], "student_db_id": r[1], "roll_no": r[2], "student_name": r[3],
                "course_id": r[4], "batch": r[5] or "—", "date": r[6],
                "day_name": working_days.day_name(r[6]),
                "is_working_day": _ok, "non_working_reason": _why,
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
        blocked = []        # requests naming a Sunday/holiday, left pending

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
                # Approving is a marking action, so the same rule applies. A
                # grievance raised before this rule existed could still name a
                # Sunday; approving it would create attendance nothing counts.
                ok_day, why = working_days.check(
                    attendance_system.conn, course_id, the_date)
                if not ok_day:
                    blocked.append({"id": gid, "reason": why})
                    continue
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
                    subject_id = timetable.subject_for_slot(
                        attendance_system.conn, course_id, the_date, session_type)
                    cur.execute(
                        "INSERT INTO attendance (student_id, date, time_in, status, is_manual, "
                        "manual_reason, session_type, course_id, subject_id) "
                        "VALUES (?, ?, ?, 'present', 1, ?, ?, ?, ?)",
                        (student_db_id, the_date,
                         datetime.now(timezone).strftime("%H:%M:%S"),
                         "Grievance approved", session_type, course_id, subject_id),
                    )
                    marked += 1

            cur.execute(
                "UPDATE grievances SET status = ?, reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP, "
                "review_note = ? WHERE id = ?",
                (new_status, reviewer, note, gid),
            )
            processed += 1

        attendance_system.conn.commit()
        audit(session, "grievance_action", target=f"{processed} request(s)",
              details=f"action={action} marked_present={marked}")
        msg = f"{new_status.title()} {processed} request(s)"
        if action == "approve":
            msg += f" — {marked} student(s) marked present"
        if blocked:
            msg += (f". {len(blocked)} skipped: "
                    + "; ".join(b["reason"] for b in blocked[:3])
                    + ("…" if len(blocked) > 3 else ""))
        return {"success": True, "message": msg, "processed": processed,
                "marked": marked, "blocked": blocked}
    except Exception as e:
        attendance_system.conn.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/terminal/manual-request")
async def terminal_manual_request(data: dict = Body(...),
                                  session: Dict[str, Any] = Depends(require_terminal)):
    """Fallback for a student the terminal camera cannot recognise.

    Deliberately does NOT mark anyone present: it files a pending grievance
    that a teacher must approve. Anyone standing at the kiosk can type any
    roll number, so a human has to be in the loop — but the student is no
    longer stuck when the camera fails.
    """
    try:
        info = session.get("user_info", {})
        course_id = info.get("course_id")
        roll = (data.get("roll_no") or "").strip()
        reason = (data.get("reason") or "").strip() or "Camera could not recognise me at the terminal"
        if not roll:
            return {"success": False, "message": "Enter your roll number"}

        cur = attendance_system.conn.cursor()
        row = cur.execute(
            "SELECT id, name FROM students WHERE student_id = ? AND course_id = ? "
            "AND status IN ('active','pending_registration')",
            (roll, course_id),
        ).fetchone()
        if not row:
            # Same message either way — the kiosk is a public surface, so it
            # must not confirm which roll numbers exist.
            return {"success": False,
                    "message": "No student with that roll number in this batch"}
        student_db_id, student_name = row[0], row[1]

        today = date.today().strftime("%Y-%m-%d")
        ok_day, why = working_days.check(attendance_system.conn, course_id, today)
        if not ok_day:
            return {"success": False, "message": why}

        already = cur.execute(
            "SELECT 1 FROM attendance WHERE student_id = ? AND date = ?",
            (student_db_id, today),
        ).fetchone()
        if already:
            return {"success": True, "already": True,
                    "message": f"{student_name}, you are already marked present today."}

        dup = cur.execute(
            "SELECT 1 FROM grievances WHERE student_id = ? AND date = ? AND status = 'pending'",
            (student_db_id, today),
        ).fetchone()
        if dup:
            return {"success": True, "already": True,
                    "message": f"{student_name}, your request for today is already awaiting approval."}

        cur.execute(
            "INSERT INTO grievances (student_id, course_id, date, session_type, reason, status) "
            "VALUES (?, ?, ?, NULL, ?, 'pending')",
            (student_db_id, course_id, today, reason),
        )
        attendance_system.conn.commit()
        audit(session, "terminal_manual_request", target=f"student:{roll}",
              details=f"course_id={course_id}")
        return {"success": True,
                "message": f"Thanks {student_name.split()[0]} — sent to your teacher for approval."}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Biometric consent (India DPDP Act 2023)
#
# Face encodings are biometric personal data. The Act requires consent that is
# free, informed and specific, a record of it, and a way to withdraw. Bump
# CONSENT_POLICY_VERSION whenever the privacy notice changes materially —
# students are then asked again rather than silently carried over.
# ---------------------------------------------------------------------------
CONSENT_POLICY_VERSION = os.getenv("CONSENT_POLICY_VERSION", "1.0")
CONSENT_PURPOSE = "biometric_attendance"


def consent_state(student_db_id: int):
    """(has_valid_consent, latest_record_or_None) for the current policy version."""
    try:
        row = attendance_system.conn.execute(
            "SELECT id, policy_version, granted, granted_at, withdrawn_at "
            "FROM consent_records WHERE student_id = ? AND purpose = ? "
            "ORDER BY id DESC LIMIT 1",
            (student_db_id, CONSENT_PURPOSE),
        ).fetchone()
    except Exception:
        return True, None       # pre-migration database: don't lock anyone out
    if not row:
        return False, None
    record = {"id": row[0], "policy_version": row[1], "granted": bool(row[2]),
              "granted_at": row[3], "withdrawn_at": row[4]}
    valid = record["granted"] and record["policy_version"] == CONSENT_POLICY_VERSION
    return valid, record


@app.get("/api/student/consent")
async def get_consent(session: Dict[str, Any] = Depends(require_student)):
    """Whether this student has consented to biometric processing."""
    try:
        sid = session.get("user_info", {}).get("id")
        valid, record = consent_state(sid)
        return {"success": True, "consented": valid,
                "policy_version": CONSENT_POLICY_VERSION, "record": record}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/student/consent")
async def set_consent(request: Request, data: dict = Body(...),
                      session: Dict[str, Any] = Depends(require_student)):
    """Grant or withdraw consent for face-based attendance.

    Withdrawal is not merely a flag: it deletes the stored face encoding, which
    is what actually stops the processing. Attendance history is retained — it
    is an academic record, not biometric data.
    """
    try:
        info = session.get("user_info", {})
        sid = info.get("id")
        granted = bool(data.get("granted"))

        cur = attendance_system.conn.cursor()
        cur.execute(
            "INSERT INTO consent_records (student_id, purpose, policy_version, granted, "
            "granted_at, withdrawn_at, ip_address, user_agent) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)",
            (sid, CONSENT_PURPOSE, CONSENT_POLICY_VERSION, 1 if granted else 0,
             None if granted else datetime.now().isoformat(),
             (request.client.host if request.client else None),
             request.headers.get("user-agent", "")[:300]),
        )

        message = "Consent recorded. Thank you."
        if not granted:
            cur.execute(
                "UPDATE students SET face_encoding = NULL, photo_count = 0 WHERE id = ?", (sid,))
            try:
                cur.execute("DELETE FROM face_encodings WHERE student_id = ?", (sid,))
            except Exception:
                pass
            message = ("Consent withdrawn. Your face data has been deleted and you will "
                       "no longer be recognised by the camera — your teacher will mark you "
                       "manually. Your attendance history is unchanged.")
        attendance_system.conn.commit()

        if not granted:
            # Drop the encoding from the in-memory match set too, otherwise the
            # camera keeps recognising them until the next restart.
            try:
                attendance_system.load_student_faces()
            except Exception:
                pass    # the encoding is already gone from the database

        audit(session, "consent_change", target=f"student:{sid}",
              details=f"granted={granted} version={CONSENT_POLICY_VERSION}")
        return {"success": True, "consented": granted, "message": message}
    except Exception as e:
        attendance_system.conn.rollback()
        return {"success": False, "message": str(e)}


@app.get("/api/admin/consent-status")
async def consent_status_report(course_id: Optional[int] = None,
                                session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Who has and has not consented — the record you need if anyone asks."""
    try:
        allowed = teacher_allowed_course_ids(session)
        sql = ("SELECT s.id, s.student_id, s.name, s.course_id, c.name, "
               "       (SELECT granted FROM consent_records r WHERE r.student_id = s.id "
               "        AND r.purpose = ? AND r.policy_version = ? ORDER BY r.id DESC LIMIT 1), "
               "       (SELECT granted_at FROM consent_records r WHERE r.student_id = s.id "
               "        AND r.purpose = ? AND r.policy_version = ? ORDER BY r.id DESC LIMIT 1), "
               "       (s.face_encoding IS NOT NULL) "
               "FROM students s LEFT JOIN courses c ON c.id = s.course_id "
               "WHERE s.status IN ('active','pending_registration')")
        params = [CONSENT_PURPOSE, CONSENT_POLICY_VERSION,
                  CONSENT_PURPOSE, CONSENT_POLICY_VERSION]
        if course_id:
            sql += " AND s.course_id = ?"
            params.append(course_id)
        sql += " ORDER BY s.name"

        rows = attendance_system.conn.execute(sql, params).fetchall()
        out, granted_n = [], 0
        for r in rows:
            if allowed is not None and r[3] not in allowed:
                continue
            ok = bool(r[5])
            granted_n += 1 if ok else 0
            out.append({"student_db_id": r[0], "roll_no": r[1], "name": r[2],
                        "batch": r[4] or "—", "consented": ok,
                        "granted_at": r[6], "has_face_data": bool(r[7])})
        return {"success": True, "policy_version": CONSENT_POLICY_VERSION,
                "students": out, "total": len(out), "consented": granted_n,
                "pending": len(out) - granted_n}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_notice(request: Request):
    """The privacy notice students consent to. Public by necessity — it has to
    be readable before signing in."""
    return templates.TemplateResponse("privacy.html", {
        "request": request, "policy_version": CONSENT_POLICY_VERSION,
        "retention_days": int(os.getenv("BIOMETRIC_RETENTION_DAYS", "90") or 90),
    })


# ---------------------------------------------------------------------------
# Subjects / modules and the weekly timetable
# ---------------------------------------------------------------------------

@app.get("/api/courses/{course_id}/subjects")
async def list_subjects_api(course_id: int,
                            session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Modules within a batch."""
    try:
        assert_course_allowed(session, course_id)
        return {"success": True,
                "subjects": timetable.list_subjects(attendance_system.conn, course_id,
                                                    active_only=False)}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/courses/{course_id}/subjects")
async def create_subject(course_id: int, data: dict = Body(...),
                         session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Add a module to a batch."""
    try:
        assert_course_allowed(session, course_id)
        name = (data.get("name") or "").strip()
        if not name:
            return {"success": False, "message": "Subject name is required"}
        try:
            min_att = float(data.get("min_attendance") or 75)
        except (TypeError, ValueError):
            min_att = 75.0
        cur = attendance_system.conn.cursor()
        cur.execute(
            "INSERT INTO subjects (course_id, name, code, min_attendance, start_date, end_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (course_id, name, (data.get("code") or "").strip() or None, min_att,
             (data.get("start_date") or None), (data.get("end_date") or None)),
        )
        attendance_system.conn.commit()
        audit(session, "subject_create", target=name, course_id=course_id)
        return {"success": True, "message": f"Subject '{name}' added", "id": cur.lastrowid}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.put("/api/subjects/{subject_id}")
async def update_subject(subject_id: int, data: dict = Body(...),
                         session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Rename a module, change its code, minimum, dates or active flag."""
    try:
        row = attendance_system.conn.execute(
            "SELECT course_id FROM subjects WHERE id = ?", (subject_id,)
        ).fetchone()
        if not row:
            return {"success": False, "message": "Subject not found"}
        assert_course_allowed(session, row[0])

        fields, params = [], []
        for key, col in (("name", "name"), ("code", "code"),
                         ("start_date", "start_date"), ("end_date", "end_date")):
            if key in data:
                fields.append(f"{col} = ?")
                params.append((data.get(key) or None))
        if "min_attendance" in data:
            try:
                fields.append("min_attendance = ?")
                params.append(float(data.get("min_attendance") or 75))
            except (TypeError, ValueError):
                fields.pop()
        if "is_active" in data:
            fields.append("is_active = ?")
            params.append(1 if data.get("is_active") else 0)
        if not fields:
            return {"success": False, "message": "Nothing to update"}

        params.append(subject_id)
        attendance_system.conn.execute(
            f"UPDATE subjects SET {', '.join(fields)} WHERE id = ?", params)
        attendance_system.conn.commit()
        audit(session, "subject_update", target=f"subject:{subject_id}", course_id=row[0])
        return {"success": True, "message": "Subject updated"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.delete("/api/subjects/{subject_id}")
async def delete_subject(subject_id: int,
                         session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Deactivate a module.

    Never a hard delete: attendance rows point at subject_id, and removing the
    row would orphan that history.
    """
    try:
        row = attendance_system.conn.execute(
            "SELECT course_id, name FROM subjects WHERE id = ?", (subject_id,)
        ).fetchone()
        if not row:
            return {"success": False, "message": "Subject not found"}
        assert_course_allowed(session, row[0])
        attendance_system.conn.execute(
            "UPDATE subjects SET is_active = 0 WHERE id = ?", (subject_id,))
        attendance_system.conn.execute(
            "UPDATE timetable SET subject_id = NULL WHERE subject_id = ?", (subject_id,))
        attendance_system.conn.commit()
        audit(session, "subject_delete", target=row[1], course_id=row[0])
        return {"success": True, "message": f"'{row[1]}' deactivated and removed from the timetable"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/courses/{course_id}/timetable")
async def get_timetable_api(course_id: int,
                            session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """The weekly grid for a batch."""
    try:
        assert_course_allowed(session, course_id)
        return {"success": True,
                "grid": timetable.get_grid(attendance_system.conn, course_id),
                "slots": [{"session_type": s, "label": timetable.SLOT_LABELS[s]}
                          for s in timetable.SLOTS],
                "subjects": timetable.list_subjects(attendance_system.conn, course_id)}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.put("/api/courses/{course_id}/timetable")
async def set_timetable_api(course_id: int, data: dict = Body(...),
                            session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Replace the weekly grid for a batch."""
    try:
        assert_course_allowed(session, course_id)
        n = timetable.set_grid(attendance_system.conn, course_id, data.get("entries") or [])
        audit(session, "timetable_update", target=f"course:{course_id}",
              details=f"{n} slot(s) set", course_id=course_id)
        return {"success": True, "message": f"Timetable saved ({n} slots assigned)"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/student/timetable")
async def student_timetable(session: Dict[str, Any] = Depends(require_student)):
    """A student's own weekly schedule — they previously had no way to see
    their slot times at all."""
    try:
        info = session.get("user_info", {})
        course_id = info.get("course_id")
        if not course_id:
            return {"success": True, "grid": [], "slot_times": {},
                    "message": "You are not assigned to a batch yet"}

        # slot start/end times come from the batch's session_configs
        slot_times = {}
        try:
            for cfg in attendance_manager.get_session_configs(course_id) or []:
                slot_times[cfg.get("session_type")] = {
                    "start": str(cfg.get("start_time"))[:5],
                    "end": str(cfg.get("end_time"))[:5],
                    "is_active": bool(cfg.get("is_active", True)),
                }
        except Exception:
            slot_times = {}

        return {"success": True,
                "grid": timetable.get_grid(attendance_system.conn, course_id),
                "slot_times": slot_times,
                "subjects": timetable.list_subjects(attendance_system.conn, course_id)}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/student/schedule")
async def student_schedule(session: Dict[str, Any] = Depends(require_student)):
    """The published academic calendar for the student's batch."""
    try:
        info = session.get("user_info", {})
        course_id = info.get("course_id")
        if not course_id:
            return {"success": True, "items": [], "holidays": []}

        holidays = attendance_system.conn.execute(
            "SELECT date, name FROM holidays WHERE course_id IS NULL OR course_id = ? "
            "ORDER BY date", (course_id,)
        ).fetchall()
        return {
            "success": True,
            "batch": info.get("batch"),
            "items": timetable.course_calendar(attendance_system.conn, course_id),
            "holidays": [{"date": str(h[0])[:10], "name": h[1],
                          "day_name": working_days.day_name(h[0])} for h in holidays],
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/courses/{course_id}/schedule")
async def course_schedule(course_id: int,
                          session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """The published academic calendar for a batch (staff view)."""
    try:
        assert_course_allowed(session, course_id)
        holidays = attendance_system.conn.execute(
            "SELECT date, name FROM holidays WHERE course_id IS NULL OR course_id = ? "
            "ORDER BY date", (course_id,)
        ).fetchall()
        return {
            "success": True,
            "items": timetable.course_calendar(attendance_system.conn, course_id),
            "holidays": [{"date": str(h[0])[:10], "name": h[1],
                          "day_name": working_days.day_name(h[0])} for h in holidays],
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


EVENT_KINDS = ["event", "exam", "revision", "interview", "placement",
               "induction", "activity", "holiday"]


@app.post("/api/courses/{course_id}/events")
async def create_event(course_id: int, data: dict = Body(...),
                       session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Add a calendar entry for a batch.

    Visible only to that batch's students — every calendar read is scoped by
    course_id. A 'holiday' kind writes to the holidays table instead, because
    that is what excludes the day from attendance.
    """
    try:
        assert_course_allowed(session, course_id)
        title = (data.get("title") or "").strip()
        kind = (data.get("kind") or "event").strip().lower()
        start = working_days.as_date(data.get("start_date"))
        end = working_days.as_date(data.get("end_date")) or start

        if not title:
            return {"success": False, "message": "Title is required"}
        if not start:
            return {"success": False, "message": "A valid start date is required"}
        if end < start:
            return {"success": False, "message": "End date cannot be before the start date"}
        if kind not in EVENT_KINDS:
            return {"success": False, "message": f"Unknown kind '{kind}'"}

        cur = attendance_system.conn.cursor()

        if kind == "holiday":
            # Holidays are a different thing: they stop attendance being marked.
            n, d = 0, start
            while d <= end:
                ds = d.strftime("%Y-%m-%d")
                exists = cur.execute(
                    "SELECT id FROM holidays WHERE date = ? AND "
                    "(course_id = ? OR course_id IS NULL)", (ds, course_id)).fetchone()
                if exists:
                    cur.execute("UPDATE holidays SET name = ? WHERE id = ?", (title, exists[0]))
                else:
                    cur.execute(
                        "INSERT INTO holidays (date, name, type, course_id) "
                        "VALUES (?, ?, 'holiday', ?)", (ds, title, course_id))
                n += 1
                d += timedelta(days=1)
            attendance_system.conn.commit()
            audit(session, "holiday_add", target=title, course_id=course_id)
            return {"success": True,
                    "message": f"'{title}' added as a holiday ({n} day(s)). "
                               f"Attendance cannot be marked on those days."}

        dup = cur.execute(
            "SELECT id FROM academic_events WHERE course_id = ? AND title = ? AND start_date = ?",
            (course_id, title, start.strftime("%Y-%m-%d"))).fetchone()
        if dup:
            return {"success": False,
                    "message": "That entry already exists on that date"}

        cur.execute(
            "INSERT INTO academic_events (course_id, title, kind, start_date, end_date, "
            "notes, coordinator) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (course_id, title, kind, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
             (data.get("notes") or "").strip() or None,
             (data.get("coordinator") or "").strip() or None))
        attendance_system.conn.commit()
        audit(session, "event_add", target=title, course_id=course_id)

        # Sunday events are normal here (mock interviews, picnics), so this is
        # information rather than a warning.
        note = ""
        if start.weekday() == 6:
            note = " (note: that is a Sunday)"
        return {"success": True, "message": f"'{title}' added to the calendar{note}",
                "id": cur.lastrowid}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.delete("/api/events/{event_id}")
async def delete_event(event_id: int,
                       session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Remove a calendar entry."""
    try:
        row = attendance_system.conn.execute(
            "SELECT course_id, title FROM academic_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            return {"success": False, "message": "Entry not found"}
        assert_course_allowed(session, row[0])
        attendance_system.conn.execute(
            "DELETE FROM academic_events WHERE id = ?", (event_id,))
        attendance_system.conn.commit()
        audit(session, "event_delete", target=row[1], course_id=row[0])
        return {"success": True, "message": f"'{row[1]}' removed"}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/student/subjects")
async def student_subject_attendance(session: Dict[str, Any] = Depends(require_student)):
    """Per-module attendance for the logged-in student."""
    try:
        info = session.get("user_info", {})
        return {"success": True,
                "subjects": timetable.subject_attendance(
                    attendance_system.conn, info.get("course_id"), info.get("id"))}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/teacher/batch/{course_id}/subject-analytics")
async def batch_subject_analytics(course_id: int,
                                  session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Batch-wide per-module attendance."""
    try:
        assert_course_allowed(session, course_id)
        return {"success": True,
                "subjects": timetable.subject_attendance(attendance_system.conn, course_id)}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Planned absences (leave requests)
#
# Grievances dispute days already past. These cover days not yet reached, so
# an approved absence is excused rather than counted against the student —
# see reports._approved_leave_dates, which drops approved days from the
# denominator instead of marking anyone present.
# ---------------------------------------------------------------------------
LEAVE_MAX_DAYS = int(os.getenv("LEAVE_MAX_DAYS", "30") or 30)


def _parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@app.post("/api/student/leave")
async def request_leave(data: dict = Body(...), session: Dict[str, Any] = Depends(require_student)):
    """A student applies for a planned absence over a date range."""
    try:
        info = session.get("user_info", {})
        sid = info.get("id")
        start = _parse_date(data.get("start_date"))
        end = _parse_date(data.get("end_date")) or start
        reason = (data.get("reason") or "").strip()

        if not start or not end:
            return {"success": False, "message": "Valid start and end dates are required"}
        if not reason:
            return {"success": False, "message": "Please give a reason for the leave"}
        if end < start:
            return {"success": False, "message": "End date cannot be before the start date"}

        today = date.today()
        if start < today:
            return {"success": False,
                    "message": "Leave must be applied for in advance. To correct a past "
                               "date, raise a dispute instead."}
        span = (end - start).days + 1
        if span > LEAVE_MAX_DAYS:
            return {"success": False, "message": f"Leave cannot exceed {LEAVE_MAX_DAYS} days"}

        cur = attendance_system.conn.cursor()
        # Block overlapping requests so one absence cannot be counted twice.
        dup = cur.execute(
            "SELECT 1 FROM leave_requests WHERE student_id = ? "
            "AND status IN ('pending','approved') AND start_date <= ? AND end_date >= ?",
            (sid, end.strftime("%Y-%m-%d"), start.strftime("%Y-%m-%d")),
        ).fetchone()
        if dup:
            return {"success": False,
                    "message": "You already have a leave request covering some of those dates"}

        cur.execute(
            "INSERT INTO leave_requests (student_id, course_id, start_date, end_date, reason, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (sid, info.get("course_id"), start.strftime("%Y-%m-%d"),
             end.strftime("%Y-%m-%d"), reason),
        )
        attendance_system.conn.commit()
        return {"success": True,
                "message": f"Leave request for {span} day(s) submitted for approval"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/student/leaves")
async def my_leaves(session: Dict[str, Any] = Depends(require_student)):
    """A student's own leave history."""
    try:
        sid = session.get("user_info", {}).get("id")
        rows = attendance_system.conn.execute(
            "SELECT id, start_date, end_date, reason, status, created_at, review_note "
            "FROM leave_requests WHERE student_id = ? ORDER BY start_date DESC",
            (sid,),
        ).fetchall()
        out = []
        for r in rows:
            s, e = _parse_date(str(r[1])[:10]), _parse_date(str(r[2])[:10])
            out.append({
                "id": r[0], "start_date": str(r[1])[:10], "end_date": str(r[2])[:10],
                "start_day": working_days.day_name(r[1]),
                "end_day": working_days.day_name(r[2]),
                "days": ((e - s).days + 1) if (s and e) else 1,
                "reason": r[3], "status": r[4], "created_at": r[5], "review_note": r[6],
                "cancellable": r[4] == "pending",
            })
        return {"success": True, "leaves": out}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/student/leave/{leave_id}/cancel")
async def cancel_leave(leave_id: int, session: Dict[str, Any] = Depends(require_student)):
    """Withdraw one's own leave request while it is still pending."""
    try:
        sid = session.get("user_info", {}).get("id")
        cur = attendance_system.conn.cursor()
        # Scoped by student_id so one student cannot cancel another's request.
        row = cur.execute(
            "SELECT status FROM leave_requests WHERE id = ? AND student_id = ?",
            (leave_id, sid),
        ).fetchone()
        if not row:
            return {"success": False, "message": "Request not found"}
        if row[0] != "pending":
            return {"success": False, "message": f"This request has already been {row[0]}"}
        cur.execute("UPDATE leave_requests SET status = 'cancelled' WHERE id = ?", (leave_id,))
        attendance_system.conn.commit()
        return {"success": True, "message": "Leave request withdrawn"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/teacher/leaves")
async def teacher_leaves(status: str = "pending",
                         session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Leave requests for the teacher's assigned batches (default: pending)."""
    try:
        allowed = teacher_allowed_course_ids(session)  # None = admin (all)
        rows = attendance_system.conn.execute(
            "SELECT l.id, l.student_id, s.student_id, s.name, l.course_id, c.name, "
            "l.start_date, l.end_date, l.reason, l.status, l.created_at "
            "FROM leave_requests l JOIN students s ON s.id = l.student_id "
            "LEFT JOIN courses c ON c.id = l.course_id "
            "WHERE l.status = ? ORDER BY l.start_date ASC",
            (status,),
        ).fetchall()
        out = []
        for r in rows:
            if allowed is not None and r[4] not in allowed:
                continue
            s, e = _parse_date(str(r[6])[:10]), _parse_date(str(r[7])[:10])
            # Working days in the range, so a teacher can see that a
            # Sat-Sun request is really only one day off.
            working = 0
            if s and e:
                d = s
                while d <= e:
                    if working_days.is_working_day(attendance_system.conn, r[4], d):
                        working += 1
                    d += timedelta(days=1)
            out.append({
                "id": r[0], "student_db_id": r[1], "roll_no": r[2], "student_name": r[3],
                "course_id": r[4], "batch": r[5] or "—",
                "start_date": str(r[6])[:10], "end_date": str(r[7])[:10],
                "start_day": working_days.day_name(r[6]),
                "end_day": working_days.day_name(r[7]),
                "days": ((e - s).days + 1) if (s and e) else 1,
                "working_days": working,
                "reason": r[8], "status": r[9], "created_at": r[10],
                "upcoming": bool(s and s >= date.today()),
            })
        return {"success": True, "leaves": out, "count": len(out)}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/teacher/leaves/action")
async def act_on_leaves(data: dict = Body(...),
                        session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Approve or reject leave requests — single or in bulk."""
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

        for lid in ids:
            row = cur.execute(
                "SELECT course_id, status FROM leave_requests WHERE id = ?", (lid,)
            ).fetchone()
            if not row or row[1] != "pending":
                continue
            if allowed is not None and row[0] not in allowed:
                continue  # not this teacher's batch
            cur.execute(
                "UPDATE leave_requests SET status = ?, reviewed_by = ?, "
                "reviewed_at = CURRENT_TIMESTAMP, review_note = ? WHERE id = ?",
                (new_status, reviewer, note, lid),
            )
            processed += 1

        attendance_system.conn.commit()
        audit(session, "leave_action", target=f"{processed} request(s)",
              details=f"action={action}")
        return {"success": True, "message": f"{new_status.title()} {processed} request(s)",
                "processed": processed}
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


# ==================================================================
# Student self-registration of their own face (teacher-approved)
# ==================================================================

@app.post("/api/student/register-face/start")
async def student_register_face_start(session: Dict[str, Any] = Depends(require_student)):
    """Start a capture session for the logged-in student's own face."""
    try:
        info = session.get("user_info", {})
        sid = info.get("id")
        cur = attendance_system.conn.cursor()
        row = cur.execute(
            "SELECT student_id, name, email, face_encoding IS NOT NULL FROM students WHERE id = ?",
            (sid,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Student not found")
        if row[3]:
            return {"success": False, "message": "Your face is already registered. "
                                                 "Ask your teacher if it needs changing."}

        pending = cur.execute(
            "SELECT id FROM face_registration_requests WHERE student_id = ? AND status = 'pending'",
            (sid,),
        ).fetchone()
        if pending:
            return {"success": False,
                    "message": "You already have a registration awaiting teacher approval."}

        session_id = str(uuid.uuid4())
        expires_at = datetime.now() + timedelta(minutes=30)
        cur.execute(
            "INSERT INTO registration_sessions (session_id, student_data, expires_at) VALUES (?, ?, ?)",
            (session_id, json.dumps({"name": row[1], "email": row[2], "student_id": row[0]}),
             expires_at.isoformat()),
        )
        attendance_system.conn.commit()
        return {"success": True, "session_id": session_id, "name": row[1], "student_id": row[0]}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/student/register-face/complete")
async def student_register_face_complete(data: dict = Body(...),
                                         session: Dict[str, Any] = Depends(require_student)):
    """Submit the captured face for teacher approval (does NOT activate it)."""
    try:
        info = session.get("user_info", {})
        sid = info.get("id")
        session_id = (data.get("session_id") or "").strip()
        if not session_id:
            return {"success": False, "message": "Missing session"}

        encoding, photos, score = attendance_system.build_session_encoding(session_id)
        if encoding is None:
            return {"success": False, "message": "No usable photos were captured. Please try again."}
        if photos < 3:
            return {"success": False, "message": f"Please capture at least 3 photos (got {photos})."}

        # Block the same face being registered to someone else
        dup_name, dup_sim = attendance_system.find_matching_student(encoding, exclude_db_id=sid)
        if dup_name:
            return {"success": False,
                    "message": f"This face is already registered to another student ({dup_name}). "
                               f"Please contact your teacher."}

        cur = attendance_system.conn.cursor()
        crow = cur.execute("SELECT course_id FROM students WHERE id = ?", (sid,)).fetchone()
        cur.execute(
            "INSERT INTO face_registration_requests "
            "(student_id, course_id, session_id, photo_count, encoding_blob, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (sid, crow[0] if crow else None, session_id, photos, encoding.tobytes()),
        )
        cur.execute("UPDATE registration_sessions SET status = 'completed' WHERE session_id = ?",
                    (session_id,))
        attendance_system.conn.commit()

        temp_file = f"temp_encodings_{session_id}.npy"
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass

        return {"success": True,
                "message": "Submitted. Your teacher will review and approve your face registration."}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/student/register-face/status")
async def student_register_face_status(session: Dict[str, Any] = Depends(require_student)):
    """Whether the student has a face / a pending or rejected request."""
    try:
        sid = session.get("user_info", {}).get("id")
        cur = attendance_system.conn.cursor()
        row = cur.execute(
            "SELECT face_encoding IS NOT NULL FROM students WHERE id = ?", (sid,)
        ).fetchone()
        has_face = bool(row[0]) if row else False
        req = cur.execute(
            "SELECT status, review_note, created_at FROM face_registration_requests "
            "WHERE student_id = ? ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()
        return {
            "success": True,
            "has_face": has_face,
            "request_status": req[0] if req else None,
            "review_note": req[1] if req else None,
            "requested_at": str(req[2])[:19] if req else None,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/teacher/face-requests")
async def teacher_face_requests(status: str = "pending",
                                session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Face-registration requests for the teacher's assigned batches."""
    try:
        allowed = teacher_allowed_course_ids(session)
        rows = attendance_system.conn.execute(
            "SELECT r.id, r.student_id, s.student_id, s.name, r.course_id, c.name, "
            "r.photo_count, r.status, r.created_at "
            "FROM face_registration_requests r "
            "JOIN students s ON s.id = r.student_id "
            "LEFT JOIN courses c ON c.id = r.course_id "
            "WHERE r.status = ? ORDER BY r.created_at DESC",
            (status,),
        ).fetchall()
        out = []
        for r in rows:
            if allowed is not None and r[4] not in allowed:
                continue
            out.append({
                "id": r[0], "student_db_id": r[1], "roll_no": r[2], "student_name": r[3],
                "course_id": r[4], "batch": r[5] or "-", "photo_count": r[6],
                "status": r[7], "created_at": str(r[8])[:19],
            })
        return {"success": True, "requests": out, "count": len(out)}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/teacher/face-requests/action")
async def act_on_face_requests(data: dict = Body(...),
                               session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Approve (activates the face) or reject self-registration requests."""
    try:
        ids = data.get("ids") or []
        action = (data.get("action") or "").strip().lower()
        note = (data.get("note") or "").strip() or None
        if not ids or action not in ("approve", "reject"):
            return {"success": False, "message": "Provide ids and action ('approve' or 'reject')"}

        allowed = teacher_allowed_course_ids(session)
        reviewer = session.get("user_info", {}).get("id")
        cur = attendance_system.conn.cursor()
        processed = 0

        for rid in ids:
            row = cur.execute(
                "SELECT student_id, course_id, photo_count, encoding_blob, status "
                "FROM face_registration_requests WHERE id = ?", (rid,),
            ).fetchone()
            if not row or row[4] != "pending":
                continue
            student_db_id, course_id, photos, blob, _ = row
            if allowed is not None and course_id not in allowed:
                continue

            if action == "approve" and blob:
                cur.execute(
                    "UPDATE students SET face_encoding = ?, photo_count = ?, status = 'active', "
                    "registration_date = CURRENT_TIMESTAMP WHERE id = ?",
                    (blob, photos, student_db_id),
                )

            cur.execute(
                "UPDATE face_registration_requests SET status = ?, reviewed_by = ?, "
                "reviewed_at = CURRENT_TIMESTAMP, review_note = ? WHERE id = ?",
                ("approved" if action == "approve" else "rejected", reviewer, note, rid),
            )
            processed += 1

        attendance_system.conn.commit()
        if action == "approve" and processed:
            attendance_system.load_student_faces()   # make the new faces recognisable now
        audit(session, "face_request_action", target=f"{processed} request(s)",
              details=f"action={action}")
        return {"success": True,
                "message": f"{'Approved' if action == 'approve' else 'Rejected'} {processed} request(s)",
                "processed": processed}
    except Exception as e:
        attendance_system.conn.rollback()
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


@app.get("/api/admin/institute-report")
async def institute_report_api(month: Optional[str] = None,
                               threshold: float = 75.0,
                               session: Dict[str, Any] = Depends(require_admin_access)):
    """Every active batch in one view. Teachers get per-batch exports; this is
    the institute-wide roll-up that had no equivalent."""
    try:
        import reports as _reports
        today = date.today()
        year, mon = today.year, today.month
        if month:
            try:
                y, m = str(month).split("-")
                year, mon = int(y), int(m)
                if not 1 <= mon <= 12:
                    raise ValueError
            except (ValueError, AttributeError):
                return {"success": False, "message": "month must look like 2026-08"}

        rep = _reports.institute_report(year, mon, threshold)
        return {"success": True, "report": rep}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/admin/institute-export")
async def institute_export(month: Optional[str] = None,
                           threshold: float = 75.0,
                           session: Dict[str, Any] = Depends(require_admin_access)):
    """CSV of the institute-wide summary: one row per batch, plus every
    at-risk student underneath."""
    try:
        import reports as _reports
        today = date.today()
        year, mon = today.year, today.month
        if month:
            try:
                y, m = str(month).split("-")
                year, mon = int(y), int(m)
                if not 1 <= mon <= 12:
                    raise ValueError
            except (ValueError, AttributeError):
                return {"success": False, "message": "month must look like 2026-08"}

        rep = _reports.institute_report(year, mon, threshold)

        out = StringIO()
        w = csv.writer(out)
        w.writerow([f"Institute attendance - {rep['month_label']}"])
        w.writerow([f"Period: {rep['period']}"])
        w.writerow([])
        w.writerow(["Batch", "Students", "Working Days", "Average %", f"Below {threshold:g}%"])
        for b in rep["batches"]:
            w.writerow([b["batch"], b["students"], b["working_days"],
                        b["avg_rate"], b["at_risk_count"]])
        w.writerow([])
        w.writerow(["TOTAL", rep["total_students"], "", rep["avg_rate"], rep["at_risk_total"]])

        w.writerow([])
        w.writerow([f"Students below {threshold:g}%"])
        w.writerow(["Batch", "Roll No", "Name", "Present", "Working Days", "Rate %"])
        for b in rep["batches"]:
            for s in b["at_risk"]:
                w.writerow([b["batch"], s["roll_no"], s["name"], s["present"],
                            s["working_days"], s["rate"]])

        filename = f"institute_attendance_{year}-{mon:02d}.csv"
        return Response(content=out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/attendance/bulk-export")
async def bulk_export_attendance(
    start_date: str,
    end_date: str,
    format: str,
    include_weekends: bool = False,
    include_holidays: bool = False,
    session: Dict[str, Any] = Depends(require_teacher_or_admin)
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
async def get_student_details(student_id: int, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
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
async def export_student_attendance(student_id: int, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
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
async def get_student_session_attendance(student_id: int, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Get detailed session-based attendance data for a specific student"""
    try:
        data = attendance_system.get_student_attendance_data(student_id)
        return data
    except Exception as e:
        return {"success": False, "message": str(e)}
    


@app.get("/api/attendance/today/slots")
async def get_today_slot_attendance(session: Dict[str, Any] = Depends(require_teacher_or_admin)):
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
async def get_class_analytics_data(days: int = 14, course_id: Optional[int] = None, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Endpoint for comprehensive class analytics (optionally scoped to a batch)."""
    try:
        return analytics_manager.get_class_analytics(days=days, course_id=course_id)
    except Exception as e:
        print(f"Error in class analytics API: {e}")
        return {"success": False, "message": str(e)}

@app.get("/api/analytics/heatmap")
async def get_heatmap_data(days: int = 90, course_id: Optional[int] = None, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Per-day attendance % for calendar heatmap"""
    try:
        return analytics_manager.get_heatmap_data(days=days, course_id=course_id)
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/analytics/day-of-week")
async def get_day_of_week_data(days: int = 60, course_id: Optional[int] = None, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """Average attendance % per weekday"""
    try:
        return analytics_manager.get_day_of_week_stats(days=days, course_id=course_id)
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/analytics/at-risk")
async def get_at_risk_data(threshold: int = 75, course_id: Optional[int] = None, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """All students below attendance threshold with streak info"""
    try:
        return analytics_manager.get_at_risk_students(threshold=threshold, course_id=course_id)
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/analytics/student/{student_id}/sparkline")
async def get_student_sparkline(student_id: int, days: int = 14, session: Dict[str, Any] = Depends(require_teacher_or_admin)):
    """14-day per-day attendance sparkline for a single student"""
    try:
        return analytics_manager.get_student_sparkline(student_id=student_id, days=days)
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/attendance/live-count")
async def get_live_attendance_count(session: Dict[str, Any] = Depends(require_staff_or_terminal)):
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
