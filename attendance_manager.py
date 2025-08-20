# attendance_manager.py
"""
Enhanced Attendance Manager with Time-based Slots and Live Counting
Handles attendance marking within specific time slots and provides real-time student count
"""

import sqlite3
from datetime import datetime, time
from typing import Dict, List, Tuple, Optional
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AttendanceSlotManager:
    """Manages time-based attendance slots and live student counting"""
    
    def __init__(self, db_path: str = 'attendance.db'):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.init_slot_tables()
        
        # Define attendance slots
        self.attendance_slots = {
            'morning': {
                'name': 'Morning Session',
                'start_time': time(8, 45),    # FIXED: 8:45 AM
                'end_time': time(9, 30),      # 9:30 AM (unchanged)
                'slot_id': 'morning'
            },
            'afternoon': {
                'name': 'Afternoon Session', 
                'start_time': time(13, 45),   # 1:45 PM (unchanged)
                'end_time': time(14, 30),     # FIXED: 2:30 PM
                'slot_id': 'afternoon'
            }
        }

        logger.info("AttendanceSlotManager initialized with slots: Morning (8:45-9:30 AM), Afternoon (1:45-2:30 PM)")
            
    def init_slot_tables(self):
        """Initialize database tables for slot-based attendance"""
        cursor = self.conn.cursor()
        
        # Create slot_attendance table to track attendance by slots
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS slot_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date DATE NOT NULL,
                slot_id TEXT NOT NULL,
                time_marked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                detection_confidence REAL,
                is_manual BOOLEAN DEFAULT FALSE,
                manual_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
        logger.info("Slot attendance tables initialized")
    
    def get_current_slot(self, check_time: Optional[datetime] = None) -> Optional[Dict]:
        """
        Check if current time falls within any attendance slot
        
        Args:
            check_time: Optional datetime to check, defaults to now
            
        Returns:
            Dict with slot info if within slot, None otherwise
        """
        if check_time is None:
            check_time = datetime.now()
        
        current_time = check_time.time()
        
        for slot_key, slot_info in self.attendance_slots.items():
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
    
    def get_next_slot(self, check_time: Optional[datetime] = None) -> Optional[Dict]:
        """Get information about the next upcoming slot"""
        if check_time is None:
            check_time = datetime.now()
            
        current_time = check_time.time()
        next_slot = None
        min_wait_time = float('inf')
        
        for slot_key, slot_info in self.attendance_slots.items():
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
            current_time = datetime.now()
            today_str = current_time.date().strftime('%Y-%m-%d')
            
            # Check if we're in a valid slot (unless forced)
            if force_slot:
                if force_slot not in self.attendance_slots:
                    return {
                        'success': False,
                        'message': f'Invalid slot: {force_slot}',
                        'slot_active': False
                    }
                current_slot = {
                    'slot_key': force_slot,
                    'slot_info': self.attendance_slots[force_slot],
                    'is_active': True
                }
            else:
                current_slot = self.get_current_slot(current_time)
                
            if not current_slot:
                next_slot = self.get_next_slot(current_time)
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
            
            # Mark attendance in slot_attendance table
            cursor.execute('''
                INSERT INTO slot_attendance 
                (student_id, date, slot_id, detection_confidence, is_manual)
                VALUES (?, ?, ?, ?, ?)
            ''', (student_id, today_str, slot_id, detection_confidence, force_slot is not None))
            
            # Also mark in main attendance table for compatibility
            cursor.execute('''
                INSERT OR IGNORE INTO attendance 
                (student_id, date, time_in, is_manual, manual_reason)
                VALUES (?, ?, ?, ?, ?)
            ''', (student_id, today_str, current_time.time().strftime('%H:%M:%S'), 
                  force_slot is not None, f'{slot_name} slot attendance'))
            
            self.conn.commit()
            
            # Update daily summary
            self.update_daily_summary(today_str)
            
            logger.info(f"Attendance marked: {student_name} ({student_id_str}) - {slot_name}")
            
            return {
                'success': True,
                'message': f'Attendance marked for {student_name} - {slot_name}',
                'slot_active': True,
                'student_name': student_name,
                'student_id': student_id_str,
                'slot_name': slot_name,
                'slot_id': slot_id,
                'time_marked': current_time.strftime('%H:%M:%S'),
                'confidence': detection_confidence
            }
            
        except Exception as e:
            logger.error(f"Error marking attendance: {str(e)}")
            return {
                'success': False,
                'message': f'Error marking attendance: {str(e)}',
                'slot_active': False
            }
    
    def get_live_student_count(self, date_str: Optional[str] = None) -> Dict:
        """
        Get live count of students present today
        
        Args:
            date_str: Date to check, defaults to today
            
        Returns:
            Dict with detailed attendance counts
        """
        if date_str is None:
            date_str = datetime.now().date().strftime('%Y-%m-%d')
        
        try:
            cursor = self.conn.cursor()
            
            # Get total active students
            cursor.execute('SELECT COUNT(*) FROM students WHERE status = "active"')
            total_students = cursor.fetchone()[0]
            
            # Get counts by slot
            cursor.execute('''
                SELECT slot_id, COUNT(DISTINCT student_id) as count
                FROM slot_attendance 
                WHERE date = ?
                GROUP BY slot_id
            ''', (date_str,))
            
            slot_counts = dict(cursor.fetchall())
            morning_count = slot_counts.get('morning', 0)
            afternoon_count = slot_counts.get('afternoon', 0)
            
            # Get unique students present (attended at least one slot)
            cursor.execute('''
                SELECT COUNT(DISTINCT student_id)
                FROM slot_attendance 
                WHERE date = ?
            ''', (date_str,))
            
            total_present = cursor.fetchone()[0]
            absent_count = total_students - total_present
            
            # Get current slot info
            current_slot = self.get_current_slot()
            next_slot = self.get_next_slot()
            
            # Calculate attendance percentage
            attendance_percentage = (total_present / total_students * 100) if total_students > 0 else 0
            
            return {
                'success': True,
                'date': date_str,
                'total_students': total_students,
                'total_present': total_present,
                'total_absent': absent_count,
                'morning_present': morning_count,
                'afternoon_present': afternoon_count,
                'attendance_percentage': round(attendance_percentage, 1),
                'current_slot': current_slot,
                'next_slot': next_slot,
                'last_updated': datetime.now().strftime('%H:%M:%S')
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
                WHERE date = ? AND slot_id = "morning"
            ''', (date_str,))
            morning_count = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT COUNT(DISTINCT student_id) FROM slot_attendance 
                WHERE date = ? AND slot_id = "afternoon" 
            ''', (date_str,))
            afternoon_count = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT COUNT(DISTINCT student_id) FROM slot_attendance 
                WHERE date = ?
            ''', (date_str,))
            total_present = cursor.fetchone()[0]
            
            # Update summary
            cursor.execute('''
                INSERT OR REPLACE INTO daily_attendance_summary
                (date, total_students, present_morning, present_afternoon, total_present, last_updated)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (date_str, total_students, morning_count, afternoon_count, total_present))
            
            self.conn.commit()
            
        except Exception as e:
            logger.error(f"Error updating daily summary: {str(e)}")
    
    def get_slot_attendance_details(self, date_str: Optional[str] = None) -> Dict:
        """Get detailed attendance information by slot"""
        if date_str is None:
            date_str = datetime.now().date().strftime('%Y-%m-%d')
        
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
            
            # Organize by slot
            slots_data = {
                'morning': [],
                'afternoon': []
            }
            
            for record in attendance_records:
                name, student_id, slot_id, time_marked, confidence = record
                slots_data[slot_id].append({
                    'name': name,
                    'student_id': student_id,
                    'time_marked': time_marked,
                    'confidence': confidence
                })
            
            return {
                'success': True,
                'date': date_str,
                'slots': slots_data,
                'morning_count': len(slots_data['morning']),
                'afternoon_count': len(slots_data['afternoon'])
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
    
    print("=== Attendance Slot Manager Test ===")
    
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
    
    print("\n=== Test completed ===")
