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
    }
    // 'overview' is gone: its charts moved to /insights, which the tab bar now
    // links to directly. loadClassAnalytics() no longer exists in this file.
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
            <tr class="clickable-row" onclick="location.href='/people?student=${dbId}'" title="Open this student">
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

// The analytics functions that were here now live in static/js/insights.js,
// served at /insights. They were the Overview tab of this page; analytics was
// spread over three pages and this is the one that moved.

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
