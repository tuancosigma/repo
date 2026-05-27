'use strict';

let statsChart = null;
let pieChart = null;
let autoRefreshInterval = null;
let autoRefreshEnabled = false;
let currentChartType = 'line';

const CHART_COLORS = {
    zip: { border: 'rgb(6, 182, 212)', fill: 'rgba(6, 182, 212, 0.2)' },
    decompressed: { border: 'rgb(16, 185, 129)', fill: 'rgba(16, 185, 129, 0.2)' },
    credentials: { border: 'rgb(245, 158, 11)', fill: 'rgba(245, 158, 11, 0.2)' },
    hwid: { border: 'rgb(14, 165, 233)', fill: 'rgba(14, 165, 233, 0.2)' }
};

const PIE_COLORS = [
    'rgba(6, 182, 212, 0.8)',      // cyan
    'rgba(16, 185, 129, 0.8)',     // green
    'rgba(245, 158, 11, 0.8)',     // amber
    'rgba(14, 165, 233, 0.8)',     // blue
    'rgba(168, 85, 247, 0.8)',     // purple
    'rgba(249, 115, 22, 0.8)'      // orange
];

const PIE_BORDERS = [
    'rgb(6, 182, 212)',
    'rgb(16, 185, 129)',
    'rgb(245, 158, 11)',
    'rgb(14, 165, 233)',
    'rgb(168, 85, 247)',
    'rgb(249, 115, 22)'
];

document.addEventListener('DOMContentLoaded', () => {
    // Initialize period and update date range display
    updateDateRangeDisplay();
    loadAllData();
    
    // Period change handler
    const periodSelect = document.getElementById('periodSelect');
    if (periodSelect) {
        periodSelect.addEventListener('change', onPeriodChange);
    }
    
    // Chart type buttons
    ['chartTypeLine', 'chartTypeBar', 'chartTypeArea'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) {
            btn.addEventListener('click', changeChartType);
        }
    });
    
    // Set default active chart type button
    const defaultChartBtn = document.getElementById('chartTypeLine');
    if (defaultChartBtn) {
        defaultChartBtn.classList.add('active');
    }
    
    // Initialize collapse state from localStorage
    const savedState = localStorage.getItem('additionalInfoExpanded');
    if (savedState === 'true') {
        const collapse = document.getElementById('additionalInfo');
        if (collapse) {
            const bsCollapse = new bootstrap.Collapse(collapse, { toggle: false });
            bsCollapse.show();
        }
    }
    
    // Save collapse state
    const collapseElement = document.getElementById('additionalInfo');
    if (collapseElement) {
        collapseElement.addEventListener('shown.bs.collapse', () => {
            localStorage.setItem('additionalInfoExpanded', 'true');
        });
        collapseElement.addEventListener('hidden.bs.collapse', () => {
            localStorage.setItem('additionalInfoExpanded', 'false');
        });
    }
    
    // Initialize auto-refresh
    initAutoRefresh();
    
    // Save refresh interval preference
    const refreshSelect = document.getElementById('refreshInterval');
    if (refreshSelect) {
        refreshSelect.addEventListener('change', () => {
            localStorage.setItem('refreshInterval', refreshSelect.value);
        });
    }
});

/**
 * TIMEZONE POLICY:
 * - All datetime operations use UTC timezone to match server-side (Python datetime.now(timezone.utc))
 * - All date calculations and comparisons are in UTC
 * - All displayed dates show "UTC" suffix for clarity
 * - France is UTC+1 (UTC+2 in summer), but all internal operations use UTC
 * - When displaying dates, use formatDateUTC() helper function
 */

function getPeriodDates(period) {
    /**Calculate start and end dates based on period selection.
    
    All dates are in UTC timezone to match server-side logic.
    France is UTC+1, but all internal operations use UTC.
    */
    // Use UTC time - matches server-side datetime.now(timezone.utc)
    const now = new Date(); // JavaScript Date is always UTC internally
    let start;
    
    switch(period) {
        case 'daily':
            // Last 24 hours: now - 24h to now (UTC)
            start = new Date(now.getTime() - 24 * 60 * 60 * 1000);
            break;
        case 'weekly':
            // Last 7 days: now - 7 days to now (UTC)
            start = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
            break;
        default:
            // Default to weekly (7 days)
            start = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    }
    
    return { start, end: now };
}

function formatDateUTC(date, includeTime = true) {
    /**Format date in UTC timezone for display.
    
    Args:
        date: Date object or ISO string
        includeTime: Whether to include time in output
    
    Returns:
        Formatted date string in UTC
    */
    const d = date instanceof Date ? date : new Date(date);
    const options = {
        timeZone: 'UTC',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        ...(includeTime && {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        })
    };
    return d.toLocaleString('en-US', options) + ' UTC';
}

function formatDateFrance(date, includeTime = true) {
    /**Format date in France timezone (UTC+1) for display.
    
    Args:
        date: Date object or ISO string
        includeTime: Whether to include time in output
    
    Returns:
        Formatted date string in France timezone (Europe/Paris)
    */
    const d = date instanceof Date ? date : new Date(date);
    const options = {
        timeZone: 'Europe/Paris', // France timezone (UTC+1, UTC+2 in summer)
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        ...(includeTime && {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        })
    };
    return d.toLocaleString('en-US', options) + ' (France)';
}

function formatDateForAPI(date) {
    /**Format date for API calls in UTC format (+00:00).
    
    IMPORTANT: All dates sent to API must be in UTC format with explicit timezone
    to match server-side UTC 0 requirement.
    
    Args:
        date: Date object
    
    Returns:
        ISO string with UTC timezone (+00:00), e.g., "2026-03-11T03:40:09.847+00:00"
    */
    // Use toISOString() to get UTC format, then ensure it has +00:00 timezone
    const isoString = date.toISOString();
    // toISOString() returns format like "2026-03-11T03:40:09.847Z"
    // Replace 'Z' with '+00:00' for explicit UTC timezone
    return isoString.replace('Z', '+00:00');
}

function updateDateRangeDisplay() {
    /**Update the date range display based on current period selection.
    
    All dates displayed in UTC to match server-side logic.
    France is UTC+1, but all internal operations use UTC.
    */
    const period = document.getElementById('periodSelect')?.value || 'daily';
    const { start, end } = getPeriodDates(period);
    
    const formatDate = (date) => {
        // Format in UTC timezone to match server
        return date.toLocaleDateString('en-US', {
            timeZone: 'UTC',
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        }) + ' UTC';
    };
    
    const display = document.getElementById('dateRangeDisplay');
    if (display) {
        display.innerHTML = `<small class="text-primary fw-semibold">${formatDate(start)} → ${formatDate(end)}</small>`;
    }
}

function onPeriodChange() {
    /**Handle period selection change - auto apply filters.*/
    updateDateRangeDisplay();
    applyFilters();
}

function updateRefreshInterval() {
    /**Update auto-refresh interval based on selection.*/
    const interval = parseInt(document.getElementById('refreshInterval')?.value || '0');
    
    // Clear existing interval
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
    
    // Update UI
    const statusEl = document.getElementById('autoRefreshStatus');
    const btn = document.getElementById('autoRefreshBtn');
    
    if (interval > 0) {
        autoRefreshEnabled = true;
        if (statusEl) statusEl.textContent = `${interval}s`;
        if (btn) btn.classList.add('active');
        
        // Set new interval
        autoRefreshInterval = setInterval(() => {
            loadAllData();
        }, interval * 1000);
    } else {
        autoRefreshEnabled = false;
        if (statusEl) statusEl.textContent = 'OFF';
        if (btn) btn.classList.remove('active');
    }
}

function showLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) {
        overlay.classList.add('show');
    }
}

function hideLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) {
        overlay.classList.remove('show');
    }
}

function updateLastUpdateTime() {
    // Display in UTC to match server-side timezone
    const now = new Date();
    const utcTime = now.toLocaleTimeString('en-US', { timeZone: 'UTC', hour12: false });
    document.getElementById('lastUpdate').textContent = 
        `Last update: ${utcTime} UTC`;
}

function animateNumber(elementId, targetValue) {
    const element = document.getElementById(elementId);
    const startValue = parseInt(element.textContent.replace(/,/g, '')) || 0;
    const duration = 1000;
    const startTime = performance.now();
    
    const update = (currentTime) => {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const easeOutQuart = 1 - Math.pow(1 - progress, 4);
        const currentValue = Math.floor(startValue + (targetValue - startValue) * easeOutQuart);
        
        element.textContent = currentValue.toLocaleString();
        
        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            element.textContent = targetValue.toLocaleString();
        }
    };
    
    requestAnimationFrame(update);
}

