/* Insights — the analytics surface.
 *
 * These functions were the Overview tab of the reports page. Analytics lived
 * in three places (that tab, the teacher portal's Analytics tab, and the
 * dashboard), each rendering overlapping data its own way. This file is the
 * one home; it was moved here verbatim rather than rewritten, so the charts
 * behave exactly as they did.
 */
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
                borderColor: 'var(--color-primary)',
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
                backgroundColor: ['var(--status-ok-solid)', '#FFAB00', 'var(--status-bad-solid)'],
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
                backgroundColor: ['var(--color-primary)', '#0065FF', 'var(--status-ok-solid)', 'var(--status-ok-solid)'],
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
        let color = 'var(--bg-page)'; // Empty
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
        div.style.border = '1px solid var(--bg-page)';

        // Find student ID from global data if possible to enable click
        const student = allStudentsData.find(s => s[0] === item.name);
        if (student) {
            div.onclick = () => showStudentSparkline(student[7] || 0, item.name); // student[7] is 'id'
            div.title = "Click to view trend";
        }

        div.innerHTML = `
            <div style="display: flex; align-items: center; gap: 1rem;">
                <span style="font-weight: 600; color: var(--text-subtle);">${index + 1}</span>
                <span style="font-weight: 500;">${item.name}</span>
            </div>
            <span style="font-weight: 700; color: ${color === 'green' ? 'var(--status-ok-solid)' : 'var(--status-bad-solid)'}">${item.pct}%</span>
        `;
        container.appendChild(div);
    });
}

function renderAtRiskList(list) {
    const container = document.getElementById('atRiskList');
    container.innerHTML = '';

    if (list.length === 0) {
        container.innerHTML = '<p style="padding: 1rem; color: var(--text-secondary); text-align: center;">No high-risk students found</p>';
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
                <div style="font-size: 0.75rem; color: var(--status-bad-solid);">
                    ${item.streak} days streak absence
                </div>
            </div>
            <div style="text-align: right;">
                <div style="font-weight: 700; color: var(--status-bad-solid);">${item.pct}%</div>
                <div style="font-size: 0.75rem; color: var(--text-subtle);">30-day avg</div>
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
                    <div style="font-size: 0.75rem; color: var(--text-secondary);">Avg Slots/Day</div>
                </div>
                <div class="stat-card" style="padding: 1rem;">
                    <div style="font-size: 1.25rem; font-weight: 700;">${fullDays}</div>
                    <div style="font-size: 0.75rem; color: var(--text-secondary);">Full Days (14d)</div>
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

document.addEventListener('DOMContentLoaded', function () {
    ensureAnalyticsBatches().then(loadClassAnalytics);
});
