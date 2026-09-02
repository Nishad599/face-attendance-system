# attendance_manager.py
"""
Enhanced Attendance Manager with Configurable Time-based Slots and Live Counting
Handles attendance marking within specific time slots and provides real-time student count
Now reads slot timings from session_configs database table for admin configurability
Now supports both SQLite and PostgreSQL via db.py.
Fixed with proper IST timezone handling
"""

from datetime import datetime, time
from typing import Dict, List, Tuple, Optional
import logging
import pytz
from db import get_connection, is_postgres

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# IST timezone constant
IST = pytz.timezone('Asia/Kolkata')

# Per-batch slot timings, cached across manager instances.
#
# The marking path builds a fresh AttendanceSlotManager per request, so an
# uncached lookup would re-query session_configs for every detected face. A
# short TTL keeps that cheap while still letting an admin's edit take effect
# on its own - the global `attendance_manager` in the app is built once at
# import and would otherwise serve stale timings until the next restart.
_SLOT_CACHE = {}
_SLOT_CACHE_TTL_SECONDS = 20


def invalidate_slot_cache(course_id=None):
    """Drop cached timings so the next read hits the database.

    Called when slot configuration is saved, so a teacher sees their change
    immediately rather than up to TTL seconds later.
    """
    if course_id is None:
        _SLOT_CACHE.clear()
    else:
        _SLOT_CACHE.pop(int(course_id), None)

def get_ist_time():
    """Get current time in IST"""
    return datetime.now(IST)

def get_ist_date_str():
    """Get current IST date as string"""
    return get_ist_time().strftime('%Y-%m-%d')

def get_ist_time_str():
    """Get current IST time as string"""
    return get_ist_time().strftime('%H:%M:%S')

def get_ist_timestamp_str():
    """Get current IST timestamp as string"""
    return get_ist_time().strftime('%Y-%m-%d %H:%M:%S')