// Debounce function for performance
let filterTimeout = null;
function applyFilters() {
    // Clear previous timeout
    if (filterTimeout) {
        clearTimeout(filterTimeout);
    }
    
    // Show loading immediately for better UX
    showLoading();
    
    // Debounce: wait 300ms after user stops typing/changing filters
    filterTimeout = setTimeout(() => {
        loadAllData();
    }, 300);
}

function loadAllData() {
    // Load stats first (fast) - hide loading as soon as stats ready
    loadStats()
        .then(() => {
            hideLoading();
            updateLastUpdateTime();
            document.getElementById('statsCards').classList.add('fade-in');
            // Load chart in background - don't block UI
            loadChartData().catch(e => console.warn('Chart load failed:', e));
        })
        .catch(error => {
            hideLoading();
            console.error('Error loading data:', error);
            const errorMsg = document.createElement('div');
            errorMsg.className = 'alert alert-warning alert-dismissible fade show';
            errorMsg.innerHTML = `
                <strong>Warning!</strong> Failed to load some data. Please try again.
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            `;
            document.querySelector('.container-fluid').insertBefore(errorMsg, document.querySelector('.container-fluid').firstChild);
        });
}

function loadStats() {
    return fetch(buildApiUrl('/api/stats'))
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.success) {
                animateNumber('zipCount', data.stats.zip_import);
                animateNumber('decompressedCount', data.stats.decompressed);
                animateNumber('credentialsCount', data.stats.credentials);
                animateNumber('hwidCount', data.stats.hwid);
                
                // Organizations stats
                if (data.stats.total_organizations !== undefined) {
                    animateNumber('organizationsCount', data.stats.total_organizations);
                    document.getElementById('organizationsIndexes').textContent = data.stats.organizations_indexes || 0;
                }
                if (data.stats.total_domains !== undefined) {
                    animateNumber('totalDomainsCount', data.stats.total_domains);
                    document.getElementById('uniqueDomainsCount').textContent = (data.stats.unique_domains || 0).toLocaleString();
                }
                
                // Domain occurrences - always try to display, even if empty
                displayDomainOccurrences(data.stats.domain_occurrences || {});
                
                // Dated info - always try to display, even if empty
                displayDated(data.stats.dated || {});
                return data;
            }
            throw new Error(data.error || 'Failed to load stats');
        });
}

function loadChartData() {
    return fetch(buildApiUrl('/api/chart-data'))
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.success) {
                updateChart(data.labels, data.datasets);
                updatePieChart(data.datasets);
                return data;
            }
            throw new Error(data.error || 'Failed to load chart data');
        });
}

function buildApiUrl(endpoint) {
    /**Build API URL with current filter parameters.
    
    IMPORTANT: All dates are sent in UTC format (+00:00) to match server-side UTC 0 requirement.
    */
    const period = document.getElementById('periodSelect')?.value || 'daily';
    const { start, end } = getPeriodDates(period);
    
    let url = `${endpoint}?period=${period}`;
    url += `&start_date=${encodeURIComponent(formatDateForAPI(start))}&end_date=${encodeURIComponent(formatDateForAPI(end))}`;
    return url;
}

function changeChartType() {
    // Get chart type from clicked button
    const buttons = ['chartTypeLine', 'chartTypeBar', 'chartTypeArea'];
    const clickedBtn = event?.target?.closest('button');
    
    if (clickedBtn) {
        // Update active button
        buttons.forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.classList.remove('active');
        });
        clickedBtn.classList.add('active');
        
        // Determine chart type from button
        if (clickedBtn.id === 'chartTypeLine' || clickedBtn.textContent.includes('Line')) {
            currentChartType = 'line';
        } else if (clickedBtn.id === 'chartTypeBar' || clickedBtn.textContent.includes('Bar')) {
            currentChartType = 'bar';
        } else if (clickedBtn.id === 'chartTypeArea' || clickedBtn.textContent.includes('Area')) {
            currentChartType = 'area';
        }
    } else {
        // Fallback: try to get from select (if exists)
        const select = document.getElementById('chartType');
        if (select) {
            currentChartType = select.value;
        }
    }
    
    // Only reload chart data, not all data
    if (statsChart && statsChart.data) {
        // Update existing chart without reloading data
        const chartType = currentChartType === 'area' ? 'line' : currentChartType;
        const fill = currentChartType === 'area'; // Only area chart has fill
        
        statsChart.config.type = chartType;
        statsChart.data.datasets.forEach(dataset => {
            dataset.fill = fill;
            // Update fill mode for area chart
            if (fill) {
                dataset.fill = true;
            } else {
                dataset.fill = false;
            }
        });
        statsChart.update('active');
    } else {
        // Load chart data if chart doesn't exist
        showLoading();
        loadChartData()
            .then(() => hideLoading())
            .catch(error => {
                hideLoading();
                console.error('Error loading chart:', error);
            });
    }
}

function updateChart(labels, datasets) {
    const canvas = document.getElementById('statsChart');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    
    // Destroy existing chart to prevent memory leaks
    if (statsChart) {
        statsChart.destroy();
        statsChart = null;
    }
    
    const chartType = currentChartType === 'area' ? 'line' : currentChartType;
    const fill = currentChartType === 'area'; // Only area chart has fill
    
    statsChart = new Chart(ctx, {
        type: chartType,
        data: {
            labels,
            datasets: [
                createDataset('Zip Archives', datasets.zip_import, CHART_COLORS.zip, fill),
                createDataset('Decompressed', datasets.decompressed, CHART_COLORS.decompressed, fill),
                createDataset('Credentials', datasets.credentials, CHART_COLORS.credentials, fill),
                createDataset('HWID', datasets.hwid, CHART_COLORS.hwid, fill)
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        usePointStyle: true,
                        padding: 16,
                        font: { size: 12, weight: '600' },
                        color: '#cbd5e1'
                    }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    borderColor: 'rgba(6, 182, 212, 0.5)',
                    borderWidth: 1,
                    padding: 14,
                    titleFont: { size: 13, weight: '600' },
                    bodyFont: { size: 12 },
                    displayColors: true,
                    cornerRadius: 8
                }
            },
            scales: {
                x: {
                    grid: { display: false, color: 'rgba(51, 65, 85, 0.1)' },
                    ticks: { maxRotation: 45, minRotation: 0, color: '#94a3b8', font: { size: 11 } }
                },
                y: {
                    beginAtZero: true,
                    ticks: {
                        precision: 0,
                        callback: value => value.toLocaleString(),
                        color: '#94a3b8',
                        font: { size: 11 }
                    },
                    grid: { color: 'rgba(51, 65, 85, 0.15)' }
                }
            },
            animation: {
                duration: 1000,
                easing: 'easeInOutQuart'
            },
            interaction: {
                intersect: false,
                mode: 'index'
            },
            elements: {
                point: {
                    radius: fill ? 3 : 4, // Smaller points for area chart
                    hoverRadius: fill ? 5 : 6,
                    hoverBorderWidth: 2,
                    borderWidth: 2
                },
                line: {
                    borderWidth: 2.5,
                    tension: 0.4
                }
            }
        }
    });
}

function createDataset(label, data, colors, fill) {
    return {
        label,
        data,
        borderColor: colors.border,
        backgroundColor: fill ? colors.fill : 'transparent',
        tension: 0.4,
        fill: fill ? true : false, // Explicitly set fill for area chart
        borderWidth: 2.5,
        pointRadius: fill ? 3 : 4, // Smaller points for area chart
        pointHoverRadius: fill ? 5 : 6
    };
}

function updatePieChart(datasets) {
    const canvas = document.getElementById('pieChart');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    const totals = {
        zip: datasets.zip_import.reduce((a, b) => a + b, 0),
        decompressed: datasets.decompressed.reduce((a, b) => a + b, 0),
        credentials: datasets.credentials.reduce((a, b) => a + b, 0),
        hwid: datasets.hwid.reduce((a, b) => a + b, 0)
    };
    
    // Destroy existing chart to prevent memory leaks
    if (pieChart) {
        pieChart.destroy();
        pieChart = null;
    }
    
    pieChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Zip Archives', 'Decompressed', 'Credentials', 'HWID'],
            datasets: [{
                data: [totals.zip, totals.decompressed, totals.credentials, totals.hwid],
                backgroundColor: PIE_COLORS,
                borderColor: PIE_BORDERS,
                borderWidth: 2.5,
                hoverBorderWidth: 3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        usePointStyle: true,
                        padding: 14,
                        font: { size: 11, weight: '600' },
                        color: '#cbd5e1'
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    borderColor: 'rgba(6, 182, 212, 0.5)',
                    borderWidth: 1,
                    padding: 12,
                    titleFont: { size: 12, weight: '600' },
                    bodyFont: { size: 11 },
                    cornerRadius: 8,
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = context.parsed || 0;
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                            return `${label}: ${value.toLocaleString()} (${percentage}%)`;
                        }
                    }
                }
            },
            animation: {
                animateRotate: true,
                animateScale: false,
                duration: 1200,
                easing: 'easeInOutQuart'
            },
            elements: {
                arc: {
                    borderWidth: 2.5,
                    hoverBorderWidth: 3
                }
            }
        }
    });
}

