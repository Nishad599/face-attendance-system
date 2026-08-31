/* Reports page behaviour.
 *
 * Extracted from templates/attendance_management.html, where it was ~50KB
 * of inline script re-downloaded on every page load. As an external file
 * the browser (and the service worker) can cache it.
 */

// Global variables
let currentDate = new Date();
let selectedStudent = null;
let studentAttendanceData = {};
let holidays = [];
let allStudentsData = [];
let filteredStudentsData = [];

// Tab switching functionality
function switchTab(tabName) {
    // Remove active class from all tabs
    document.querySelectorAll('.nav-tab').forEach(tab => tab.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    // Add active class to clicked tab and corresponding content
    event.target.classList.add('active');
    document.getElementById(tabName + '-tab').classList.add('active');

    // Load data based on tab
    if (tabName === 'holidays') {
        loadHolidays();
    } else if (tabName === 'today') {
        loadTodayAttendance();
    } else if (tabName === 'overview') {
        loadClassAnalytics();
    }
}

// Initialize page
document.addEventListener('DOMContentLoaded', function () {
    console.log('Page loaded, initializing...');

    loadStudents();
    loadHolidays();
    loadTodayAttendance();

    // Nav link visibility is handled centrally by static/js/navbar.js.

    // Set default dates for export
    const today = new Date().toISOString().split('T')[0];
    const firstDayOfMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString().split('T')[0];

    const exportStartDate = document.getElementById('exportStartDate');
    const exportEndDate = document.getElementById('exportEndDate');

    if (exportStartDate && exportEndDate) {
        exportStartDate.value = firstDayOfMonth;
        exportEndDate.value = today;

        exportStartDate.addEventListener('change', updateDateRangePreview);
        exportEndDate.addEventListener('change', updateDateRangePreview);

        updateDateRangePreview();
    }
});

// Load students
async function loadStudents() {
    try {
        const response = await fetch('/api/students/list');
        const data = await response.json();

        const select = document.getElementById('studentSelect');
        select.innerHTML = '<option value="">Choose a student...</option>';

        if (data.success && data.students) {
            data.students.forEach(student => {
                const option = document.createElement('option');
                option.value = student.id;
                option.textContent = `${student.name} (${student.student_id})`;
                select.appendChild(option);
            });
        }
    } catch (error) {
        console.error('Error loading students:', error);
    }
}

// FIXED: Single loadStudentAttendance function with proper API calls
async function loadStudentAttendance() {
    const studentId = document.getElementById('studentSelect').value;

    if (!studentId) {
        document.getElementById('studentAttendanceContent').style.display = 'none';
        document.getElementById('noStudentSelected').style.display = 'block';
        return;
    }

    selectedStudent = studentId;
    document.getElementById('noStudentSelected').style.display = 'none';
    document.getElementById('studentAttendanceContent').style.display = 'block';

    try {
        // Get student info first (for joining date)
        const studentResponse = await fetch(`/api/students/${studentId}`);
        const studentData = await studentResponse.json();

        if (!studentData.success) {
            console.error('Failed to fetch student data:', studentData.message);
            window.studentJoiningDate = null;
        } else {
            // FIXED: Correct property access for joining date
            window.studentJoiningDate = studentData.student?.joining_date || null;
            console.log('Joining date set to:', window.studentJoiningDate);
        }

        // Get attendance data
        const response = await fetch(`/api/attendance/student/${studentId}/slots`);
        const data = await response.json();

        if (data.success) {
            studentAttendanceData = data.attendance || {};
            updateAttendanceStats(data.stats || {});
            updateCurrentSessionStatus();
            updateCalendar();
        } else {
            console.error('Error loading attendance data:', data.message);
            alert('Error loading attendance data: ' + data.message);
        }
    } catch (error) {
        console.error('Error loading student attendance:', error);
        window.studentJoiningDate = null;
        alert('Error loading attendance data. Please try again.');
    }
}

// Update attendance stats for session-based system
function updateAttendanceStats(stats) {
    document.getElementById('presentDays').textContent = stats.full_days || 0;
    document.getElementById('partialDays').textContent = stats.half_days || 0;
    document.getElementById('absentDays').textContent = stats.absent_days || 0;
    document.getElementById('holidayDays').textContent = stats.holidays || 0;
    document.getElementById('attendancePercentage').textContent = (stats.percentage || 0) + '%';
}

// Update current session status
function updateCurrentSessionStatus() {
    const statusElement = document.getElementById('currentSessionStatus');
    const now = new Date();
    const currentTime = now.getHours() * 60 + now.getMinutes();

    // Session windows from the new 4-slot configuration
    const sessions = [
        { name: 'Morning 1', start: 8 * 60 + 30, end: 9 * 60 + 30 },
        { name: 'Morning 2', start: 11 * 60 + 0, end: 11 * 60 + 15 },
        { name: 'Afternoon 1', start: 13 * 60 + 45, end: 14 * 60 + 0 },
        { name: 'Afternoon 2', start: 16 * 60 + 15, end: 16 * 60 + 45 }
    ];

    let currentSession = null;
    for (let session of sessions) {
        if (currentTime >= session.start && currentTime <= session.end) {
            currentSession = session;
            break;
        }
    }

    if (currentSession) {
        statusElement.className = 'status-indicator active';
        statusElement.innerHTML = `<div class="current-session">${currentSession.name} Session</div>`;
    } else {
        statusElement.className = 'status-indicator inactive';
        statusElement.innerHTML = '<div class="current-session">No Active Session</div>';
    }
}

// FIXED: Calendar function with proper joining date logic
function updateCalendar() {
    console.log('updateCalendar() called');
    console.log('selectedStudent:', selectedStudent);
    console.log('window.studentJoiningDate:', window.studentJoiningDate);

    const calendarGrid = document.getElementById('calendarGrid');
    calendarGrid.innerHTML = '';

    // Add day headers
    const weekdays = ['SAT', 'SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI'];
    weekdays.forEach(day => {
        const header = document.createElement('div');
        header.className = 'calendar-day-header';
        header.textContent = day;
        calendarGrid.appendChild(header);
    });

    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    document.getElementById('calendarTitle').textContent =
        currentDate.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });

    const firstDay = new Date(year, month, 1);
    const startDate = new Date(firstDay);
    startDate.setDate(startDate.getDate() - ((firstDay.getDay() + 1) % 7));

    let currentCalendarDate = new Date(startDate);
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    for (let i = 0; i < 42; i++) {
        const dayElement = document.createElement('div');
        dayElement.className = 'calendar-day';

        const dayNumber = document.createElement('div');
        dayNumber.textContent = currentCalendarDate.getDate();
        dayElement.appendChild(dayNumber);

        const dateString = new Date(currentCalendarDate.getTime() - (currentCalendarDate.getTimezoneOffset() * 60000)).toISOString().split('T')[0];
        const isCurrentMonth = currentCalendarDate.getMonth() === month;
        const isToday = dateString === today.toISOString().split('T')[0];
        const isPastDate = currentCalendarDate < today;
        const dayOfWeek = currentCalendarDate.getDay();

        // FIXED: Proper joining date logic
        const joiningDate = window.studentJoiningDate ? new Date(window.studentJoiningDate) : null;

        if (joiningDate) {
            joiningDate.setHours(0, 0, 0, 0);
        }

        // CORRECTED LOGIC:
        // If NO joining date set: process all past working days (original behavior)
        // If joining date IS set: only process days >= joining date
        const isAfterJoining = !joiningDate || currentCalendarDate >= joiningDate;

        if (!isCurrentMonth) dayElement.classList.add('other-month');
        if (isToday) dayElement.classList.add('today');

        // Check for weekends (only Sunday)
        if (dayOfWeek === 0) {
            dayElement.classList.add('weekend');
        }
        // Check for holidays
        else {
            const holiday = holidays.find(h => h.date === dateString);
            if (holiday) {
                dayElement.classList.add('holiday');
                const tip = document.createElement('div');
                tip.className = 'cal-tooltip';
                tip.textContent = '🎉 ' + holiday.name;
                dayElement.appendChild(tip);
            }
            // Process attendance for working days
            else if (selectedStudent && isPastDate && dayOfWeek !== 0 && isAfterJoining) {
                const sessionData = studentAttendanceData[dateString];

                if (sessionData) {
                    // Has session data (new 4-slot logic)
                    const sessionBadges = document.createElement('div');
                    sessionBadges.className = 'session-badges';

                    const count = sessionData.count || 0;
                    const fmtSlot = (val) => val ? val.split(' ').pop().substring(0, 5) : '—';

                    if (count === 4) {
                        dayElement.classList.add('present');
                    } else if (count > 0) {
                        dayElement.classList.add('partial');
                    }

                    // Create styled tooltip
                    const tip = document.createElement('div');
                    tip.className = 'cal-tooltip';
                    tip.innerHTML = `<strong>${dateString}</strong>\n` +
                        `${count === 4 ? '✅ Full Day' : count > 0 ? '⚠️ Partial (' + count + '/4)' : ''}\n` +
                        `M1: ${fmtSlot(sessionData.m1)}\n` +
                        `M2: ${fmtSlot(sessionData.m2)}\n` +
                        `A1: ${fmtSlot(sessionData.a1)}\n` +
                        `A2: ${fmtSlot(sessionData.a2)}`;
                    dayElement.appendChild(tip);

                    // Show up to 4 micro-badges
                    const slots = ['m1', 'm2', 'a1', 'a2'];
                    slots.forEach(s => {
                        if (sessionData[s]) {
                            const badge = document.createElement('div');
                            badge.className = `session-badge ${s.startsWith('m') ? 'morning' : 'afternoon'}`;
                            sessionBadges.appendChild(badge);
                        }
                    });
                    dayElement.appendChild(sessionBadges);
                }
                if (!sessionData) {
                    // No session data for this working day = absent
                    dayElement.classList.add('absent');
                    const tip = document.createElement('div');
                    tip.className = 'cal-tooltip';
                    tip.innerHTML = `<strong>${dateString}</strong>\n❌ Absent\nNo sessions attended`;
                    dayElement.appendChild(tip);
                }
            }
        }

        calendarGrid.appendChild(dayElement);
        currentCalendarDate.setDate(currentCalendarDate.getDate() + 1);
    }

    console.log('updateCalendar() completed successfully');
}