class AttendanceSlotManager:
    """Manages configurable time-based attendance slots and live student counting"""
    
    def __init__(self, db_path: str = 'attendance.db'):
        self.db_path = db_path
        self.conn = get_connection(db_path)
        self.init_slot_tables()
        
        # Load attendance slots from database instead of hardcoded values
        self.attendance_slots = self.load_session_configs()
        
        # Ensure we have default configs if none exist
        self.ensure_default_configs()
        
        # Log loaded configuration
        slot_info = ", ".join([
            f"{slot['name']} ({slot['start_time'].strftime('%H:%M')}-{slot['end_time'].strftime('%H:%M')})"
            for slot in self.attendance_slots.values()
        ])
        logger.info(f"AttendanceSlotManager initialized with configurable slots: {slot_info}")
            
    def init_slot_tables(self):
        """Initialize database tables for slot-based attendance"""
        cursor = self.conn.cursor()
        
        # Create slot_attendance table to track attendance by slots
        if is_postgres():
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS slot_attendance (
                    id SERIAL PRIMARY KEY,
                    student_id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    slot_id TEXT NOT NULL,
                    time_marked TEXT NOT NULL,
                    detection_confidence REAL,
                    is_manual BOOLEAN DEFAULT FALSE,
                    manual_reason TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (student_id) REFERENCES students (id),
                    UNIQUE(student_id, date, slot_id)
                )
            ''')
            # Create daily_attendance_summary for quick counts
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_attendance_summary (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL UNIQUE,
                    total_students INTEGER DEFAULT 0,
                    present_morning INTEGER DEFAULT 0,
                    present_afternoon INTEGER DEFAULT 0,
                    total_present INTEGER DEFAULT 0,
                    last_updated TEXT NOT NULL
                )
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS slot_attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    slot_id TEXT NOT NULL,
                    time_marked TEXT NOT NULL,
                    detection_confidence REAL,
                    is_manual BOOLEAN DEFAULT FALSE,
                    manual_reason TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (student_id) REFERENCES students (id),
                    UNIQUE(student_id, date, slot_id)
                )
            ''')
            # Create daily_attendance_summary for quick counts
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_attendance_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL UNIQUE,
                    total_students INTEGER DEFAULT 0,
                    present_morning INTEGER DEFAULT 0,
                    present_afternoon INTEGER DEFAULT 0,
                    total_present INTEGER DEFAULT 0,
                    last_updated TEXT NOT NULL
                )
            ''')

        # session_configs must exist before load_session_configs() reads it.
        # The slot manager is constructed at import time BEFORE AttendanceSystem
        # creates this table, so on a fresh DB (SQLite or Postgres) we must
        # ensure it here too. (No FK to courses — that table may not exist yet.)
        id_decl = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS session_configs (
                id {id_decl},
                course_id INTEGER,
                session_type TEXT NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                is_active BOOLEAN DEFAULT TRUE
            )
        ''')

        self.conn.commit()
        logger.info("Slot attendance tables initialized")
    
    def load_session_configs(self, course_id: Optional[int] = None):
        """Load slot configuration from the session_configs table.

        session_configs is per-batch. Loading every batch at once collapses
        them, because the dict is keyed by session_type and each batch has its
        own 'morning_1', 'afternoon_2' and so on - so the last row read silently
        overwrote every other batch's timings. Editing one batch's slot then
        appeared to do nothing, and its students could not mark attendance
        outside whichever batch happened to win.

        Pass a course_id to get that batch's real timings. course_id=None keeps
        the old all-batches view, which is only meaningful as a fallback for a
        batch that has no rows of its own.
        """
        cursor = self.conn.cursor()
        if course_id is None:
            cursor.execute('''
                SELECT session_type, start_time, end_time
                FROM session_configs
                WHERE is_active = 1
                ORDER BY start_time
            ''')
        else:
            cursor.execute('''
                SELECT session_type, start_time, end_time
                FROM session_configs
                WHERE is_active = 1 AND course_id = ?
                ORDER BY start_time
            ''', (int(course_id),))

        slots = {}
        for row in cursor.fetchall():
            session_type, start_time_str, end_time_str = row
            
            try:
                start_time = datetime.strptime(start_time_str, '%H:%M:%S').time()
                end_time = datetime.strptime(end_time_str, '%H:%M:%S').time()
            except ValueError:
                # Fallback for different time formats
                start_time = datetime.strptime(start_time_str, '%H:%M').time()
                end_time = datetime.strptime(end_time_str, '%H:%M').time()
            
            # Map session_type to slot_id and create display name
            slot_id = session_type.lower()
            name = f"{session_type.title()} Session"
            
            slots[slot_id] = {
                'name': name,
                'start_time': start_time,
                'end_time': end_time,
                'slot_id': slot_id
            }
        
        return slots
    
    def ensure_default_configs(self):
        """Ensure default session configurations exist"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM session_configs WHERE is_active = 1')
        
        if cursor.fetchone()[0] == 0:
            logger.info("No active session configs found, creating defaults")
            
            # Get or create default course
            cursor.execute('SELECT id FROM courses WHERE is_active = 1 LIMIT 1')
            course_row = cursor.fetchone()
            
            if course_row:
                course_id = course_row[0]
            else:
                # Create default course
                cursor.execute('''
                    INSERT INTO courses (name, start_date, end_date, description)
                    VALUES (?, ?, ?, ?)
                ''', (
                    "Default Course",
                    "2025-01-01",
                    "2025-12-31", 
                    "Default course for attendance system"
                ))
                course_id = cursor.lastrowid
            
            # Create default session configs for 4 slots
            if is_postgres():
                # On PostgreSQL, we should do separate inserts or standard multi-value insert
                cursor.execute('''
                    INSERT INTO session_configs (course_id, session_type, start_time, end_time, is_active)
                    VALUES (?, 'morning_1', '08:30:00', '09:30:00', 1)
                ''', (course_id,))
                cursor.execute('''
                    INSERT INTO session_configs (course_id, session_type, start_time, end_time, is_active)
                    VALUES (?, 'morning_2', '11:00:00', '11:15:00', 1)
                ''', (course_id,))
                cursor.execute('''
                    INSERT INTO session_configs (course_id, session_type, start_time, end_time, is_active)
                    VALUES (?, 'afternoon_1', '13:45:00', '14:00:00', 1)
                ''', (course_id,))
                cursor.execute('''
                    INSERT INTO session_configs (course_id, session_type, start_time, end_time, is_active)
                    VALUES (?, 'afternoon_2', '16:15:00', '16:45:00', 1)
                ''', (course_id,))
            else:
                cursor.execute('''
                    INSERT INTO session_configs (course_id, session_type, start_time, end_time, is_active)
                    VALUES 
                    (?, 'morning_1', '08:30:00', '09:30:00', 1),
                    (?, 'morning_2', '11:00:00', '11:15:00', 1),
                    (?, 'afternoon_1', '13:45:00', '14:00:00', 1),
                    (?, 'afternoon_2', '16:15:00', '16:45:00', 1)
                ''', (course_id, course_id, course_id, course_id))
            
            self.conn.commit()
            
            # Reload slots after creating defaults
            self.attendance_slots = self.load_session_configs()
    
    def reload_config(self):
        """Reload slot configuration from database"""
        invalidate_slot_cache()
        self.attendance_slots = self.load_session_configs()
        slot_info = ", ".join([
            f"{slot['name']} ({slot['start_time'].strftime('%H:%M')}-{slot['end_time'].strftime('%H:%M')})"
            for slot in self.attendance_slots.values()
        ])
        logger.info(f"Slot configuration reloaded: {slot_info}")
    
    def slots_for_course(self, course_id: Optional[int] = None) -> Dict:
        """Slot timings that apply to one batch.

        Falls back to the all-batches view when the batch has no rows of its
        own, so a course created before slots were configured still works.
        """
        if course_id is None:
            return self.attendance_slots

        key = int(course_id)
        now = datetime.now().timestamp()
        hit = _SLOT_CACHE.get(key)
        if hit and (now - hit[0]) < _SLOT_CACHE_TTL_SECONDS:
            return hit[1]

        slots = self.load_session_configs(key)
        if not slots:
            slots = self.attendance_slots
        _SLOT_CACHE[key] = (now, slots)
        return slots

    def get_current_slot(self, check_time: Optional[datetime] = None,
                         course_id: Optional[int] = None) -> Optional[Dict]:
        """
        Check if current time falls within any attendance slot

        Args:
            check_time: Optional datetime to check, defaults to IST now
            course_id: Batch whose timings apply. Slots are configured per
                       batch, so omitting this checks against a merged view
                       that may belong to a different batch entirely.

        Returns:
            Dict with slot info if within slot, None otherwise
        """
        if check_time is None:
            check_time = get_ist_time()
        
        current_time = check_time.time()
        
        for slot_key, slot_info in self.slots_for_course(course_id).items():
            if slot_info['start_time'] <= current_time <= slot_info['end_time']:
                return {
                    'slot_key': slot_key,
                    'slot_info': slot_info,
                    'is_active': True,
                    'time_remaining': self._calculate_time_remaining(current_time, slot_info['end_time'])
                }
        
        return None
    
    def _calculate_time_remaining(self, current_time: time, end_time: time) -> int:
        """Calculate minutes remaining in current slot"""
        current_minutes = current_time.hour * 60 + current_time.minute
        end_minutes = end_time.hour * 60 + end_time.minute
        return max(0, end_minutes - current_minutes)
    
    def get_next_slot(self, check_time: Optional[datetime] = None,
                      course_id: Optional[int] = None) -> Optional[Dict]:
        """Get information about the next upcoming slot for a batch."""
        if check_time is None:
            check_time = get_ist_time()
            
        current_time = check_time.time()
        next_slot = None
        min_wait_time = float('inf')
        
        for slot_key, slot_info in self.slots_for_course(course_id).items():
            start_time = slot_info['start_time']
            
            # Calculate minutes until this slot starts
            current_minutes = current_time.hour * 60 + current_time.minute
            start_minutes = start_time.hour * 60 + start_time.minute
            
            if start_minutes > current_minutes:  # Slot is later today
                wait_minutes = start_minutes - current_minutes
                if wait_minutes < min_wait_time:
                    min_wait_time = wait_minutes
                    next_slot = {
                        'slot_key': slot_key,
                        'slot_info': slot_info,
                        'wait_minutes': wait_minutes
                    }
        
        return next_slot
    
    def update_session_timing(self, session_type: str, start_time: str, end_time: str, course_id=None):
        """Update session timing in session_configs table (optionally scoped to a batch)."""
        try:
            cursor = self.conn.cursor()

            # Validate time format and logic
            try:
                start_time_obj = datetime.strptime(start_time, '%H:%M').time()
                end_time_obj = datetime.strptime(end_time, '%H:%M').time()
            except ValueError:
                return False, "Invalid time format. Use HH:MM format."

            if start_time_obj >= end_time_obj:
                return False, "Start time must be before end time"

            # Update session_configs (scoped to a course when given)
            if course_id is not None:
                cursor.execute('''
                    UPDATE session_configs
                    SET start_time = ?, end_time = ?
                    WHERE session_type = ? AND course_id = ?
                ''', (start_time + ':00', end_time + ':00', session_type, course_id))
            else:
                cursor.execute('''
                    UPDATE session_configs
                    SET start_time = ?, end_time = ?
                    WHERE session_type = ? AND is_active = 1
                ''', (start_time + ':00', end_time + ':00', session_type))

            if cursor.rowcount > 0:
                self.conn.commit()
                # Drop the cached timings so the change is live at once rather
                # than after the TTL - and clear every batch when the update
                # was unscoped, because then it touched all of them.
                invalidate_slot_cache(course_id)
                self.reload_config()
                return True, f"Session '{session_type}' updated to {start_time}-{end_time}"
            else:
                return False, "Session not found"

        except Exception as e:
            logger.error(f"Error updating session timing: {str(e)}")
            return False, f"Error updating session: {str(e)}"

    def get_session_configs(self, course_id=None):
        """Get session configuration, optionally scoped to a batch."""
        cursor = self.conn.cursor()
        if course_id is not None:
            cursor.execute('''
                SELECT id, course_id, session_type, start_time, end_time, is_active
                FROM session_configs WHERE course_id = ?
                ORDER BY start_time
            ''', (course_id,))
        else:
            cursor.execute('''
                SELECT id, course_id, session_type, start_time, end_time, is_active
                FROM session_configs
                ORDER BY start_time
            ''')
        
        configs = []
        for row in cursor.fetchall():
            configs.append({
                'id': row[0],
                'course_id': row[1],
                'session_type': row[2],
                'start_time': row[3],
                'end_time': row[4],
                'is_active': bool(row[5])
            })
        
        return configs
    
    def mark_attendance_with_slot(self, student_id: int, detection_confidence: float = 0.0, 
                                 force_slot: Optional[str] = None) -> Dict:
        """
        Mark attendance only if within valid time slot
        
        Args:
            student_id: ID of the student
            detection_confidence: Face recognition confidence score
            force_slot: Force specific slot (for manual attendance)
            
        Returns:
            Dict with success status and message
        """
        try:
            # Use IST timezone consistently
            current_time = get_ist_time()
            today_str = get_ist_date_str()
            current_timestamp = get_ist_timestamp_str()
            current_time_only = get_ist_time_str()
            
            # Slot timings are configured per batch, so they have to be read
            # against THIS student's batch. Using the manager-wide table meant a
            # student was judged against whichever batch's timings happened to
            # be loaded, and could be told they were outside their slot when
            # their own batch was mid-session.
            cursor = self.conn.cursor()
            row = cursor.execute(
                "SELECT course_id FROM students WHERE id = ?", (student_id,)
            ).fetchone()
            student_course_id = row[0] if row else None
            slots = self.slots_for_course(student_course_id)

            # Check if we're in a valid slot (unless forced)
            if force_slot:
                if force_slot not in slots:
                    return {
                        'success': False,
                        'message': f'Invalid slot: {force_slot}',
                        'slot_active': False
                    }
                current_slot = {
                    'slot_key': force_slot,
                    'slot_info': slots[force_slot],
                    'is_active': True
                }
            else:
                current_slot = self.get_current_slot(current_time, student_course_id)
                
            if not current_slot:
                next_slot = self.get_next_slot(current_time, student_course_id)
                next_info = ""
                if next_slot:
                    hours = next_slot['wait_minutes'] // 60
                    minutes = next_slot['wait_minutes'] % 60
                    next_info = f" Next slot: {next_slot['slot_info']['name']} in {hours}h {minutes}m"
                
                return {
                    'success': False,
                    'message': f'Attendance can only be marked during slot hours.{next_info}',
                    'slot_active': False,
                    'face_detected': True,
                    'outside_slot': True,
                    'next_slot': next_slot
                }
            
            slot_id = current_slot['slot_key']
            slot_name = current_slot['slot_info']['name']

            # Get student info
            cursor = self.conn.cursor()

            # No attendance on a Sunday or a holiday. This is the terminal /
            # kiosk path, so without this the camera would happily mark a whole
            # batch present on a day that no report will ever count.
            try:
                import working_days as _wd
                _row = cursor.execute(
                    "SELECT course_id FROM students WHERE id = ?", (student_id,)
                ).fetchone()
                _ok, _why = _wd.check(self.conn, _row[0] if _row else None,
                                      datetime.now().strftime('%Y-%m-%d'))
                if not _ok:
                    return {
                        'success': False,
                        'message': _why,
                        'slot_active': False,
                        'face_detected': True,
                        'non_working_day': True,
                    }
            except ImportError:
                pass
            cursor.execute('SELECT name, student_id FROM students WHERE id = ? AND status = "active"', 
                          (student_id,))
            student_info = cursor.fetchone()
            
            if not student_info:
                return {
                    'success': False,
                    'message': 'Student not found or inactive',
                    'slot_active': True
                }
            
            student_name, student_id_str = student_info
            
            # Check if already marked for this slot today
            cursor.execute('''
                SELECT id FROM slot_attendance 
                WHERE student_id = ? AND date = ? AND slot_id = ?
            ''', (student_id, today_str, slot_id))
            
            if cursor.fetchone():
                return {
                    'success': False,
                    'message': f'{student_name} already marked present for {slot_name}',
                    'slot_active': True,
                    'already_marked': True,
                    'student_name': student_name,
                    'slot_name': slot_name
                }
            
            # Mark attendance in slot_attendance table with explicit IST timestamps
            cursor.execute('''
                INSERT INTO slot_attendance 
                (student_id, date, slot_id, time_marked, detection_confidence, is_manual, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (student_id, today_str, slot_id, current_timestamp, detection_confidence, 
                  force_slot is not None, current_timestamp))
            
            # Also mark in main attendance table — this is the table every
            # report and analytic reads, so it must carry the slot and batch.
            # These were previously left NULL, which made half-day counting and
            # subject attribution impossible for face-marked attendance.
            course_row = cursor.execute(
                "SELECT course_id FROM students WHERE id = ?", (student_id,)
            ).fetchone()
            course_id = course_row[0] if course_row else None

            subject_id = None
            try:
                import timetable as _tt
                subject_id = _tt.subject_for_slot(self.conn, course_id, today_str, slot_id)
            except Exception:
                pass    # no timetable configured; attendance still marks

            if is_postgres():
                # On PG, UNIQUE index conflict checking is done if constraint exists, but since no UNIQUE constraint
                # exists on attendance(student_id, date), a standard INSERT behaves the same as INSERT OR IGNORE.
                cursor.execute('''
                    INSERT INTO attendance
                    (student_id, date, time_in, is_manual, manual_reason,
                     session_type, course_id, subject_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (student_id, today_str, current_time_only,
                      force_slot is not None, f'{slot_name} slot attendance',
                      slot_id, course_id, subject_id))
            else:
                cursor.execute('''
                    INSERT OR IGNORE INTO attendance
                    (student_id, date, time_in, is_manual, manual_reason,
                     session_type, course_id, subject_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (student_id, today_str, current_time_only,
                      force_slot is not None, f'{slot_name} slot attendance',
                      slot_id, course_id, subject_id))
            
            self.conn.commit()
            
            # Update daily summary
            self.update_daily_summary(today_str)
            
            logger.info(f"Attendance marked: {student_name} ({student_id_str}) - {slot_name} at {current_timestamp}")
            
            return {
                'success': True,
                'message': f'Attendance marked for {student_name} - {slot_name}',
                'slot_active': True,
                'student_name': student_name,
                'student_id': student_id_str,
                'slot_name': slot_name,
                'slot_id': slot_id,
                'time_marked': current_timestamp,
                'confidence': detection_confidence
            }
            
        except Exception as e:
            logger.error(f"Error marking attendance: {str(e)}")
            return {
                'success': False,
                'message': f'Error marking attendance: {str(e)}',
                'slot_active': False
            }
    
    def get_live_student_count(self, date_str: Optional[str] = None,
                               course_id=None) -> Dict:
        """
        Get live count of students present today.

        Args:
            date_str:  Date to check, defaults to IST today
            course_id: Restrict to one batch (an int) or several (a list of
                       ints, for a combined terminal covering more than one
                       batch). None = all batches, which is what the staff
                       dashboard wants.

        Returns:
            Dict with detailed attendance counts
        """
        if date_str is None:
            date_str = get_ist_date_str()

        try:
            cursor = self.conn.cursor()

            # Batch scoping. A terminal is bound to the batches it signed in
            # for, so its totals must not include students from any other.
            if isinstance(course_id, (list, tuple, set)):
                ids = [int(c) for c in course_id]
            elif course_id is None:
                ids = None
            else:
                ids = [int(course_id)]

            if ids is None:
                stu_where, stu_args = "", ()
                att_where, att_args = "", ()
            elif not ids:
                # An empty scope means "no batches", not "every batch".
                stu_where, stu_args = " AND 1 = 0", ()
                att_where, att_args = " AND 1 = 0", ()
            else:
                marks = ",".join("?" for _ in ids)
                stu_where, stu_args = f" AND course_id IN ({marks})", tuple(ids)
                att_where, att_args = f" AND course_id IN ({marks})", tuple(ids)

            # Total active students in scope
            cursor.execute(
                "SELECT COUNT(*) FROM students WHERE status = 'active'" + stu_where,
                stu_args)
            total_students = cursor.fetchone()[0]

            # Presence comes from `attendance`, the table the app actually
            # writes to. `slot_attendance` is legacy and stays empty, which is
            # why this used to report 0 present / 0%.
            cursor.execute(
                "SELECT COUNT(DISTINCT student_id) FROM attendance "
                "WHERE date = ? AND session_type LIKE 'morning%'" + att_where,
                (date_str,) + att_args)
            morning_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(DISTINCT student_id) FROM attendance "
                "WHERE date = ? AND session_type LIKE 'afternoon%'" + att_where,
                (date_str,) + att_args)
            afternoon_count = cursor.fetchone()[0]

            # Unique students present (attended at least one slot)
            cursor.execute(
                "SELECT COUNT(DISTINCT student_id) FROM attendance "
                "WHERE date = ?" + att_where,
                (date_str,) + att_args)
            total_present = cursor.fetchone()[0]

            absent_count = max(total_students - total_present, 0)

            # Get current slot info. With exactly one batch in scope - a batch
            # terminal, or a teacher with one assigned batch - show that batch's
            # own timings. A combined terminal spans batches whose slots may
            # differ, so it falls back to the merged view.
            slot_course = ids[0] if ids and len(ids) == 1 else None
            current_slot = self.get_current_slot(course_id=slot_course)
            next_slot = self.get_next_slot(course_id=slot_course)

            attendance_percentage = (total_present / total_students * 100) if total_students > 0 else 0

            return {
                'success': True,
                'date': date_str,
                # Normalised scope: a list of course ids, or None for
                # "every batch". Echoed so a caller can confirm what it got.
                'course_ids': ids,
                'total_students': total_students,
                'total_present': total_present,
                'total_absent': absent_count,
                'morning_present': morning_count,
                'afternoon_present': afternoon_count,
                'attendance_percentage': round(attendance_percentage, 1),
                'current_slot': current_slot,
                'next_slot': next_slot,
                'last_updated': get_ist_time_str()
            }

        except Exception as e:
            logger.error(f"Error getting live count: {str(e)}")
            return {
                'success': False,
                'message': f'Error getting student count: {str(e)}',
                'total_students': 0,
                'total_present': 0,
                'total_absent': 0
            }

    def update_daily_summary(self, date_str: str):
        """Update the daily attendance summary table"""
        try:
            cursor = self.conn.cursor()
            
            # Get counts
            cursor.execute('SELECT COUNT(*) FROM students WHERE status = "active"')
            total_students = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT COUNT(DISTINCT student_id) FROM slot_attendance 
                WHERE date = ? AND slot_id LIKE 'morning%'
            ''', (date_str,))
            morning_count = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT COUNT(DISTINCT student_id) FROM slot_attendance 
                WHERE date = ? AND slot_id LIKE 'afternoon%' 
            ''', (date_str,))
            afternoon_count = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT COUNT(DISTINCT student_id) FROM slot_attendance 
                WHERE date = ?
            ''', (date_str,))
            total_present = cursor.fetchone()[0]
            
            # Update summary with IST timestamp
            if is_postgres():
                cursor.execute('''
                    INSERT INTO daily_attendance_summary
                    (date, total_students, present_morning, present_afternoon, total_present, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (date) DO UPDATE SET
                        total_students = EXCLUDED.total_students,
                        present_morning = EXCLUDED.present_morning,
                        present_afternoon = EXCLUDED.present_afternoon,
                        total_present = EXCLUDED.total_present,
                        last_updated = EXCLUDED.last_updated
                ''', (date_str, total_students, morning_count, afternoon_count, total_present, get_ist_timestamp_str()))
            else:
                cursor.execute('''
                    INSERT OR REPLACE INTO daily_attendance_summary
                    (date, total_students, present_morning, present_afternoon, total_present, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (date_str, total_students, morning_count, afternoon_count, total_present, get_ist_timestamp_str()))
            
            self.conn.commit()
            
        except Exception as e:
            logger.error(f"Error updating daily summary: {str(e)}")
    
    def get_slot_attendance_details(self, date_str: Optional[str] = None) -> Dict:
        """Get detailed attendance information by slot"""
        if date_str is None:
            date_str = get_ist_date_str()
        
        try:
            cursor = self.conn.cursor()
            
            # Get students by slot
            cursor.execute('''
                SELECT s.name, s.student_id, sa.slot_id, sa.time_marked, sa.detection_confidence
                FROM slot_attendance sa
                JOIN students s ON sa.student_id = s.id
                WHERE sa.date = ?
                ORDER BY sa.slot_id, sa.time_marked
            ''', (date_str,))
            
            attendance_records = cursor.fetchall()
            
            # Organize by slot dynamically
            from collections import defaultdict
            slots_data = defaultdict(list)
            
            for record in attendance_records:
                name, student_id, slot_id, time_marked, confidence = record
                slots_data[slot_id].append({
                    'name': name,
                    'student_id': student_id,
                    'time_marked': time_marked,
                    'confidence': confidence
                })
            
            morning_count = sum(len(lst) for slot, lst in slots_data.items() if slot.startswith('morning'))
            afternoon_count = sum(len(lst) for slot, lst in slots_data.items() if slot.startswith('afternoon'))
            
            return {
                'success': True,
                'date': date_str,
                'slots': dict(slots_data),
                'morning_count': morning_count,
                'afternoon_count': afternoon_count
            }
            
        except Exception as e:
            logger.error(f"Error getting slot details: {str(e)}")
            return {
                'success': False,
                'message': f'Error getting slot details: {str(e)}'
            }
    
    def get_student_slot_history(self, student_id: int, days: int = 30) -> Dict:
        """Get a student's attendance history by slots"""
        try:
            cursor = self.conn.cursor()
            
            # Get student info
            cursor.execute('SELECT name, student_id FROM students WHERE id = ?', (student_id,))
            student_info = cursor.fetchone()
            
            if not student_info:
                return {'success': False, 'message': 'Student not found'}
            
            # Get attendance records
            cursor.execute('''
                SELECT date, slot_id, time_marked, detection_confidence
                FROM slot_attendance
                WHERE student_id = ?
                ORDER BY date DESC, time_marked DESC
                LIMIT ?
            ''', (student_id, days * 2))  # *2 because max 2 slots per day
            
            records = cursor.fetchall()
            
            # Group by date
            history = {}
            for record in records:
                date, slot_id, time_marked, confidence = record
                if date not in history:
                    history[date] = {}
                history[date][slot_id] = {
                    'time_marked': time_marked,
                    'confidence': confidence
                }
            
            return {
                'success': True,
                'student_name': student_info[0],
                'student_id': student_info[1],
                'history': history
            }
            
        except Exception as e:
            logger.error(f"Error getting student history: {str(e)}")
            return {
                'success': False,
                'message': f'Error getting student history: {str(e)}'
            }