function toggleAutoRefresh() {
    /**Toggle auto-refresh on/off manually.*/
    const refreshSelect = document.getElementById('refreshInterval');
    if (refreshSelect) {
        if (autoRefreshEnabled) {
            refreshSelect.value = '0';
        } else {
            refreshSelect.value = '60'; // Default to 1 minute
        }
        updateRefreshInterval();
    }
}

function initAutoRefresh() {
    /**Initialize auto-refresh from saved preference or default.*/
    const savedInterval = localStorage.getItem('refreshInterval') || '60';
    const refreshSelect = document.getElementById('refreshInterval');
    if (refreshSelect) {
        refreshSelect.value = savedInterval;
        updateRefreshInterval();
    }
}


function exportPDF() {
    const btn = event?.target?.closest('button') || document.querySelector('button[onclick="exportPDF()"]');
    const originalHTML = btn?.innerHTML;
    
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Generating...';
    }
    
    const url = buildApiUrl('/api/export-pdf');
    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    // Reset button after delay
    setTimeout(() => {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHTML || '<i class="bi bi-file-earmark-pdf"></i> PDF';
        }
    }, 2000);
}

function exportCSV() {
    const btn = event?.target?.closest('button') || document.querySelector('button[onclick="exportCSV()"]');
    const originalHTML = btn?.innerHTML;
    
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Exporting...';
    }
    
    const url = buildApiUrl('/api/export-csv');
    const link = document.createElement('a');
    link.href = url;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    
    // Wait for download to start, then reset button
    setTimeout(() => {
        document.body.removeChild(link);
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHTML || '<i class="bi bi-filetype-csv"></i> CSV';
        }
    }, 1500);
}

