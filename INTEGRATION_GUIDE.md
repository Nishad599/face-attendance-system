# Integration Guide: Slot-based Attendance System

## Overview
This guide helps you integrate the new slot-based attendance system with live student counting.

## Files Created
1. `attendance_manager.py` - Core slot management logic
2. `update_templates.py` - This script that updates HTML templates
3. `new_api_endpoints.py` - New API endpoints to add to your main app

## Integration Steps

### Step 1: Install Dependencies
Make sure you have all required dependencies in your existing environment.

### Step 2: Add Backend Integration
1. Copy `attendance_manager.py` to your project directory
2. Add the new API endpoints from `new_api_endpoints.py` to your `main_with_face_recognition.py`
3. Add this import to your main file:
   ```python
   from attendance_manager import create_slot_manager_instance
   ```

### Step 3: Template Updates
Your `attendance.html` template has been automatically updated with:
- Live student count display
- Slot status information
- Enhanced detection result handling
- Out-of-slot message display

### Step 4: Test the Integration

#### Test Slot Timing
```python
python -c "
from attendance_manager import AttendanceSlotManager
manager = AttendanceSlotManager()
print('Current slot:', manager.get_current_slot())
print('Next slot:', manager.get_next_slot())
print('Live count:', manager.get_live_student_count())
"
```

#### Test API Endpoints
- Visit `/api/attendance/live-count` to see live count
- Visit `/api/attendance/slot-status` to see slot information

### Step 5: Database Migration
The new system creates additional tables:
- `slot_attendance` - Tracks attendance by slot
- `daily_attendance_summary` - Quick count summaries

These are created automatically when you first run the AttendanceSlotManager.

## Features Added

### 1. Time-based Attendance Slots
- **Morning**: 8:30 AM - 9:30 AM
- **Afternoon**: 1:45 PM - 2:00 PM
- Attendance only marked during these windows

### 2. Live Student Count
- Real-time count of students present
- Updates every 10 seconds
- Shows current slot status

### 3. Enhanced Detection
- Face detection works outside slots but doesn't mark attendance
- Clear messaging for out-of-slot detection
- Confidence scoring and slot information

### 4. Better User Experience
- Visual slot status indicators
- Live count display
- Time remaining in current slot
- Next slot countdown

## Customization

### Changing Slot Times
Edit the `attendance_slots` dictionary in `attendance_manager.py`:

```python
self.attendance_slots = {
    'morning': {
        'name': 'Morning Session',
        'start_time': time(8, 30),    # 8:30 AM
        'end_time': time(9, 30),      # 9:30 AM
        'slot_id': 'morning'
    },
    # Add more slots as needed
}
```

### Updating Live Count Frequency
Change the interval in the JavaScript (attendance.html):
```javascript
// Change 10000 (10 seconds) to your preferred interval
liveCountInterval = setInterval(updateLiveCount, 10000);
```

## Troubleshooting

### Database Issues
If you encounter database issues, try:
```python
from attendance_manager import AttendanceSlotManager
manager = AttendanceSlotManager()
# This will recreate tables if needed
```

### Template Issues
If the template updates didn't work correctly:
1. Restore from backup: `templates_backup/`
2. Run `python update_templates.py` again
3. Check file permissions

### API Issues
Make sure all new endpoints are properly added to your main FastAPI app and the attendance_manager is properly imported.

## Support
Check the comments in each file for detailed documentation of functions and usage examples.