def create_slot_manager_instance(db_path: str = 'attendance.db') -> AttendanceSlotManager:
    """Factory function to create AttendanceSlotManager instance"""
    return AttendanceSlotManager(db_path)


# Utility functions for easy integration
def is_attendance_slot_active(check_time: Optional[datetime] = None) -> bool:
    """Quick check if any attendance slot is currently active"""
    manager = create_slot_manager_instance()
    current_slot = manager.get_current_slot(check_time)
    return current_slot is not None


def get_current_attendance_count() -> int:
    """Quick function to get current attendance count"""
    manager = create_slot_manager_instance()
    count_data = manager.get_live_student_count()
    return count_data.get('total_present', 0)


def mark_student_attendance(student_id: int, confidence: float = 0.0) -> Dict:
    """Quick function to mark attendance with slot validation"""
    manager = create_slot_manager_instance()
    return manager.mark_attendance_with_slot(student_id, confidence)


if __name__ == "__main__":
    # Test the AttendanceSlotManager
    manager = AttendanceSlotManager()
    
    print("=== Configurable Attendance Slot Manager Test ===")
    
    # Test current slot
    current_slot = manager.get_current_slot()
    if current_slot:
        print(f"Current slot: {current_slot['slot_info']['name']}")
        print(f"Time remaining: {current_slot['time_remaining']} minutes")
    else:
        print("No active slot")
        next_slot = manager.get_next_slot()
        if next_slot:
            print(f"Next slot: {next_slot['slot_info']['name']} in {next_slot['wait_minutes']} minutes")
    
    # Test live count
    count_data = manager.get_live_student_count()
    print(f"\nLive attendance count: {count_data}")
    
    # Test configuration
    configs = manager.get_session_configs()
    print(f"\nCurrent configurations: {configs}")
    
    print(f"\nCurrent IST time: {get_ist_timestamp_str()}")
    print("\n=== Test completed ===")