function displayDomainOccurrences(occurrences) {
    const container = document.getElementById('domainOccurrences');
    
    if (!container) {
        console.warn('domainOccurrences container not found');
        return;
    }
    
    if (!occurrences || Object.keys(occurrences).length === 0) {
        container.innerHTML = '<div class="col-12 text-center"><small style="color: var(--text-muted) !important;">No domains found</small></div>';
        return;
    }
    
    // Sort by count (descending)
    const sorted = Object.entries(occurrences).sort((a, b) => b[1] - a[1]);
    
    let html = '';
    sorted.forEach(([domain, count]) => {
        // Escape HTML to prevent XSS
        const safeDomain = String(domain || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        const badgeClass = count > 1 ? 'bg-warning text-dark' : 'bg-secondary';
        html += `
            <div class="col-md-3 col-sm-4 col-6">
                <div class="d-flex justify-content-between align-items-center p-2 border rounded" style="border-color: var(--border) !important; background-color: var(--bg-secondary);">
                    <span class="text-truncate" style="max-width: 70%; color: var(--text-primary) !important;" title="${safeDomain}">${safeDomain}</span>
                    <span class="badge ${badgeClass} ms-2">${count.toLocaleString()}</span>
                </div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

function displayDated(dated) {
    const container = document.getElementById('datedInfo');
    
    if (!container) {
        console.warn('datedInfo container not found');
        return;
    }
    
    if (!dated || Object.keys(dated).length === 0) {
        container.innerHTML = '<div class="text-center"><small style="color: var(--text-muted) !important;">No data found. Please check source collections.</small></div>';
        return;
    }
    
    let html = '';
    const collections = {
        'archives': 'Archives',
        'credentials': 'Credentials',
        'alerts': 'Alerts',
        'organizations': 'Organizations'
    };
    
    Object.entries(dated).forEach(([key, info]) => {
        const collectionName = collections[key] || key;
        html += `<div class="mb-2 p-2 border rounded" style="border-color: var(--border) !important;">`;
        html += `<small class="d-block mb-1" style="color: var(--text-secondary) !important;"><strong style="color: var(--text-primary) !important;">${collectionName}</strong></small>`;
        html += `<small class="d-block mb-2" style="color: var(--text-secondary) !important;">Field: <code>${info.field || 'N/A'}</code></small>`;
        
        if (key === 'organizations') {
            if (info.oldest_created && info.newest_created) {
                const oldest = formatDateUTC(info.oldest_created);
                const newest = formatDateUTC(info.newest_created);
                html += `<small class="d-block mb-1" style="color: var(--text-secondary) !important;"><strong style="color: var(--text-primary) !important;">Created:</strong> ${oldest} - ${newest}</small>`;
            }
            if (info.oldest_updated && info.newest_updated) {
                const oldest = formatDateUTC(info.oldest_updated);
                const newest = formatDateUTC(info.newest_updated);
                html += `<small class="d-block" style="color: var(--text-secondary) !important;"><strong style="color: var(--text-primary) !important;">Updated:</strong> ${oldest} - ${newest}</small>`;
            }
        } else {
            if (info.oldest && info.newest) {
                const oldest = formatDateUTC(info.oldest);
                const newest = formatDateUTC(info.newest);
                html += `<small class="d-block" style="color: var(--text-secondary) !important;"><strong style="color: var(--text-primary) !important;">Range:</strong> ${oldest} - ${newest}</small>`;
            }
        }
        
        html += `</div>`;
    });
    
    if (!html) {
        html = '<div class="text-center"><small style="color: var(--text-muted) !important;">No data found</small></div>';
    }
    
    container.innerHTML = html;
}

// Search & Reports Functions
function loadDailyReport() {
    showLoading();
    fetch('/api/report/daily')
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                displayReportResults('Daily Report', data);
            } else {
                alert('Error loading daily report: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            alert('Error loading daily report: ' + error.message);
        });
}

function loadWeeklyReport() {
    showLoading();
    fetch('/api/report/weekly')
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                displayReportResults('Weekly Report', data);
            } else {
                alert('Error loading weekly report: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            alert('Error loading weekly report: ' + error.message);
        });
}

// Store current report/search data for export
let currentReportData = null;
let currentSearchData = null;
let currentSearchType = null;

function displayReportResults(title, data) {
    currentReportData = data;
    currentSearchType = 'report';
    
    const exportButtons = `
        <div class="btn-group btn-group-sm mb-3" role="group">
            <button type="button" class="btn btn-outline-primary" onclick="exportCurrentData('pdf')" title="Export as PDF">
                <i class="bi bi-file-earmark-pdf"></i> PDF
            </button>
            <button type="button" class="btn btn-outline-success" onclick="exportCurrentData('csv')" title="Export as CSV">
                <i class="bi bi-filetype-csv"></i> CSV
            </button>
            <button type="button" class="btn btn-outline-info" onclick="exportCurrentData('json')" title="Export as JSON">
                <i class="bi bi-filetype-json"></i> JSON
            </button>
            <button type="button" class="btn btn-outline-secondary" onclick="copyCurrentData()" title="Copy to clipboard">
                <i class="bi bi-clipboard"></i> Copy
            </button>
            <button type="button" class="btn btn-outline-dark" onclick="printCurrentData()" title="Print">
                <i class="bi bi-printer"></i> Print
            </button>
        </div>
    `;
    
    const modal = createModal(title, `
        ${exportButtons}
        <div class="mb-3">
            <strong>Period:</strong> ${data.period}<br>
            <strong>Start:</strong> ${formatDateUTC(data.start)}<br>
            <strong>End:</strong> ${formatDateUTC(data.end)}
        </div>
        <div class="table-responsive">
            <table class="table table-sm table-bordered table-hover" id="reportTable">
                <thead>
                    <tr><th>Metric</th><th>Count</th></tr>
                </thead>
                <tbody>
                    <tr><td>Zip Archives Imported</td><td>${(data.stats.zip_import || 0).toLocaleString()}</td></tr>
                    <tr><td>Decompressed Archives</td><td>${(data.stats.decompressed || 0).toLocaleString()}</td></tr>
                    <tr><td>Credentials Found</td><td>${(data.stats.credentials || 0).toLocaleString()}</td></tr>
                    ${data.stats.credential_types ? Object.entries(data.stats.credential_types).map(([type, count]) => `
                        <tr class="table-light"><td style="padding-left: 20px;"><i class="bi bi-arrow-return-right text-muted me-1"></i> ${type === 'telegram' ? 'Telegram' : type === 'telegram_ulp' ? 'Telegram ULP' : type} (source.type)</td><td>${count.toLocaleString()}</td></tr>
                    `).join('') : ''}
                    <tr><td>HWID Found</td><td>${(data.stats.hwid || 0).toLocaleString()}</td></tr>
                    <tr><td>Total Organizations</td><td>${(data.stats.total_organizations || 0).toLocaleString()}</td></tr>
                    <tr><td>Total Domains</td><td>${(data.stats.total_domains || 0).toLocaleString()}</td></tr>
                    <tr><td>Unique Domains</td><td>${(data.stats.unique_domains || 0).toLocaleString()}</td></tr>
                </tbody>
            </table>
        </div>
    `);
    document.body.appendChild(modal);
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
    modal.addEventListener('hidden.bs.modal', () => {
        modal.remove();
        currentReportData = null;
        currentSearchType = null;
    });
}

function searchDomain() {
    const query = document.getElementById('domainSearchInput').value.trim();
    if (!query) {
        alert('Please enter a domain to search');
        return;
    }
    
    showLoading();
    fetch(`/api/search/domain?q=${encodeURIComponent(query)}&limit=50`)
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                displayDomainSearchResults(data);
            } else {
                alert('Error searching domain: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            alert('Error searching domain: ' + error.message);
        });
}

function displayDomainSearchResults(data) {
    currentSearchData = data;
    currentSearchType = 'domain_search';
    
    const resultsDiv = document.getElementById('domainSearchResults');
    if (data.total_organizations === 0) {
        resultsDiv.innerHTML = `<div class="alert alert-info">No organizations found containing "${data.query}"</div>`;
        return;
    }
    
    const exportButtons = `
        <div class="btn-group btn-group-sm mb-3" role="group">
            <button type="button" class="btn btn-outline-success" onclick="exportCurrentData('csv')" title="Export as CSV">
                <i class="bi bi-filetype-csv"></i> CSV
            </button>
            <button type="button" class="btn btn-outline-info" onclick="exportCurrentData('json')" title="Export as JSON">
                <i class="bi bi-filetype-json"></i> JSON
            </button>
            <button type="button" class="btn btn-outline-secondary" onclick="copyCurrentData()" title="Copy to clipboard">
                <i class="bi bi-clipboard"></i> Copy
            </button>
        </div>
    `;
    
    const searchBox = `
        <div class="mb-2">
            <input type="text" class="form-control form-control-sm" id="domainSearchFilter" 
                   placeholder="Filter results..." onkeyup="filterTable('domainSearchTable', this.value)">
        </div>
    `;
    
    let html = `${exportButtons}${searchBox}`;
    html += `<div class="alert alert-success">Found ${data.total_organizations} organization(s) containing "${highlightText(data.query)}"</div>`;
    html += '<div class="table-responsive"><table class="table table-sm table-bordered table-hover" id="domainSearchTable">';
    html += '<thead><tr><th>Organization Index</th><th>Display Name</th><th>Matching Domains</th><th>Total Domains</th><th>Actions</th></tr></thead><tbody>';
    
    data.results.forEach((org, index) => {
        const matchingDomains = org.matching_domains.map(d => 
            `<span class="badge bg-primary me-1">${highlightText(d, data.query)}</span>`
        ).join('');
        html += `<tr data-index="${index}">
            <td><code>${highlightText(org.organization_index, data.query)}</code></td>
            <td>${highlightText(org.display_name, data.query)}</td>
            <td>${matchingDomains || '<span style="color: var(--text-muted);">None</span>'}</td>
            <td>${org.total_domains}</td>
            <td>
                <button class="btn btn-sm btn-outline-primary" onclick="viewDomainDetails(${index})" title="View Details">
                    <i class="bi bi-eye"></i>
                </button>
            </td>
        </tr>`;
    });
    
    html += '</tbody></table></div>';
    resultsDiv.innerHTML = html;
    
    // Store data for detail view
    window.domainSearchData = data;
}

function searchOrganization() {
    const query = document.getElementById('orgSearchInput').value.trim();
    if (!query) {
        alert('Please enter an organization index or name to search');
        return;
    }
    
    showLoading();
    fetch(`/api/search/organization?q=${encodeURIComponent(query)}&limit=50`)
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                displayOrgSearchResults(data);
            } else {
                alert('Error searching organization: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            alert('Error searching organization: ' + error.message);
        });
}

function displayOrgSearchResults(data) {
    currentSearchData = data;
    currentSearchType = 'org_search';
    
    const resultsDiv = document.getElementById('orgSearchResults');
    if (data.total_found === 0) {
        resultsDiv.innerHTML = `<div class="alert alert-info">No organizations found matching "${data.query}"</div>`;
        return;
    }
    
    const exportButtons = `
        <div class="btn-group btn-group-sm mb-3" role="group">
            <button type="button" class="btn btn-outline-success" onclick="exportCurrentData('csv')" title="Export as CSV">
                <i class="bi bi-filetype-csv"></i> CSV
            </button>
            <button type="button" class="btn btn-outline-info" onclick="exportCurrentData('json')" title="Export as JSON">
                <i class="bi bi-filetype-json"></i> JSON
            </button>
            <button type="button" class="btn btn-outline-secondary" onclick="copyCurrentData()" title="Copy to clipboard">
                <i class="bi bi-clipboard"></i> Copy
            </button>
        </div>
    `;
    
    const searchBox = `
        <div class="mb-2">
            <input type="text" class="form-control form-control-sm" id="orgSearchFilter" 
                   placeholder="Filter results..." onkeyup="filterTable('orgSearchTable', this.value)">
        </div>
    `;
    
    let html = `${exportButtons}${searchBox}`;
    html += `<div class="alert alert-success">Found ${data.total_found} organization(s) matching "${highlightText(data.query)}"</div>`;
    html += '<div class="table-responsive"><table class="table table-sm table-bordered table-hover" id="orgSearchTable">';
    html += '<thead><tr><th>Organization Index</th><th>Display Name</th><th>Domains</th><th>Total Domains</th><th>Actions</th></tr></thead><tbody>';
    
    data.results.forEach((org, index) => {
        const domainsList = org.domains && org.domains.length > 0 
            ? org.domains.map(d => `<span class="badge bg-secondary me-1">${d}</span>`).join('')
            : '<span style="color: var(--text-muted) !important;">No domains</span>';
        html += `<tr data-index="${index}">
            <td><code>${highlightText(org.organization_index, data.query)}</code></td>
            <td>${highlightText(org.display_name, data.query)}</td>
            <td>${domainsList}</td>
            <td>${org.total_domains}</td>
            <td>
                <button class="btn btn-sm btn-outline-primary" onclick="viewOrgDetails(${index})" title="View Details">
                    <i class="bi bi-eye"></i>
                </button>
            </td>
        </tr>`;
    });
    
    html += '</tbody></table></div>';
    resultsDiv.innerHTML = html;
    
    // Store data for detail view
    window.orgSearchData = data;
}

function viewDomainIndexes() {
    const domain = document.getElementById('domainIndexInput').value.trim();
    if (!domain) {
        alert('Please enter a domain to view indexes');
        return;
    }
    
    showLoading();
    fetch(`/api/domain/indexes?domain=${encodeURIComponent(domain)}`)
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                displayDomainIndexes(data);
            } else {
                alert('Error viewing domain indexes: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            alert('Error viewing domain indexes: ' + error.message);
        });
}

function displayDomainIndexes(data) {
    const resultsDiv = document.getElementById('domainIndexResults');
    if (data.total_organizations === 0) {
        resultsDiv.innerHTML = `<div class="alert alert-info">Domain "${data.domain}" not found in any organization</div>`;
        return;
    }
    
    let html = `<div class="alert alert-success">
        Domain "<strong>${data.domain}</strong>" found in ${data.total_organizations} organization(s)
    </div>`;
    html += `<div class="mb-2"><strong>Organization Indexes:</strong> `;
    html += data.organization_indexes.map(idx => `<code class="me-2">${idx}</code>`).join('');
    html += '</div>';
    
    html += '<div class="table-responsive"><table class="table table-sm table-bordered table-hover">';
    html += '<thead><tr><th>Organization Index</th><th>Display Name</th><th>All Domains</th></tr></thead><tbody>';
    
    data.results.forEach(org => {
            const domainsList = org.all_domains && org.all_domains.length > 0 
            ? org.all_domains.map(d => {
                const badgeClass = d === data.domain ? 'bg-success' : 'bg-secondary';
                return `<span class="badge ${badgeClass} me-1">${d}</span>`;
            }).join('')
            : '<span style="color: var(--text-muted) !important;">No domains</span>';
        html += `<tr>
            <td><code>${org.organization_index}</code></td>
            <td>${org.display_name}</td>
            <td>${domainsList}</td>
        </tr>`;
    });
    
    html += '</tbody></table></div>';
    resultsDiv.innerHTML = html;
}

function createModal(title, content) {
    const modal = document.createElement('div');
    modal.className = 'modal fade';
    modal.innerHTML = `
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">${title}</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">${content}</div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    `;
    return modal;
}

function countArchivesDomains() {
    const domainPattern = document.getElementById('archivesDomainCountInput').value.trim();
    const period = document.getElementById('archivesDomainCountPeriod')?.value || 'weekly';
    const resultsDiv = document.getElementById('archivesDomainCountResults');
    
    showLoading();
    
    // Build URL with period and domain pattern
    const { start, end } = getPeriodDates(period);
    
    let url = `/api/archives/domains/count?limit=100&period=${period}`;
    url += `&start_date=${encodeURIComponent(formatDateForAPI(start))}&end_date=${encodeURIComponent(formatDateForAPI(end))}`;
    if (domainPattern) {
        url += `&domain=${encodeURIComponent(domainPattern)}`;
    }
    
    fetch(url)
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                displayArchivesDomainCountResults(data);
            } else {
                resultsDiv.innerHTML = `<div class="alert alert-warning">Error: ${data.error || 'Unknown error'}</div>`;
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            resultsDiv.innerHTML = `<div class="alert alert-danger">Error: ${error.message || 'Failed to fetch archives domain count'}</div>`;
        });
}

function displayArchivesDomainCountResults(data) {
    const resultsDiv = document.getElementById('archivesDomainCountResults');
    
    if (data.message && data.message.includes('does not have domains field')) {
        resultsDiv.innerHTML = `<div class="alert alert-info">
            ${data.message}<br>
            <small style="color: var(--text-muted);">Collection: ${data.collection}, Date Field: ${data.date_field}</small>
        </div>`;
        return;
    }
    
    if (!data.results || data.results.length === 0) {
        const dateRange = data.start_date && data.end_date 
            ? `${formatDateUTC(data.start_date)} - ${formatDateUTC(data.end_date)}`
            : '';
        resultsDiv.innerHTML = `<div class="alert alert-info">
            No domains found${data.domain_pattern ? ` matching "${data.domain_pattern}"` : ''}
            ${dateRange ? `<br><small style="color: var(--text-muted);">Period: ${dateRange}</small>` : ''}
            <br><small style="color: var(--text-muted);">Collection: ${data.collection}, Date Field: ${data.date_field}</small>
        </div>`;
        return;
    }
    
    const dateRange = data.start_date && data.end_date 
        ? `${formatDateUTC(data.start_date)} - ${formatDateUTC(data.end_date)}`
        : '';
    
    let html = `<div class="alert alert-success">
        <strong style="color: var(--text-primary);">Collection:</strong> <span style="color: var(--text-primary);">${data.collection}</span><br>
        <strong style="color: var(--text-primary);">Date Field:</strong> <span style="color: var(--text-primary);">${data.date_field}</span><br>
        <strong style="color: var(--text-primary);">Total Occurrences:</strong> <span style="color: var(--text-primary);">${data.total_occurrences.toLocaleString()}</span><br>
        <strong style="color: var(--text-primary);">Unique Domains:</strong> <span style="color: var(--text-primary);">${data.unique_domains.toLocaleString()}</span><br>
        <strong style="color: var(--text-primary);">Returned:</strong> <span style="color: var(--text-primary);">${data.total_returned.toLocaleString()}</span>
        ${data.domain_pattern ? `<br><strong style="color: var(--text-primary);">Pattern:</strong> <span style="color: var(--text-primary);">${data.domain_pattern}</span>` : ''}
        ${dateRange ? `<br><small style="color: var(--text-muted);">Period: ${dateRange}</small>` : ''}
    </div>`;
    
    html += '<div class="table-responsive"><table class="table table-sm table-bordered table-hover">';
    html += '<thead><tr><th>#</th><th>Domain</th><th>Count</th><th>Sample Document IDs</th></tr></thead><tbody>';
    
    data.results.forEach((item, index) => {
        const sampleIds = item.sample_doc_ids ? item.sample_doc_ids.slice(0, 3).map(id => `<code>${String(id).substring(0, 8)}...</code>`).join(', ') : 'N/A';
        html += `<tr>
            <td>${index + 1}</td>
            <td><strong style="color: var(--text-primary);">${item.domain}</strong></td>
            <td><span class="badge bg-primary">${item.count.toLocaleString()}</span></td>
            <td style="color: var(--text-secondary); font-size: 0.85rem;">${sampleIds}</td>
        </tr>`;
    });
    
    html += '</tbody></table></div>';
    resultsDiv.innerHTML = html;
}

function countAlertsDomains() {
    const domainPattern = document.getElementById('alertsDomainCountInput').value.trim();
    const period = document.getElementById('alertsDomainCountPeriod')?.value || 'weekly';
    const resultsDiv = document.getElementById('alertsDomainCountResults');
    
    showLoading();
    
    // Build URL with period and domain pattern
    const { start, end } = getPeriodDates(period);
    
    let url = `/api/alerts/domains/count?limit=100&period=${period}`;
    url += `&start_date=${encodeURIComponent(formatDateForAPI(start))}&end_date=${encodeURIComponent(formatDateForAPI(end))}`;
    if (domainPattern) {
        url += `&domain=${encodeURIComponent(domainPattern)}`;
    }
    
    fetch(url)
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                displayAlertsDomainCountResults(data);
            } else {
                resultsDiv.innerHTML = `<div class="alert alert-warning">Error: ${data.error || 'Unknown error'}</div>`;
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            resultsDiv.innerHTML = `<div class="alert alert-danger">Error: ${error.message || 'Failed to fetch alerts domain count'}</div>`;
        });
}

function displayAlertsDomainCountResults(data) {
    const resultsDiv = document.getElementById('alertsDomainCountResults');
    
    if (!data.results || data.results.length === 0) {
        const dateRange = data.start_date && data.end_date 
            ? `${formatDateUTC(data.start_date)} - ${formatDateUTC(data.end_date)}`
            : '';
        resultsDiv.innerHTML = `<div class="alert alert-info">
            No domains found${data.domain_pattern ? ` matching "${data.domain_pattern}"` : ''}
            ${dateRange ? `<br><small style="color: var(--text-muted);">Period: ${dateRange}</small>` : ''}
            <br><small style="color: var(--text-muted);">Collection: ${data.collection || 'alerts'}, Date Field: ${data.date_field || 'updated_date'}</small>
        </div>`;
        return;
    }
    
    const dateRange = data.start_date && data.end_date 
        ? `${formatDateUTC(data.start_date)} - ${formatDateUTC(data.end_date)}`
        : '';
    
    let html = `<div class="alert alert-success">
        <strong style="color: var(--text-primary);">Collection:</strong> <span style="color: var(--text-primary);">${data.collection || 'alerts'}</span><br>
        <strong style="color: var(--text-primary);">Date Field:</strong> <span style="color: var(--text-primary);">${data.date_field || 'updated_date'}</span><br>
        <strong style="color: var(--text-primary);">Total Occurrences:</strong> <span style="color: var(--text-primary);">${data.total_occurrences.toLocaleString()}</span><br>
        <strong style="color: var(--text-primary);">Unique Domains:</strong> <span style="color: var(--text-primary);">${data.unique_domains.toLocaleString()}</span><br>
        <strong style="color: var(--text-primary);">Returned:</strong> <span style="color: var(--text-primary);">${data.total_returned.toLocaleString()}</span>
        ${data.domain_pattern ? `<br><strong style="color: var(--text-primary);">Pattern:</strong> <span style="color: var(--text-primary);">${data.domain_pattern}</span>` : ''}
        ${dateRange ? `<br><small style="color: var(--text-muted);">Period: ${dateRange}</small>` : ''}
    </div>`;
    
    html += '<div class="table-responsive"><table class="table table-sm table-bordered table-hover">';
    html += '<thead><tr><th>#</th><th>Domain</th><th>Count</th><th>Sample Document IDs</th></tr></thead><tbody>';
    
    data.results.forEach((item, index) => {
        const sampleIds = item.sample_doc_ids ? item.sample_doc_ids.slice(0, 3).map(id => `<code>${String(id).substring(0, 8)}...</code>`).join(', ') : 'N/A';
        html += `<tr>
            <td>${index + 1}</td>
            <td><strong style="color: var(--text-primary);">${item.domain}</strong></td>
            <td><span class="badge bg-primary">${item.count.toLocaleString()}</span></td>
            <td style="color: var(--text-secondary); font-size: 0.85rem;">${sampleIds}</td>
        </tr>`;
    });
    
    html += '</tbody></table></div>';
    resultsDiv.innerHTML = html;
}

function searchAlertsDomain() {
    const domainQuery = document.getElementById('alertsDomainSearchInput').value.trim();
    const period = document.getElementById('alertsDomainSearchPeriod')?.value || 'weekly';
    const resultsDiv = document.getElementById('alertsDomainSearchResults');
    
    if (!domainQuery) {
        resultsDiv.innerHTML = '<div class="alert alert-warning">Please enter a domain pattern to search</div>';
        return;
    }
    
    showLoading();
    
    // Build URL with period
    const { start, end } = getPeriodDates(period);
    
    let url = `/api/alerts/domains/search?domain=${encodeURIComponent(domainQuery)}&limit=50&period=${period}`;
    url += `&start_date=${encodeURIComponent(formatDateForAPI(start))}&end_date=${encodeURIComponent(formatDateForAPI(end))}`;
    
    fetch(url)
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                displayAlertsDomainSearchResults(data);
            } else {
                resultsDiv.innerHTML = `<div class="alert alert-warning">Error: ${data.error || 'Unknown error'}</div>`;
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            resultsDiv.innerHTML = `<div class="alert alert-danger">Error: ${error.message || 'Failed to search alerts domain'}</div>`;
        });
}

function displayAlertsDomainSearchResults(data) {
    currentSearchData = data;
    currentSearchType = 'alerts_domain_search';
    
    const resultsDiv = document.getElementById('alertsDomainSearchResults');
    
    if (data.total_matching_alerts === 0) {
        const dateRange = data.start_date && data.end_date 
            ? `${formatDateUTC(data.start_date)} - ${formatDateUTC(data.end_date)}`
            : '';
        resultsDiv.innerHTML = `<div class="alert alert-info">
            No alerts found containing "${highlightText(data.query)}"
            ${dateRange ? `<br><small style="color: var(--text-muted);">Period: ${dateRange}</small>` : ''}
        </div>`;
        return;
    }
    
    const dateRange = data.start_date && data.end_date 
        ? `${formatDateUTC(data.start_date)} - ${formatDateUTC(data.end_date)}`
        : '';
    
    const exportButtons = `
        <div class="btn-group btn-group-sm mb-3" role="group">
            <button type="button" class="btn btn-outline-success" onclick="exportCurrentData('csv')" title="Export as CSV">
                <i class="bi bi-filetype-csv"></i> CSV
            </button>
            <button type="button" class="btn btn-outline-info" onclick="exportCurrentData('json')" title="Export as JSON">
                <i class="bi bi-filetype-json"></i> JSON
            </button>
            <button type="button" class="btn btn-outline-secondary" onclick="copyCurrentData()" title="Copy to clipboard">
                <i class="bi bi-clipboard"></i> Copy
            </button>
        </div>
    `;
    
    const searchBox = `
        <div class="mb-2">
            <input type="text" class="form-control form-control-sm" id="alertsDomainSearchFilter" 
                   placeholder="Filter results..." onkeyup="filterTable('alertsDomainSearchTable', this.value)">
        </div>
    `;
    
    let html = `${exportButtons}${searchBox}`;
    html += `<div class="alert alert-success">
        Found <strong style="color: var(--text-primary);">${data.total_matching_alerts.toLocaleString()}</strong> alert(s) containing "${highlightText(data.query)}"<br>
        Showing <strong style="color: var(--text-primary);">${data.returned_count}</strong> result(s)
        ${dateRange ? `<br><small style="color: var(--text-muted);">Period: ${dateRange}</small>` : ''}
    </div>`;
    
    html += '<div class="table-responsive"><table class="table table-sm table-bordered table-hover" id="alertsDomainSearchTable">';
    html += '<thead><tr><th>Alert ID</th><th>Matching Domains</th><th>All Domains</th><th>Organization ID</th><th>Type</th><th>Created Date</th><th>Actions</th></tr></thead><tbody>';
    
    data.results.forEach((alert, index) => {
        const matchingBadges = alert.matching_domains.map(d => 
            `<span class="badge bg-success me-1">${highlightText(d, data.query)}</span>`
        ).join('');
        const allBadges = alert.all_domains.map(d => {
            const isMatching = alert.matching_domains.includes(d);
            const badgeClass = isMatching ? 'bg-success' : 'bg-secondary';
            return `<span class="badge ${badgeClass} me-1">${highlightText(d, data.query)}</span>`;
        }).join('');
        
        html += `<tr data-index="${index}">
            <td><code style="color: var(--primary);">${alert.alert_id.substring(0, 12)}...</code></td>
            <td>${matchingBadges || '<span style="color: var(--text-muted);">None</span>'}</td>
            <td>${allBadges || '<span style="color: var(--text-muted);">None</span>'}</td>
            <td>${alert.organization_id || '<span style="color: var(--text-muted);">N/A</span>'}</td>
            <td><span class="badge bg-info">${alert.type || 'N/A'}</span></td>
            <td style="color: var(--text-secondary); font-size: 0.85rem;">${alert.created_date ? formatDateUTC(alert.created_date) : 'N/A'}</td>
            <td>
                <button class="btn btn-sm btn-outline-primary" onclick="viewAlertDetails(${index})" title="View Details">
                    <i class="bi bi-eye"></i>
                </button>
            </td>
        </tr>`;
    });
    
    html += '</tbody></table></div>';
    resultsDiv.innerHTML = html;
    
    // Store data for detail view
    window.alertsDomainSearchData = data;
}

function searchHWID() {
    const hwidPattern = document.getElementById('hwidSearchInput').value.trim();
    const period = document.getElementById('hwidSearchPeriod')?.value || 'weekly';
    const resultsDiv = document.getElementById('hwidSearchResults');
    
    showLoading();
    
    // Build URL with period
    const { start, end } = getPeriodDates(period);
    
    let url = `/api/hwid/list?limit=100&period=${period}`;
    url += `&start_date=${encodeURIComponent(formatDateForAPI(start))}&end_date=${encodeURIComponent(formatDateForAPI(end))}`;
    if (hwidPattern) {
        url += `&hwid_pattern=${encodeURIComponent(hwidPattern)}`;
    }
    
    fetch(url)
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                displayHWIDSearchResults(data);
            } else {
                resultsDiv.innerHTML = `<div class="alert alert-warning">Error: ${data.error || 'Unknown error'}</div>`;
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            resultsDiv.innerHTML = `<div class="alert alert-danger">Error: ${error.message || 'Failed to fetch HWID data'}</div>`;
        });
}

function displayHWIDSearchResults(data) {
    currentSearchData = data;
    currentSearchType = 'hwid_search';
    
    const resultsDiv = document.getElementById('hwidSearchResults');
    
    const dateRange = data.start_date && data.end_date 
        ? `${formatDateUTC(data.start_date)} - ${formatDateUTC(data.end_date)}`
        : '';
    
    if (data.total_unique_hwids === 0) {
        resultsDiv.innerHTML = `<div class="alert alert-info">
            No HWIDs found
            ${data.hwid_pattern ? ` matching pattern "${data.hwid_pattern}"` : ''}
            ${dateRange ? `<br><small style="color: var(--text-muted);">Period: ${dateRange}</small>` : ''}
        </div>`;
        return;
    }
    
    const exportButtons = `
        <div class="btn-group btn-group-sm mb-3" role="group">
            <button type="button" class="btn btn-outline-success" onclick="exportCurrentData('csv')" title="Export as CSV">
                <i class="bi bi-filetype-csv"></i> CSV
            </button>
            <button type="button" class="btn btn-outline-info" onclick="exportCurrentData('json')" title="Export as JSON">
                <i class="bi bi-filetype-json"></i> JSON
            </button>
            <button type="button" class="btn btn-outline-secondary" onclick="copyCurrentData()" title="Copy to clipboard">
                <i class="bi bi-clipboard"></i> Copy
            </button>
        </div>
    `;
    
    const searchBox = `
        <div class="mb-2">
            <input type="text" class="form-control form-control-sm" id="hwidSearchFilter" 
                   placeholder="Filter results..." onkeyup="filterTable('hwidSearchTable', this.value)">
        </div>
    `;
    
    let html = `${exportButtons}${searchBox}`;
    html += `<div class="alert alert-success">
        Found <strong style="color: var(--text-primary);">${data.total_unique_hwids.toLocaleString()}</strong> unique HWID(s)
        ${data.hwid_pattern ? ` matching pattern "${highlightText(data.hwid_pattern)}"` : ''}
        ${dateRange ? `<br><small style="color: var(--text-muted);">Period: ${dateRange}</small>` : ''}
    </div>`;
    
    html += '<div class="table-responsive"><table class="table table-sm table-bordered table-hover" id="hwidSearchTable">';
    html += '<thead><tr><th>#</th><th>HWID</th><th>Alert Count</th><th>Occurrence Count</th><th>First Seen</th><th>Last Seen</th><th>Actions</th></tr></thead><tbody>';
    
    data.hwids.forEach((item, index) => {
        const highlightedHwid = data.hwid_pattern ? highlightText(item.hwid, data.hwid_pattern) : item.hwid;
        html += `<tr data-index="${index}">
            <td style="color: var(--text-secondary);">${index + 1}</td>
            <td><code style="color: var(--primary); font-size: 0.9rem;">${highlightedHwid}</code></td>
            <td><span class="badge bg-info">${item.alert_count.toLocaleString()}</span></td>
            <td><span class="badge bg-secondary">${item.occurrence_count.toLocaleString()}</span></td>
            <td style="color: var(--text-secondary); font-size: 0.85rem;">${item.first_seen ? formatDateUTC(item.first_seen) : 'N/A'}</td>
            <td style="color: var(--text-secondary); font-size: 0.85rem;">${item.last_seen ? formatDateUTC(item.last_seen) : 'N/A'}</td>
            <td>
                <button class="btn btn-sm btn-outline-primary" onclick="viewHWIDDetails(${index})" title="View Details">
                    <i class="bi bi-eye"></i>
                </button>
            </td>
        </tr>`;
    });
    
    html += '</tbody></table></div>';
    resultsDiv.innerHTML = html;
    
    // Store data for detail view
    window.hwidSearchData = data;
}

function exportJSON() {
    const btn = event?.target?.closest('button') || document.querySelector('button[onclick="exportJSON()"]');
    const originalHTML = btn?.innerHTML;
    
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Exporting...';
    }
    
    fetch(buildApiUrl('/api/stats'))
        .then(response => {
            if (!response.ok) {
                return response.text().then(text => {
                    throw new Error(`HTTP ${response.status}: ${text.substring(0, 100)}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (!data.success) {
                throw new Error(data.error || 'Failed to fetch data');
            }
            
            // Enhance JSON with metadata
            const exportData = {
                report_metadata: {
                    company: 'Breachunt',
                    generated_at: new Date().toISOString(),
                    period: document.getElementById('periodSelect').value,
                    start_date: data.start_date,
                    end_date: data.end_date,
                    version: '1.0'
                },
                statistics: data.stats,
                chart_data: null  // Can be added if needed
            };
            
            const jsonStr = JSON.stringify(exportData, null, 2);
            const blob = new Blob([jsonStr], { type: 'application/json;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            const period = document.getElementById('periodSelect').value;
            const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
            a.download = `mongodb_report_${period}_${timestamp}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalHTML || '<i class="bi bi-filetype-json"></i> JSON';
            }
        })
        .catch(error => {
            console.error('Export error:', error);
            alert('Failed to export JSON: ' + (error.message || 'Unknown error'));
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalHTML || '<i class="bi bi-filetype-json"></i> JSON';
            }
        });
}

// Helper functions for enhanced report/search features

function highlightText(text, query) {
    if (!query || !text) return String(text || '');
    const textStr = String(text);
    const queryStr = String(query);
    if (!queryStr) return textStr;
    
    // Escape special regex characters
    const escapedQuery = queryStr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(${escapedQuery})`, 'gi');
    // Use cyan/primary color with white text for better contrast on dark theme
    return textStr.replace(regex, '<mark style="background-color: #06b6d4; color: #ffffff; padding: 2px 4px; border-radius: 3px; font-weight: 500;">$1</mark>');
}

function filterTable(tableId, searchText) {
    const table = document.getElementById(tableId);
    if (!table) return;
    
    const rows = table.querySelectorAll('tbody tr');
    const searchLower = searchText.toLowerCase();
    
    rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        if (text.includes(searchLower)) {
            row.style.display = '';
        } else {
            row.style.display = 'none';
        }
    });
}

function exportCurrentData(format) {
    if (!currentSearchData && !currentReportData) {
        alert('No data to export. Please run a search or report first.');
        return;
    }
    
    const data = currentSearchData || currentReportData;
    const type = currentSearchType || 'report';
    
    if (format === 'json') {
        const jsonStr = JSON.stringify(data, null, 2);
        const blob = new Blob([jsonStr], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        
        let downloadName = `${type}_${new Date().toISOString().split('T')[0]}.json`;
        if (type === 'report' && data.period) {
            const startDateStr = data.start ? data.start.split('T')[0] : new Date().toISOString().split('T')[0];
            if (data.period === 'daily') {
                downloadName = `Daily_report_${startDateStr}.json`;
            } else if (data.period === 'weekly') {
                downloadName = `Weekly_report_${startDateStr}.json`;
            }
        }
        link.download = downloadName;
        link.click();
        URL.revokeObjectURL(url);
    } else if (format === 'csv') {
        let csv = '';
        if (type === 'report') {
            csv = 'Metric,Count\n';
            csv += `Zip Archives Imported,${data.stats?.zip_import || 0}\n`;
            csv += `Decompressed Archives,${data.stats?.decompressed || 0}\n`;
            csv += `Credentials Found,${data.stats?.credentials || 0}\n`;
            csv += `HWID Found,${data.stats?.hwid || 0}\n`;
            csv += `Total Organizations,${data.stats?.total_organizations || 0}\n`;
            csv += `Total Domains,${data.stats?.total_domains || 0}\n`;
            csv += `Unique Domains,${data.stats?.unique_domains || 0}\n`;
        } else if (type === 'domain_search' && data.results) {
            csv = 'Organization Index,Display Name,Matching Domains,Total Domains\n';
            data.results.forEach(org => {
                csv += `"${org.organization_index}","${org.display_name}","${org.matching_domains.join('; ')}",${org.total_domains}\n`;
            });
        } else if (type === 'org_search' && data.results) {
            csv = 'Organization Index,Display Name,Domains,Total Domains\n';
            data.results.forEach(org => {
                csv += `"${org.organization_index}","${org.display_name}","${org.domains?.join('; ') || ''}",${org.total_domains}\n`;
            });
        } else if (type === 'hwid_search' && data.hwids) {
            csv = 'HWID,Alert Count,Occurrence Count,First Seen,Last Seen\n';
            data.hwids.forEach(item => {
                csv += `"${item.hwid}",${item.alert_count},${item.occurrence_count},"${item.first_seen || 'N/A'}","${item.last_seen || 'N/A'}"\n`;
            });
        } else if (type === 'alerts_domain_search' && data.results) {
            csv = 'Alert ID,Matching Domains,All Domains,Organization ID,Type,Created Date\n';
            data.results.forEach(alert => {
                csv += `"${alert.alert_id}","${alert.matching_domains.join('; ')}","${alert.all_domains.join('; ')}","${alert.organization_id || 'N/A'}","${alert.type || 'N/A'}","${alert.created_date || 'N/A'}"\n`;
            });
        }
        
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        
        let downloadName = `${type}_${new Date().toISOString().split('T')[0]}.csv`;
        if (type === 'report' && data.period) {
            const startDateStr = data.start ? data.start.split('T')[0] : new Date().toISOString().split('T')[0];
            if (data.period === 'daily') {
                downloadName = `Daily_report_${startDateStr}.csv`;
            } else if (data.period === 'weekly') {
                downloadName = `Weekly_report_${startDateStr}.csv`;
            }
        }
        link.download = downloadName;
        link.click();
        URL.revokeObjectURL(url);
    } else if (format === 'pdf') {
        // Show loading indicator
        const loadingToast = document.createElement('div');
        loadingToast.className = 'alert alert-info position-fixed top-0 end-0 m-3';
        loadingToast.style.zIndex = '9999';
        loadingToast.innerHTML = '<i class="bi bi-hourglass-split"></i> Đang tạo PDF...';
        document.body.appendChild(loadingToast);
        
        // For PDF, send data to backend endpoint
        const exportData = {
            type: type,
            data: data,
            title: type === 'report' ? 'Statistics Report' : `${type.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())} Results`
        };
        
        fetch(buildApiUrl('/api/export-pdf-search'), {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(exportData)
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => { throw new Error(err.error || 'Export failed'); });
            }
            return response.blob();
        })
        .then(blob => {
            loadingToast.remove();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            
            let downloadName = `${type}_${new Date().toISOString().split('T')[0]}.pdf`;
            if (type === 'report' && data.period) {
                const startDateStr = data.start ? data.start.split('T')[0] : new Date().toISOString().split('T')[0];
                if (data.period === 'daily') {
                    downloadName = `Daily_report_${startDateStr}.pdf`;
                } else if (data.period === 'weekly') {
                    downloadName = `Weekly_report_${startDateStr}.pdf`;
                }
            }
            link.download = downloadName;
            link.click();
            URL.revokeObjectURL(url);
            
            // Show success message
            const successToast = document.createElement('div');
            successToast.className = 'alert alert-success position-fixed top-0 end-0 m-3';
            successToast.style.zIndex = '9999';
            successToast.innerHTML = '<i class="bi bi-check-circle"></i> PDF đã được tạo thành công!';
            document.body.appendChild(successToast);
            setTimeout(() => successToast.remove(), 3000);
        })
        .catch(error => {
            loadingToast.remove();
            console.error('PDF export error:', error);
            
            // Show error message
            const errorToast = document.createElement('div');
            errorToast.className = 'alert alert-danger position-fixed top-0 end-0 m-3';
            errorToast.style.zIndex = '9999';
            errorToast.innerHTML = `<i class="bi bi-exclamation-triangle"></i> Lỗi: ${error.message}`;
            document.body.appendChild(errorToast);
            setTimeout(() => errorToast.remove(), 5000);
        });
    }
}

function copyCurrentData() {
    if (!currentSearchData && !currentReportData) {
        alert('No data to copy. Please run a search or report first.');
        return;
    }
    
    const data = currentSearchData || currentReportData;
    const jsonStr = JSON.stringify(data, null, 2);
    
    navigator.clipboard.writeText(jsonStr).then(() => {
        // Show toast notification
        const toast = document.createElement('div');
        toast.className = 'alert alert-success position-fixed top-0 end-0 m-3';
        toast.style.zIndex = '9999';
        toast.innerHTML = '<i class="bi bi-check-circle"></i> Data copied to clipboard!';
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 2000);
    }).catch(err => {
        alert('Failed to copy to clipboard: ' + err.message);
    });
}