// FIXED: Navigation functions with error handling
function previousMonth() {
    try {
        console.log('previousMonth() called');
        currentDate.setMonth(currentDate.getMonth() - 1);
        updateCalendar();
        console.log('previousMonth() completed');
    } catch (error) {
        console.error('Error in previousMonth():', error);
    }
}

function nextMonth() {
    try {
        console.log('nextMonth() called');
        currentDate.setMonth(currentDate.getMonth() + 1);
        updateCalendar();
        console.log('nextMonth() completed');
    } catch (error) {
        console.error('Error in nextMonth():', error);
    }
}

// Enhanced today's attendance with session data
async function loadTodayAttendance() {
    try {
        const response = await fetch('/api/attendance/today/slots');
        const data = await response.json();

        allStudentsData = data || [];
        filteredStudentsData = [...allStudentsData];
        displayTodayAttendance();

    } catch (error) {
        console.error("Error loading today's attendance:", error);
        document.getElementById('todayAttendanceTableBody').innerHTML =
            '<tr><td colspan="8" style="text-align: center; color: red;">Error loading data</td></tr>';
    }
}

function displayTodayAttendance() {
    const tbody = document.getElementById('todayAttendanceTableBody');

    if (!filteredStudentsData || filteredStudentsData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center;">No attendance records found</td></tr>';
        updateSearchResultCount(0, 0);
        return;
    }

    let tableHTML = '';
    filteredStudentsData.forEach(record => {
        const studentName = record[0];
        const studentId = record[1];
        const email = record[2];
        const m1 = record[3];
        const m2 = record[4];
        const a1 = record[5];
        const a2 = record[6];
        const dbId = record[7];

        const has_m1 = m1 !== null && m1 !== undefined && m1 !== '';
        const has_m2 = m2 !== null && m2 !== undefined && m2 !== '';
        const has_a1 = a1 !== null && a1 !== undefined && a1 !== '';
        const has_a2 = a2 !== null && a2 !== undefined && a2 !== '';

        let slots_count = 0;
        if (has_m1) slots_count++;
        if (has_m2) slots_count++;
        if (has_a1) slots_count++;
        if (has_a2) slots_count++;

        let status, statusClass;
        if (slots_count === 4) {
            status = 'Full Day Present';
            statusClass = 'status-present';
        } else if (slots_count > 0) {
            status = `Partial (${slots_count}/4)`;
            statusClass = 'status-partial';
        } else {
            status = 'Absent';
            statusClass = 'status-absent';
        }

        // Attach calculated status back to record for sorting (index 8)
        record[8] = status;

        const today = new Date().toLocaleDateString();

        // Extract just HH:MM from datetime strings like "2026-04-28 09:01:06"
        const fmtTime = (val) => {
            if (!val) return '-';
            const parts = val.split(' ');
            if (parts.length >= 2) return parts[1].substring(0, 5); // "09:01"
            return val;
        };

        tableHTML += `
            <tr class="clickable-row" onclick="showStudentSparkline(${dbId}, '${studentName}')" title="Click to view history">
                <td><strong>${studentName}</strong></td>
                <td>${studentId || 'N/A'}</td>
                <td>${email}</td>
                <td class="${statusClass}">${status}</td>
                <td>${fmtTime(m1)}</td>
                <td>${fmtTime(m2)}</td>
                <td>${fmtTime(a1)}</td>
                <td>${fmtTime(a2)}</td>
                <td>${today}</td>
            </tr>
        `;
    });

    tbody.innerHTML = tableHTML;
    updateSearchResultCount(filteredStudentsData.length, allStudentsData.length);
}

