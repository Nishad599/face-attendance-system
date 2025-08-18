# phase1_integration.py
# Phase 1 Integration - Enhanced Attendance System
# Multi-Session Support with Saturday-First Calendar

import sqlite3
import json
from datetime import datetime, timedelta, date, time
from typing import Dict, List, Optional, Tuple
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def enhance_existing_attendance_system(attendance_system):
    """
    Enhance the existing AttendanceSystem with Phase 1 features
    Add these methods to your AttendanceSystem instance
    """
    
    def update_working_days_config(self):
        """Update working days configuration for Saturday-Friday schedule"""
        cursor = self.conn.cursor()
        
        # Create working_days_config table if not exists
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS working_days_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_of_week INTEGER NOT NULL UNIQUE, -- 0=Sunday, 1=Monday, ..., 6=Saturday
                is_working BOOLEAN DEFAULT 1,
                day_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Configure Saturday-Friday as working days (Sunday off)
        working_days_data = [
            (0, False, "Sunday"),    # Sunday - Holiday
            (1, True, "Monday"),     # Monday - Working
            (2, True, "Tuesday"),    # Tuesday - Working
            (3, True, "Wednesday"),  # Wednesday - Working
            (4, True, "Thursday"),   # Thursday - Working
            (5, True, "Friday"),     # Friday - Working
            (6, True, "Saturday")    # Saturday - Working
        ]
        
        for day_of_week, is_working, day_name in working_days_data:
            cursor.execute('''
                INSERT OR REPLACE INTO working_days_config 
                (day_of_week, is_working, day_name) 
                VALUES (?, ?, ?)
            ''', (day_of_week, is_working, day_name))
        
        self.conn.commit()
        print("✅ Working days configuration updated: Saturday-Friday working, Sunday off")
    
    def is_working_day_enhanced(self, check_date):
        """Enhanced working day check using configuration"""
        if isinstance(check_date, str):
            check_date = datetime.strptime(check_date, '%Y-%m-%d').date()
        
        # Check if it's a holiday first
        cursor = self.conn.cursor()
        cursor.execute('SELECT id FROM holidays WHERE date = ?', (check_date.strftime('%Y-%m-%d'),))
        if cursor.fetchone():
            return False
        
        # Check working days configuration (0=Sunday, 1=Monday, ..., 6=Saturday)
        day_of_week = check_date.weekday()  # 0=Monday, 6=Sunday
        # Convert to our system: 0=Sunday, 1=Monday, ..., 6=Saturday
        adjusted_day = (day_of_week + 1) % 7
        
        cursor.execute('SELECT is_working FROM working_days_config WHERE day_of_week = ?', (adjusted_day,))
        result = cursor.fetchone()
        
        if result:
            return bool(result[0])
        else:
            # Fallback: Saturday (6) through Friday (5) working, Sunday (0) off
            return adjusted_day != 0
    
    def update_session_windows_enhanced(self):
        """Update session windows with enhanced configuration"""
        cursor = self.conn.cursor()
        
        # Check if session_windows table exists and has the required columns
        cursor.execute("PRAGMA table_info(session_windows)")
        columns = [row[1] for row in cursor.fetchall()]
        
        # Add missing columns if they don't exist
        if 'attendance_window_minutes' not in columns:
            cursor.execute('ALTER TABLE session_windows ADD COLUMN attendance_window_minutes INTEGER DEFAULT 45')
        if 'is_required' not in columns:
            cursor.execute('ALTER TABLE session_windows ADD COLUMN is_required BOOLEAN DEFAULT TRUE')
        
        # Clear existing session windows and insert Phase 1 configuration
        cursor.execute('DELETE FROM session_windows')
        
        # Insert Phase 1 session windows
        enhanced_sessions = [
            ('morning', '08:45:00', '09:30:00', 10, 'Morning Lecture Session', 1, True, True, 45),
            ('afternoon', '13:45:00', '14:30:00', 10, 'Afternoon Lecture Session', 2, True, True, 45),
            ('evening', '18:00:00', '23:59:00', 15, 'Evening Lab/Project Session', 3, True, True, 360)
        ]
        
        for session_data in enhanced_sessions:
            cursor.execute('''
                INSERT INTO session_windows 
                (session_name, start_time, end_time, grace_minutes, description, 
                 display_order, is_active, is_required, attendance_window_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', session_data)
        
        self.conn.commit()
        
        # Update the instance session_windows
        self.session_windows = self.get_session_windows_enhanced()
        print("✅ Session windows updated with Phase 1 configuration")
    
    def get_session_windows_enhanced(self):
        """Get enhanced session windows with all details"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT session_name, start_time, end_time, grace_minutes, 
                   description, display_order, is_required, attendance_window_minutes
            FROM session_windows 
            WHERE is_active = TRUE 
            ORDER BY display_order
        ''')
        
        sessions = []
        for row in cursor.fetchall():
            sessions.append({
                'name': row[0],
                'start_time': row[1],
                'end_time': row[2],
                'grace_minutes': row[3] or 10,
                'description': row[4],
                'order': row[5],
                'is_required': row[6],
                'window_minutes': row[7] or 45
            })
        
        return sessions
    
    def get_current_session_enhanced(self, current_time=None):
        """Enhanced current session detection with grace periods"""
        if current_time is None:
            current_time = datetime.now().time()
        elif isinstance(current_time, str):
            current_time = datetime.strptime(current_time, '%H:%M:%S').time()
        
        for session in self.session_windows:
            start_time = datetime.strptime(session['start_time'], '%H:%M:%S').time()
            end_time = datetime.strptime(session['end_time'], '%H:%M:%S').time()
            
            # Add grace period
            grace_minutes = session.get('grace_minutes', 10)
            grace_delta = timedelta(minutes=grace_minutes)
            
            # Calculate grace periods
            start_with_grace = (datetime.combine(date.today(), start_time) - grace_delta).time()
            end_with_grace = (datetime.combine(date.today(), end_time) + grace_delta).time()
            
            # Handle overnight sessions (like evening session)
            if end_time < start_time:  # Session crosses midnight
                if current_time >= start_with_grace or current_time <= end_with_grace:
                    return session
            else:  # Normal session within same day
                if start_with_grace <= current_time <= end_with_grace:
                    return session
        
        return None
    
    def mark_attendance_enhanced(self, student_id, session_name=None, manual=False, manual_date=None, reason=None):
        """Enhanced attendance marking with automatic session detection"""
        try:
            # Determine date
            if manual_date:
                attendance_date = datetime.strptime(manual_date, '%Y-%m-%d').date()
            else:
                attendance_date = date.today()
            
            # Check if it's a working day
            if not self.is_working_day_enhanced(attendance_date):
                return {
                    'success': False,
                    'message': f'Cannot mark attendance on {attendance_date.strftime("%A")} - not a working day'
                }
            
            current_time = datetime.now().time()
            
            # Auto-detect session if not provided
            if session_name is None and not manual:
                current_session = self.get_current_session_enhanced(current_time)
                if not current_session:
                    return {
                        'success': False,
                        'message': 'No active session window. Attendance can only be marked during session hours (Morning: 8:45-9:30, Afternoon: 1:45-2:30, Evening: after 6:00 PM)'
                    }
                session_name = current_session['name']
            elif session_name is None and manual:
                # For manual attendance, default to morning session
                session_name = 'morning'
            
            cursor = self.conn.cursor()
            
            # Check if already marked for this session today
            cursor.execute('''
                SELECT id FROM session_attendance 
                WHERE student_id = ? AND date = ? AND session_name = ?
            ''', (student_id, attendance_date.strftime('%Y-%m-%d'), session_name))
            
            if cursor.fetchone():
                return {
                    'success': False,
                    'message': f'Attendance already marked for {session_name} session on {attendance_date.strftime("%Y-%m-%d")}'
                }
            
            # Determine attendance status
            status = 'present'
            if not manual:
                # Check if late based on session end time
                session_info = next((s for s in self.session_windows if s['name'] == session_name), None)
                if session_info:
                    session_end = datetime.strptime(session_info['end_time'], '%H:%M:%S').time()
                    grace_minutes = session_info.get('grace_minutes', 10)
                    
                    # Calculate late threshold
                    end_with_grace = (datetime.combine(date.today(), session_end) + 
                                    timedelta(minutes=grace_minutes)).time()
                    
                    if current_time > end_with_grace:
                        status = 'late'
            
            # Insert session attendance record
            cursor.execute('''
                INSERT INTO session_attendance 
                (student_id, date, session_name, time_marked, is_manual, manual_reason, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                student_id,
                attendance_date.strftime('%Y-%m-%d'),
                session_name,
                current_time.strftime('%H:%M:%S'),
                manual,
                reason,
                status
            ))
            
            # Update daily attendance summary
            self._update_daily_attendance_summary(student_id, attendance_date)
            
            self.conn.commit()
            
            return {
                'success': True,
                'message': f'Attendance marked for {session_name} session',
                'session': session_name,
                'status': status,
                'time': current_time.strftime('%H:%M:%S'),
                'date': attendance_date.strftime('%Y-%m-%d')
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Error marking attendance: {str(e)}'
            }
    
    def _update_daily_attendance_summary(self, student_id, attendance_date):
        """Update daily attendance summary in main attendance table"""
        cursor = self.conn.cursor()
        
        # Get all sessions for this student on this date
        cursor.execute('''
            SELECT session_name, time_marked, status FROM session_attendance
            WHERE student_id = ? AND date = ?
            ORDER BY time_marked
        ''', (student_id, attendance_date.strftime('%Y-%m-%d')))
        
        session_records = cursor.fetchall()
        total_sessions = len(self.session_windows)
        attended_sessions = len(session_records)
        
        # Create session summary
        session_data = {
            'sessions': [
                {
                    'name': r[0], 
                    'time': r[1], 
                    'status': r[2]
                } for r in session_records
            ],
            'total_possible': total_sessions,
            'attended': attended_sessions,
            'attendance_type': 'multi_session'
        }
        
        # Get first session time as primary time_in
        primary_time = session_records[0][1] if session_records else None
        
        # Calculate overall daily status
        if attended_sessions == total_sessions:
            daily_status = 'present'
        elif attended_sessions > 0:
            daily_status = 'partial'
        else:
            daily_status = 'absent'
        
        # Update or insert daily attendance record
        cursor.execute('''
            INSERT OR REPLACE INTO attendance 
            (student_id, date, time_in, session_data, total_sessions_today, 
             attended_sessions, is_manual, manual_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            student_id,
            attendance_date.strftime('%Y-%m-%d'),
            primary_time,
            json.dumps(session_data),
            total_sessions,
            attended_sessions,
            any(r[2] == 'manual' for r in session_records),
            'Multi-session attendance summary'
        ))
    
    def generate_saturday_first_calendar(self, year, month):
        """Generate calendar with Saturday-first week layout"""
        try:
            from calendar import monthrange
            import calendar
            
            # Get month information
            first_weekday, num_days = monthrange(year, month)
            month_name = calendar.month_name[month]
            
            # Adjust first day for Saturday-first week
            # Python's weekday: Monday=0, Sunday=6
            # Our layout: Saturday=0, Sunday=1, Monday=2, ..., Friday=6
            saturday_first_adjustment = (first_weekday + 2) % 7
            
            # Calculate starting date for calendar grid
            first_date = datetime(year, month, 1)
            start_date = first_date - timedelta(days=saturday_first_adjustment)
            
            # Generate 6 weeks of calendar data
            calendar_weeks = []
            current_date = start_date
            
            for week in range(6):
                week_days = []
                for day_index in range(7):  # Saturday to Friday
                    day_info = {
                        'date': current_date.strftime('%Y-%m-%d'),
                        'day': current_date.day,
                        'is_current_month': current_date.month == month,
                        'is_today': current_date.date() == datetime.now().date(),
                        'is_working_day': self.is_working_day_enhanced(current_date),
                        'weekday_name': ['SAT', 'SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI'][day_index],
                        'weekday_full': current_date.strftime('%A')
                    }
                    
                    # Check for holidays
                    cursor = self.conn.cursor()
                    cursor.execute('SELECT name FROM holidays WHERE date = ?', 
                                 (current_date.strftime('%Y-%m-%d'),))
                    holiday = cursor.fetchone()
                    if holiday:
                        day_info['is_holiday'] = True
                        day_info['holiday_name'] = holiday[0]
                    else:
                        day_info['is_holiday'] = False
                    
                    week_days.append(day_info)
                    current_date += timedelta(days=1)
                
                calendar_weeks.append(week_days)
            
            return {
                'success': True,
                'calendar_weeks': calendar_weeks,
                'month_name': month_name,
                'year': year,
                'layout': 'saturday_first',
                'working_days_per_week': 6,
                'week_headers': ['SAT', 'SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI']
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Calendar generation failed: {str(e)}'
            }
    
    def get_student_attendance_enhanced_v2(self, student_id, start_date=None, end_date=None):
        """Enhanced student attendance with session details and Saturday-first calendar"""
        try:
            cursor = self.conn.cursor()
            
            # Get student info
            cursor.execute('SELECT name, student_id, email, joining_date FROM students WHERE id = ?', (student_id,))
            student_info = cursor.fetchone()
            if not student_info:
                return {'success': False, 'message': 'Student not found'}
            
            # Determine date range
            if not end_date:
                end_date = date.today()
            else:
                end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
            
            if not start_date:
                if student_info[3]:  # joining_date
                    try:
                        start_date = datetime.strptime(student_info[3], '%Y-%m-%d').date()
                    except:
                        start_date = date.today().replace(month=1, day=1)
                else:
                    start_date = date.today().replace(month=1, day=1)
            else:
                start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            
            # Get session attendance records
            cursor.execute('''
                SELECT date, session_name, time_marked, status, is_manual, manual_reason
                FROM session_attendance 
                WHERE student_id = ? AND date BETWEEN ? AND ?
                ORDER BY date, time_marked
            ''', (student_id, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
            
            session_records = cursor.fetchall()
            
            # Get holidays
            cursor.execute('SELECT date, name FROM holidays WHERE date BETWEEN ? AND ?',
                          (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
            holidays = cursor.fetchall()
            holiday_dict = {h[0]: h[1] for h in holidays}
            
            # Organize session data
            session_by_date = {}
            for record in session_records:
                date_str = record[0]
                if date_str not in session_by_date:
                    session_by_date[date_str] = []
                session_by_date[date_str].append({
                    'session': record[1],
                    'time': record[2],
                    'status': record[3],
                    'manual': record[4],
                    'reason': record[5]
                })
            
            # Create attendance calendar data
            attendance_data = {}
            working_days = []
            present_days = 0
            partial_days = 0
            total_sessions_attended = 0
            
            current_date = start_date
            while current_date <= end_date:
                date_str = current_date.strftime('%Y-%m-%d')
                
                if date_str in holiday_dict:
                    attendance_data[date_str] = 'holiday'
                elif self.is_working_day_enhanced(current_date):
                    working_days.append(current_date)
                    day_sessions = session_by_date.get(date_str, [])
                    sessions_count = len(day_sessions)
                    total_sessions_attended += sessions_count
                    
                    if sessions_count == len(self.session_windows):
                        attendance_data[date_str] = 'present'
                        present_days += 1
                    elif sessions_count > 0:
                        attendance_data[date_str] = 'partial'
                        partial_days += 1
                    else:
                        attendance_data[date_str] = 'absent'
                else:
                    attendance_data[date_str] = 'weekend'
                
                current_date += timedelta(days=1)
            
            # Calculate statistics
            total_working_days = len(working_days)
            absent_days = total_working_days - present_days - partial_days
            total_possible_sessions = total_working_days * len(self.session_windows)
            
            daily_percentage = (present_days / total_working_days * 100) if total_working_days > 0 else 0
            session_percentage = (total_sessions_attended / total_possible_sessions * 100) if total_possible_sessions > 0 else 0
            
            return {
                'success': True,
                'attendance': attendance_data,
                'session_details': session_by_date,
                'session_windows': self.session_windows,
                'stats': {
                    'present_days': present_days,
                    'partial_days': partial_days,
                    'absent_days': absent_days,
                    'total_working_days': total_working_days,
                    'total_sessions_attended': total_sessions_attended,
                    'total_possible_sessions': total_possible_sessions,
                    'daily_percentage': round(daily_percentage, 1),
                    'session_percentage': round(session_percentage, 1),
                    'holidays': len(holiday_dict)
                },
                'student_info': {
                    'name': student_info[0],
                    'student_id': student_info[1],
                    'email': student_info[2],
                    'joining_date': student_info[3]
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Error retrieving attendance: {str(e)}'
            }
    
    def get_today_attendance_enhanced_v2(self):
        """Enhanced today's attendance with detailed session breakdown"""
        try:
            today = date.today().strftime('%Y-%m-%d')
            cursor = self.conn.cursor()
            
            # Get all active students
            cursor.execute('SELECT id, name, student_id, email FROM students WHERE status = "active" ORDER BY name')
            students = cursor.fetchall()
            
            # Get today's session attendance
            cursor.execute('''
                SELECT student_id, session_name, time_marked, status, is_manual
                FROM session_attendance 
                WHERE date = ?
                ORDER BY student_id, time_marked
            ''', (today,))
            
            session_records = cursor.fetchall()
            
            # Organize by student
            result = []
            for student in students:
                student_id, name, student_id_str, email = student
                
                # Get this student's sessions today
                student_sessions = [r for r in session_records if r[0] == student_id]
                
                # Organize sessions
                sessions_dict = {}
                for session_record in student_sessions:
                    session_name = session_record[1]
                    sessions_dict[session_name] = {
                        'time': session_record[2],
                        'status': session_record[3],
                        'manual': session_record[4]
                    }
                
                # Calculate overall status
                total_sessions = len(self.session_windows)
                attended_sessions = len(sessions_dict)
                
                if attended_sessions == total_sessions:
                    overall_status = 'present'
                elif attended_sessions > 0:
                    overall_status = 'partial'
                else:
                    overall_status = 'absent'
                
                result.append({
                    'student_id': student_id,
                    'name': name,
                    'student_id_str': student_id_str,
                    'email': email,
                    'sessions': sessions_dict,
                    'overall_status': overall_status,
                    'sessions_attended': attended_sessions,
                    'total_sessions': total_sessions
                })
            
            return result
            
        except Exception as e:
            print(f"Error in get_today_attendance_enhanced_v2: {e}")
            return []
    
    # Bind methods to the attendance_system instance
    attendance_system.update_working_days_config = update_working_days_config.__get__(attendance_system)
    attendance_system.is_working_day_enhanced = is_working_day_enhanced.__get__(attendance_system)
    attendance_system.update_session_windows_enhanced = update_session_windows_enhanced.__get__(attendance_system)
    attendance_system.get_session_windows_enhanced = get_session_windows_enhanced.__get__(attendance_system)
    attendance_system.get_current_session_enhanced = get_current_session_enhanced.__get__(attendance_system)
    attendance_system.mark_attendance_enhanced = mark_attendance_enhanced.__get__(attendance_system)
    attendance_system._update_daily_attendance_summary = _update_daily_attendance_summary.__get__(attendance_system)
    attendance_system.generate_saturday_first_calendar = generate_saturday_first_calendar.__get__(attendance_system)
    attendance_system.get_student_attendance_enhanced_v2 = get_student_attendance_enhanced_v2.__get__(attendance_system)
    attendance_system.get_today_attendance_enhanced_v2 = get_today_attendance_enhanced_v2.__get__(attendance_system)


def add_phase1_api_endpoints(app, attendance_system):
    """
    Add these new API endpoints to your FastAPI app
    Call this after creating your app and attendance_system
    """
    
    @app.get("/api/working-days/config")
    async def get_working_days_config():
        """Get working days configuration"""
        try:
            cursor = attendance_system.conn.cursor()
            cursor.execute('''
                SELECT day_of_week, is_working, day_name 
                FROM working_days_config 
                ORDER BY day_of_week
            ''')
            
            config = {}
            for row in cursor.fetchall():
                config[row[0]] = {
                    'is_working': bool(row[1]),
                    'day_name': row[2]
                }
            
            return {
                'success': True,
                'working_days': config,
                'schedule': 'Saturday through Friday working, Sunday off',
                'total_working_days_per_week': 6
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    @app.get("/api/session/windows/enhanced")
    async def get_enhanced_session_windows():
        """Get enhanced session windows configuration"""
        try:
            sessions = attendance_system.get_session_windows_enhanced()
            return {
                'success': True,
                'sessions': sessions,
                'total_sessions_per_day': len(sessions)
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    @app.get("/api/session/current/enhanced")
    async def get_current_session_enhanced():
        """Get currently active session with enhanced details"""
        try:
            current_session = attendance_system.get_current_session_enhanced()
            if current_session:
                return {
                    'success': True,
                    'session': current_session,
                    'is_active': True
                }
            else:
                return {
                    'success': False,
                    'session': None,
                    'is_active': False,
                    'message': 'No active session at current time'
                }
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    @app.post("/api/attendance/enhanced")
    async def mark_attendance_enhanced_api(data: dict):
        """Mark attendance using enhanced system"""
        try:
            student_id = data.get('student_id')
            session_name = data.get('session_name')
            manual = data.get('manual', False)
            manual_date = data.get('date')
            reason = data.get('reason')
            
            if not student_id:
                return {'success': False, 'message': 'Student ID is required'}
            
            result = attendance_system.mark_attendance_enhanced(
                student_id=student_id,
                session_name=session_name,
                manual=manual,
                manual_date=manual_date,
                reason=reason
            )
            
            return result
            
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    @app.get("/api/calendar/enhanced/{year}/{month}")
    async def get_enhanced_calendar(year: int, month: int):
        """Get Saturday-first calendar layout"""
        try:
            result = attendance_system.generate_saturday_first_calendar(year, month)
            return result
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    @app.get("/api/attendance/student/{student_id}/enhanced-v2")
    async def get_student_attendance_enhanced_v2_api(student_id: int):
        """Get enhanced attendance data with session details"""
        try:
            result = attendance_system.get_student_attendance_enhanced_v2(student_id)
            return result
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    @app.get("/api/attendance/today/enhanced-v2")
    async def get_today_attendance_enhanced_v2_api():
        """Get today's attendance with enhanced session details"""
        try:
            result = attendance_system.get_today_attendance_enhanced_v2()
            return result
        except Exception as e:
            return []
    
    @app.post("/api/system/initialize-phase1")
    async def initialize_phase1():
        """Initialize Phase 1 enhancements"""
        try:
            # Update working days configuration
            attendance_system.update_working_days_config()
            
            # Update session windows
            attendance_system.update_session_windows_enhanced()
            
            return {
                'success': True,
                'message': 'Phase 1 initialization completed successfully',
                'features_enabled': [
                    'Saturday-first calendar layout',
                    '6-day work week (Saturday-Friday)',
                    'Multi-session attendance (Morning, Afternoon, Evening)',
                    'Enhanced working day detection',
                    'Session-based attendance tracking'
                ]
            }
        except Exception as e:
            return {'success': False, 'message': f'Initialization failed: {str(e)}'}
    
    @app.get("/api/system/phase1-status")
    async def get_phase1_status():
        """Get Phase 1 system status"""
        try:
            cursor = attendance_system.conn.cursor()
            
            # Check if enhanced tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='working_days_config'")
            working_days_table = cursor.fetchone() is not None
            
            cursor.execute("SELECT COUNT(*) FROM session_windows WHERE is_active = TRUE")
            active_sessions = cursor.fetchone()[0]
            
            # Check working days configuration
            cursor.execute("SELECT COUNT(*) FROM working_days_config WHERE is_working = TRUE")
            working_days_count = cursor.fetchone()[0] if working_days_table else 0
            
            return {
                'success': True,
                'phase1_initialized': working_days_table and active_sessions >= 3,
                'features': {
                    'working_days_config': working_days_table,
                    'active_sessions': active_sessions,
                    'working_days_per_week': working_days_count,
                    'calendar_layout': 'Saturday-first',
                    'session_attendance_tracking': True
                },
                'session_windows': attendance_system.session_windows
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