function printCurrentData() {
    const printWindow = window.open('', '_blank');
    if (!printWindow) {
        alert('Please allow popups to print');
        return;
    }
    
    let content = '<html><head><title>Report</title><style>body{font-family:Arial,sans-serif;padding:20px;}table{border-collapse:collapse;width:100%;}th,td{border:1px solid #ddd;padding:8px;text-align:left;}th{background-color:#f2f2f2;}</style></head><body>';
    
    if (currentReportData) {
        content += `<h2>${currentSearchType || 'Report'}</h2>`;
        content += '<table><tr><th>Metric</th><th>Count</th></tr>';
        const stats = currentReportData.stats || {};
        content += `<tr><td>Zip Archives Imported</td><td>${(stats.zip_import || 0).toLocaleString()}</td></tr>`;
        content += `<tr><td>Decompressed Archives</td><td>${(stats.decompressed || 0).toLocaleString()}</td></tr>`;
        content += `<tr><td>Credentials Found</td><td>${(stats.credentials || 0).toLocaleString()}</td></tr>`;
        content += `<tr><td>HWID Found</td><td>${(stats.hwid || 0).toLocaleString()}</td></tr>`;
        content += `<tr><td>Total Organizations</td><td>${(stats.total_organizations || 0).toLocaleString()}</td></tr>`;
        content += `<tr><td>Total Domains</td><td>${(stats.total_domains || 0).toLocaleString()}</td></tr>`;
        content += `<tr><td>Unique Domains</td><td>${(stats.unique_domains || 0).toLocaleString()}</td></tr>`;
        content += '</table>';
    }
    
    content += '</body></html>';
    printWindow.document.write(content);
    printWindow.document.close();
    printWindow.print();
}