function filterAttendanceTable() {
    const searchTerm = document.getElementById('attendanceSearch').value.toLowerCase();
    const statusFilter = document.getElementById('statusFilter').value;

    filteredStudentsData = allStudentsData.filter(record => {
        const studentName = (record[0] || '').toLowerCase();
        const studentId = (record[1] || '').toLowerCase();
        const email = (record[2] || '').toLowerCase();

        const m1 = record[3];
        const m2 = record[4];
        const a1 = record[5];
        const a2 = record[6];

        const has_m1 = m1 !== null && m1 !== undefined && m1 !== '';
        const has_m2 = m2 !== null && m2 !== undefined && m2 !== '';
        const has_a1 = a1 !== null && a1 !== undefined && a1 !== '';
        const has_a2 = a2 !== null && a2 !== undefined && a2 !== '';

        let slots_count = 0;
        if (has_m1) slots_count++;
        if (has_m2) slots_count++;
        if (has_a1) slots_count++;
        if (has_a2) slots_count++;

        // Search filter
        const matchesSearch = studentName.includes(searchTerm) ||
            studentId.includes(searchTerm) ||
            email.includes(searchTerm);

        // Status filter
        let matchesStatus = true;
        if (statusFilter === 'present') {
            matchesStatus = slots_count === 4;
        } else if (statusFilter === 'partial') {
            matchesStatus = slots_count > 0 && slots_count < 4;
        } else if (statusFilter === 'absent') {
            matchesStatus = slots_count === 0;
        }

        return matchesSearch && matchesStatus;
    });

    displayTodayAttendance();
}