function viewDomainDetails(index) {
    if (!window.domainSearchData || !window.domainSearchData.results[index]) {
        alert('Data not available');
        return;
    }
    
    const org = window.domainSearchData.results[index];
    const content = `
        <div class="mb-3">
            <h6>Organization Details</h6>
            <table class="table table-sm table-bordered">
                <tr><th>Organization Index</th><td><code>${org.organization_index}</code></td></tr>
                <tr><th>Display Name</th><td>${org.display_name}</td></tr>
                <tr><th>Total Domains</th><td>${org.total_domains}</td></tr>
                <tr><th>Matching Domains</th><td>${org.matching_count}</td></tr>
                <tr><th>Created At</th><td>${org.created_at ? formatDateUTC(org.created_at) : 'N/A'}</td></tr>
                <tr><th>Updated At</th><td>${org.updated_at ? formatDateUTC(org.updated_at) : 'N/A'}</td></tr>
            </table>
        </div>
        <div class="mb-3">
            <h6>Matching Domains</h6>
            <div>${org.matching_domains.map(d => `<span class="badge bg-primary me-1">${d}</span>`).join('')}</div>
        </div>
        <div class="mb-3">
            <h6>All Domains</h6>
            <div>${org.all_domains.map(d => `<span class="badge bg-secondary me-1">${d}</span>`).join('')}</div>
        </div>
    `;
    
    const modal = createModal('Domain Search Details', content);
    document.body.appendChild(modal);
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
    modal.addEventListener('hidden.bs.modal', () => modal.remove());
}

function viewOrgDetails(index) {
    if (!window.orgSearchData || !window.orgSearchData.results[index]) {
        alert('Data not available');
        return;
    }
    
    const org = window.orgSearchData.results[index];
    const content = `
        <div class="mb-3">
            <h6>Organization Details</h6>
            <table class="table table-sm table-bordered">
                <tr><th>Organization Index</th><td><code>${org.organization_index}</code></td></tr>
                <tr><th>Display Name</th><td>${org.display_name}</td></tr>
                <tr><th>Total Domains</th><td>${org.total_domains}</td></tr>
                <tr><th>Created At</th><td>${org.created_at ? formatDateUTC(org.created_at) : 'N/A'}</td></tr>
                <tr><th>Updated At</th><td>${org.updated_at ? formatDateUTC(org.updated_at) : 'N/A'}</td></tr>
            </table>
        </div>
        <div class="mb-3">
            <h6>Domains</h6>
            <div>${org.domains && org.domains.length > 0 ? org.domains.map(d => `<span class="badge bg-secondary me-1">${d}</span>`).join('') : 'No domains'}</div>
        </div>
    `;
    
    const modal = createModal('Organization Details', content);
    document.body.appendChild(modal);
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
    modal.addEventListener('hidden.bs.modal', () => modal.remove());
}