function updateSearchResultCount(filtered, total) {
    const countElement = document.getElementById('searchResultCount');
    if (filtered === total) {
        countElement.textContent = `${total} students`;
    } else {
        countElement.textContent = `${filtered} of ${total} students`;
    }
}

function showDatePicker() {
    if (!selectedStudent) {
        alert('Please select a student first');
        return;
    }

    document.getElementById('attendanceDate').value = '';
    document.getElementById('sessionType').value = '';
    document.getElementById('manualAttendanceModal').style.display = 'block';
}

async function submitManualAttendance() {
    const date = document.getElementById('attendanceDate').value;
    const sessionType = document.getElementById('sessionType').value;
    const reason = document.getElementById('attendanceReason').value;

    if (!date || !selectedStudent || !sessionType) {
        alert('Please fill all required fields');
        return;
    }

    try {
        const response = await fetch('/api/attendance/manual/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                student_id: selectedStudent,
                date: date,
                session_type: sessionType,
                reason: reason
            })
        });

        const data = await response.json();

        if (data.success) {
            closeModal('manualAttendanceModal');
            if (typeof loadStudentAttendance === 'function') loadStudentAttendance();
            if (typeof loadTodayAttendance === 'function') loadTodayAttendance();
            alert(`Success: ${data.message}`);
        } else {
            alert('Error: ' + data.message);
        }
    } catch (error) {
        alert('<i class="fa-solid fa-xmark text-red-500"></i> Error marking attendance: ' + error.message);
    }
}

// Holiday management functions
async function loadHolidays() {
    try {
        const response = await fetch('/api/holidays');
        const data = await response.json();

        if (data.success) {
            holidays = data.holidays || [];
            displayHolidays();
            updateCalendar();
        }
    } catch (error) {
        console.error('Error loading holidays:', error);
    }
}