function viewHWIDDetails(index) {
    if (!window.hwidSearchData || !window.hwidSearchData.hwids[index]) {
        alert('Data not available');
        return;
    }
    
    const item = window.hwidSearchData.hwids[index];
    const content = `
        <div class="mb-3">
            <h6>HWID Details</h6>
            <table class="table table-sm table-bordered">
                <tr><th>HWID</th><td><code>${item.hwid}</code></td></tr>
                <tr><th>Alert Count</th><td><span class="badge bg-info">${item.alert_count.toLocaleString()}</span></td></tr>
                <tr><th>Occurrence Count</th><td><span class="badge bg-secondary">${item.occurrence_count.toLocaleString()}</span></td></tr>
                <tr><th>First Seen</th><td>${item.first_seen ? formatDateUTC(item.first_seen) : 'N/A'}</td></tr>
                <tr><th>Last Seen</th><td>${item.last_seen ? formatDateUTC(item.last_seen) : 'N/A'}</td></tr>
            </table>
        </div>
    `;
    
    const modal = createModal('HWID Details', content);
    document.body.appendChild(modal);
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
    modal.addEventListener('hidden.bs.modal', () => modal.remove());
}

function viewAlertDetails(index) {
    if (!window.alertsDomainSearchData || !window.alertsDomainSearchData.results[index]) {
        alert('Data not available');
        return;
    }
    
    const alert = window.alertsDomainSearchData.results[index];
    const content = `
        <div class="mb-3">
            <h6>Alert Details</h6>
            <table class="table table-sm table-bordered">
                <tr><th>Alert ID</th><td><code>${alert.alert_id}</code></td></tr>
                <tr><th>Organization ID</th><td>${alert.organization_id || 'N/A'}</td></tr>
                <tr><th>Type</th><td><span class="badge bg-info">${alert.type || 'N/A'}</span></td></tr>
                <tr><th>Created Date</th><td>${alert.created_date ? formatDateUTC(alert.created_date) : 'N/A'}</td></tr>
            </table>
        </div>
        <div class="mb-3">
            <h6>Matching Domains</h6>
            <div>${alert.matching_domains.map(d => `<span class="badge bg-success me-1">${d}</span>`).join('')}</div>
        </div>
        <div class="mb-3">
            <h6>All Domains</h6>
            <div>${alert.all_domains.map(d => `<span class="badge bg-secondary me-1">${d}</span>`).join('')}</div>
        </div>
    `;
    
    const modal = createModal('Alert Details', content);
    document.body.appendChild(modal);
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
    modal.addEventListener('hidden.bs.modal', () => modal.remove());
}