function displayHolidays() {
    const container = document.getElementById('holidaysList');

    if (holidays.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">🗓️</div>
                <h3>No Holidays</h3>
                <p>Add holidays to exclude them from attendance calculations</p>
            </div>
        `;
        return;
    }

    holidays.sort((a, b) => new Date(b.date) - new Date(a.date));

    let html = '<div style="display: grid; gap: 1rem;">';
    holidays.forEach(holiday => {
        const date = new Date(holiday.date).toLocaleDateString();
        html += `
            <div class="card" style="padding: 1.5rem; display: flex; justify-content: space-between; align-items: center; margin: 0;">
                <div>
                    <strong style="font-size: 1.1rem; color: #1a202c;">${holiday.name}</strong>
                    <div style="color: #64748b; font-size: 0.875rem; margin-top: 0.25rem;">
                        <i class="fa-solid fa-calendar-days"></i> ${date} • ${holiday.type.replace('_', ' ').toUpperCase()}
                    </div>
                </div>
                <button class="btn btn-outline" onclick="deleteHoliday(${holiday.id})" style="padding: 0.5rem 0.75rem;">
                    🗑️ Delete
                </button>
            </div>
        `;
    });
    html += '</div>';

    container.innerHTML = html;
}

function showAddHolidayModal() {
    document.getElementById('addHolidayModal').style.display = 'block';
}

async function submitHoliday() {
    const date = document.getElementById('holidayDate').value;
    const name = document.getElementById('holidayName').value;
    const type = document.getElementById('holidayType').value;

    if (!date || !name || !type) {
        alert('Please fill all fields');
        return;
    }

    try {
        const response = await fetch('/api/holidays', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date, name, type })
        });

        const data = await response.json();

        if (data.success) {
            closeModal('addHolidayModal');
            loadHolidays();
            alert('<i class="fa-solid fa-check text-green-500"></i> Holiday added successfully!');
        } else {
            alert('<i class="fa-solid fa-xmark text-red-500"></i> Error: ' + data.message);
        }
    } catch (error) {
        alert('<i class="fa-solid fa-xmark text-red-500"></i> Error adding holiday: ' + error.message);
    }
}

async function deleteHoliday(holidayId) {
    if (!confirm('Are you sure you want to delete this holiday?')) return;

    try {
        const response = await fetch(`/api/holidays/${holidayId}`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (data.success) {
            loadHolidays();
            alert('<i class="fa-solid fa-check text-green-500"></i> Holiday deleted successfully!');
        } else {
            alert('<i class="fa-solid fa-xmark text-red-500"></i> Error: ' + data.message);
        }
    } catch (error) {
        alert('<i class="fa-solid fa-xmark text-red-500"></i> Error deleting holiday: ' + error.message);
    }
}

// Export functions
function exportAttendance() {
    if (!selectedStudent) {
        alert('Please select a student first');
        return;
    }

    window.open(`/api/attendance/export/${selectedStudent}`, '_blank');
}

function showBulkExportModal() {
    const modal = document.getElementById('bulkExportModal');
    if (modal) {
        modal.style.display = 'block';
        const today = new Date().toISOString().split('T')[0];
        const firstDayOfMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString().split('T')[0];

        document.getElementById('exportStartDate').value = firstDayOfMonth;
        document.getElementById('exportEndDate').value = today;
        updateDateRangePreview();
    }
}

function closeBulkExportModal() {
    const modal = document.getElementById('bulkExportModal');
    if (modal) {
        modal.style.display = 'none';
        document.getElementById('exportStartDate').value = '';
        document.getElementById('exportEndDate').value = '';
        document.getElementById('exportFormat').value = 'session_detailed';
        document.getElementById('includeWeekends').checked = false;
        document.getElementById('includeHolidays').checked = false;
        document.getElementById('dateRangePreview').textContent = 'Select dates to see preview';
    }
}

function updateDateRangePreview() {
    const startDate = document.getElementById('exportStartDate').value;
    const endDate = document.getElementById('exportEndDate').value;
    const previewElement = document.getElementById('dateRangePreview');

    if (startDate && endDate) {
        const start = new Date(startDate);
        const end = new Date(endDate);

        if (end >= start) {
            const diffTime = Math.abs(end - start);
            const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24)) + 1;

            previewElement.textContent = `${diffDays} days from ${start.toLocaleDateString()} to ${end.toLocaleDateString()}`;
            previewElement.style.color = '#666';
        } else {
            previewElement.textContent = 'End date must be after start date';
            previewElement.style.color = '#ef4444';
            return;
        }
    } else {
        previewElement.textContent = 'Select dates to see preview';
        previewElement.style.color = '#666';
    }
}

async function exportBulkAttendance() {
    const startDate = document.getElementById('exportStartDate').value;
    const endDate = document.getElementById('exportEndDate').value;
    const format = document.getElementById('exportFormat').value;
    const includeWeekends = document.getElementById('includeWeekends').checked;
    const includeHolidays = document.getElementById('includeHolidays').checked;

    if (!startDate || !endDate) {
        alert('Please select both start and end dates');
        return;
    }

    const params = new URLSearchParams({
        start_date: startDate,
        end_date: endDate,
        format: format,
        include_weekends: includeWeekends,
        include_holidays: includeHolidays
    });

    window.open(`/api/attendance/bulk-export?${params}`, '_blank');
    closeBulkExportModal();
}

// Utility functions
function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = 'none';

        if (modalId === 'manualAttendanceModal') {
            document.getElementById('attendanceDate').value = '';
            document.getElementById('sessionType').value = '';
            document.getElementById('attendanceReason').value = '';
        } else if (modalId === 'addHolidayModal') {
            document.getElementById('holidayDate').value = '';
            document.getElementById('holidayName').value = '';
            document.getElementById('holidayType').value = '';
        }
    }
}

function performLogout() {
    if (confirm('Are you sure you want to logout?')) {
        window.location.href = '/logout';
    }
}

// Close modal when clicking outside
window.onclick = function (event) {
    const modals = document.querySelectorAll('.modal');
    modals.forEach(modal => {
        if (event.target === modal) {
            modal.style.display = 'none';
        }
    });
}

// Keyboard event handling
document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
        const visibleModal = document.querySelector('.modal[style*="block"]');
        if (visibleModal) {
            visibleModal.style.display = 'none';
        }
    }
});

// --- Class Analytics Logic ---
let trendChart = null;
let slotChart = null;
let distributionChart = null;
let dayOfWeekChart = null;
let sparklineChart = null;

async function ensureAnalyticsBatches() {
    const sel = document.getElementById('analyticsBatch');
    if (sel.dataset.loaded) return;
    try {
        const r = await fetch('/api/courses');
        const d = await r.json();
        (d.courses || []).filter(b => b.is_active).forEach(b => {
            const o = document.createElement('option');
            o.value = b.id; o.textContent = b.name; sel.appendChild(o);
        });
        sel.dataset.loaded = '1';
    } catch (e) { }
}

async function loadClassAnalytics() {
    await ensureAnalyticsBatches();
    const range = document.getElementById('overviewDateRange').value;
    const cid = document.getElementById('analyticsBatch').value;
    const cq = cid ? `&course_id=${cid}` : '';
    const loader = document.getElementById('loadingIndicator');
    if (loader) loader.style.display = 'block';

    try {
        // Parallel requests for better performance
        const [classRes, heatmapRes, dayOfWeekRes, atRiskRes] = await Promise.all([
            fetch(`/api/attendance/analytics/class?days=${range}${cq}`),
            fetch(`/api/analytics/heatmap?days=90${cq}`),
            fetch(`/api/analytics/day-of-week?days=60${cq}`),
            fetch(`/api/analytics/at-risk?threshold=75${cq}`)
        ]);

        const classData = await classRes.json();
        const heatmapData = await heatmapRes.json();
        const dayOfWeekData = await dayOfWeekRes.json();
        const atRiskData = await atRiskRes.json();

        if (classData.success) {
            updateOverviewKPIs(classData);
            renderTrendChart(classData.trend);
            renderSlotChart(classData.slot_performance);
            renderDistributionChart(classData.trend); // Derive from trend
            renderLeaderboard('topPerformersList', classData.top_performers, 'green');
        }

        if (heatmapData.success) {
            renderHeatmap(heatmapData.heatmap);
        }

        if (dayOfWeekData.success) {
            renderDayOfWeekChart(dayOfWeekData.days);
        }

        if (atRiskData.success) {
            renderAtRiskList(atRiskData.at_risk);
            document.getElementById('atRiskCount').textContent = atRiskData.count;
        }

    } catch (error) {
        console.error('Error fetching analytics:', error);
    } finally {
        if (loader) loader.style.display = 'none';
    }
}

function updateOverviewKPIs(data) {
    document.getElementById('avgClassAttendance').textContent = data.avg_attendance + '%';
    document.getElementById('totalActiveStudents').textContent = data.total_students;
    document.getElementById('peakSlotNum').textContent = data.peak_slot;

    // Trend calculation (Simplified: compare last 7 with previous 7)
    if (data.trend && data.trend.length >= 14) {
        const recent = data.trend.slice(-7).reduce((acc, curr) => acc + curr.pct, 0) / 7;
        const previous = data.trend.slice(-14, -7).reduce((acc, curr) => acc + curr.pct, 0) / 7;
        const diff = recent - previous;
        const trendEl = document.getElementById('attendanceTrendNum');

        if (diff > 0) {
            trendEl.className = 'stat-trend trend-up';
            trendEl.innerHTML = `<i class="fa-solid fa-arrow-up"></i> ${diff.toFixed(1)}% vs last week`;
        } else if (diff < 0) {
            trendEl.className = 'stat-trend trend-down';
            trendEl.innerHTML = `<i class="fa-solid fa-arrow-down"></i> ${Math.abs(diff).toFixed(1)}% vs last week`;
        } else {
            trendEl.className = 'stat-trend';
            trendEl.textContent = 'Stable vs last week';
        }
    }
}

function renderTrendChart(trendData) {
    const ctx = document.getElementById('attendanceTrendChart').getContext('2d');
    if (trendChart) trendChart.destroy();

    trendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: trendData.map(d => d.date.split('-').slice(1).reverse().join('/')),
            datasets: [{
                label: 'Attendance %',
                data: trendData.map(d => d.pct),
                borderColor: '#0052CC',
                backgroundColor: 'rgba(0, 82, 204, 0.05)',
                fill: true,
                tension: 0.4,
                borderWidth: 3,
                pointRadius: 3,
                pointHoverRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, max: 100, ticks: { callback: v => v + '%' } },
                x: { grid: { display: false } }
            }
        }
    });
}

function renderDistributionChart(trendData) {
    const ctx = document.getElementById('distributionDoughnutChart').getContext('2d');
    if (distributionChart) distributionChart.destroy();

    // Calculate aggregate distribution from recent trend
    const total = trendData.length;
    const fullDays = trendData.filter(d => d.pct >= 95).length;
    const partialDays = trendData.filter(d => d.pct > 50 && d.pct < 95).length;
    const lowDays = total - fullDays - partialDays;

    distributionChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['High (>95%)', 'Medium (50-95%)', 'Low (<50%)'],
            datasets: [{
                data: [fullDays, partialDays, lowDays],
                backgroundColor: ['#00875A', '#FFAB00', '#DE350B'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, padding: 15 } } },
            cutout: '70%'
        }
    });
}

function renderDayOfWeekChart(daysData) {
    const ctx = document.getElementById('dayOfWeekChart').getContext('2d');
    if (dayOfWeekChart) dayOfWeekChart.destroy();

    dayOfWeekChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: daysData.map(d => d.day),
            datasets: [{
                data: daysData.map(d => d.avg_pct),
                backgroundColor: '#0065FF',
                borderRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, max: 100, ticks: { callback: v => v + '%' } },
                x: { grid: { display: false } }
            }
        }
    });
}

function renderSlotChart(slotData) {
    const ctx = document.getElementById('slotComparisonChart').getContext('2d');
    if (slotChart) slotChart.destroy();

    const labels = ['Morn 1', 'Morn 2', 'Aft 1', 'Aft 2'];
    const values = [slotData.morning_1, slotData.morning_2, slotData.afternoon_1, slotData.afternoon_2];

    slotChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: ['#0052CC', '#0065FF', '#00875A', '#36B37E'],
                borderRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true }
            }
        }
    });
}

function renderHeatmap(data) {
    const container = document.getElementById('attendanceHeatmap');
    container.innerHTML = '';

    data.forEach(d => {
        const cell = document.createElement('div');
        cell.className = 'heatmap-cell';

        // Color mapping: 0-100%
        let color = '#f1f5f9'; // Empty
        if (d.pct > 90) color = '#15803d';
        else if (d.pct > 70) color = '#22c55e';
        else if (d.pct > 50) color = '#86efac';
        else if (d.pct > 0) color = '#dcfce7';

        cell.style.backgroundColor = color;
        cell.setAttribute('data-tooltip', `${d.date}: ${d.pct}% (${d.present} students)`);
        container.appendChild(cell);
    });
}

function renderLeaderboard(elementId, list, color) {
    const container = document.getElementById(elementId);
    container.innerHTML = '';

    if (list.length === 0) {
        container.innerHTML = '<p class="text-muted" style="padding: 1rem;">No data available</p>';
        return;
    }

    list.forEach((item, index) => {
        const div = document.createElement('div');
        div.className = 'leader-item';
        div.style.display = 'flex';
        div.style.justifyContent = 'space-between';
        div.style.alignItems = 'center';
        div.style.padding = '0.75rem 1rem';
        div.style.borderRadius = '8px';
        div.style.marginBottom = '0.5rem';
        div.style.border = '1px solid #f1f5f9';

        // Find student ID from global data if possible to enable click
        const student = allStudentsData.find(s => s[0] === item.name);
        if (student) {
            div.onclick = () => showStudentSparkline(student[7] || 0, item.name); // student[7] is 'id'
            div.title = "Click to view trend";
        }

        div.innerHTML = `
            <div style="display: flex; align-items: center; gap: 1rem;">
                <span style="font-weight: 600; color: #94a3b8;">${index + 1}</span>
                <span style="font-weight: 500;">${item.name}</span>
            </div>
            <span style="font-weight: 700; color: ${color === 'green' ? '#00875A' : '#DE350B'}">${item.pct}%</span>
        `;
        container.appendChild(div);
    });
}

function renderAtRiskList(list) {
    const container = document.getElementById('atRiskList');
    container.innerHTML = '';

    if (list.length === 0) {
        container.innerHTML = '<p style="padding: 1rem; color: #64748b; text-align: center;">No high-risk students found</p>';
        return;
    }

    list.slice(0, 5).forEach(item => {
        const div = document.createElement('div');
        div.className = 'leader-item';
        div.style.display = 'flex';
        div.style.justifyContent = 'space-between';
        div.style.alignItems = 'center';
        div.style.padding = '0.75rem 1rem';
        div.style.borderRadius = '8px';
        div.style.marginBottom = '0.5rem';
        div.style.border = '1px solid #fee2e2';
        div.style.backgroundColor = '#fffafb';

        div.innerHTML = `
            <div>
                <div style="font-weight: 600;">${item.name}</div>
                <div style="font-size: 0.75rem; color: #DE350B;">
                    ${item.streak} days streak absence
                </div>
            </div>
            <div style="text-align: right;">
                <div style="font-weight: 700; color: #DE350B;">${item.pct}%</div>
                <div style="font-size: 0.75rem; color: #94a3b8;">30-day avg</div>
            </div>
        `;
        container.appendChild(div);
    });
}

async function showStudentSparkline(id, name) {
    if (!id) return;

    document.getElementById('sparklineStudentName').textContent = `${name}'s Trends`;
    document.getElementById('sparklineModal').style.display = 'block';

    try {
        const res = await fetch(`/api/analytics/student/${id}/sparkline`);
        const data = await res.json();

        if (data.success) {
            renderSparklineChart(data.sparkline);

            const totalSlots = data.sparkline.reduce((acc, curr) => acc + curr.slots, 0);
            const avgSlots = (totalSlots / data.sparkline.length).toFixed(1);
            const fullDays = data.sparkline.filter(d => d.slots === 4).length;

            document.getElementById('sparklineStats').innerHTML = `
                <div class="stat-card" style="padding: 1rem;">
                    <div style="font-size: 1.25rem; font-weight: 700;">${avgSlots}</div>
                    <div style="font-size: 0.75rem; color: #64748b;">Avg Slots/Day</div>
                </div>
                <div class="stat-card" style="padding: 1rem;">
                    <div style="font-size: 1.25rem; font-weight: 700;">${fullDays}</div>
                    <div style="font-size: 0.75rem; color: #64748b;">Full Days (14d)</div>
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading sparkline:', error);
    }
}

function renderSparklineChart(data) {
    const ctx = document.getElementById('studentSparklineChart').getContext('2d');
    if (sparklineChart) sparklineChart.destroy();

    sparklineChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.map(d => d.date.split('-').reverse().join('/')),
            datasets: [{
                label: 'Slots Attended',
                data: data.map(d => d.slots),
                borderColor: '#0065FF',
                backgroundColor: 'rgba(0, 101, 255, 0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true, max: 4, ticks: { stepSize: 1 } },
                x: { grid: { display: false } }
            }
        }
    });
}

function closeSparklineModal() {
    document.getElementById('sparklineModal').style.display = 'none';
}

// Add table sorting
let currentSort = { col: null, asc: true };
function sortTodayTable(colIndex) {
    const isAsc = currentSort.col === colIndex ? !currentSort.asc : true;
    currentSort = { col: colIndex, asc: isAsc };

    filteredStudentsData.sort((a, b) => {
        let valA = a[colIndex] || '';
        let valB = b[colIndex] || '';

        if (typeof valB === 'string') valA = valA.toLowerCase();
        if (typeof valB === 'string') valB = valB.toLowerCase();

        if (valA < valB) return isAsc ? -1 : 1;
        if (valA > valB) return isAsc ? 1 : -1;
        return 0;
    });

    displayTodayAttendance();

    // Update icons
    document.querySelectorAll('.sortable i').forEach(i => i.className = 'fa-solid fa-sort');
    const th = document.querySelectorAll('.attendance-table th')[colIndex];
    if (th) th.querySelector('i').className = `fa-solid fa-sort-${isAsc ? 'up' : 'down'}`;
}

// Update session status every minute
setInterval(updateCurrentSessionStatus, 60000);
